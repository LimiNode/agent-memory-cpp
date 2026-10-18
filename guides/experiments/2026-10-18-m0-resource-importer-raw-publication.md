# M0 raw Chunk publication benchmark

This is a controlled implementation measurement for the
`LexicalWithRawChunks` profile, not a production capacity claim. Both commits
were built with the same Release MinGW configuration (`AGENT_MEMORY_BUILD_TESTS=ON`,
`AGENT_MEMORY_ENABLE_MDBX=ON`, `AGENT_MEMORY_ENABLE_JSON=ON`,
`AGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED`) and run with:

```text
agent_memory_mdbx_resource_importer_test.exe --benchmark
```

The baseline is commit `9d151088e788e39474c8d8e80689f24c06ebe7c4` and the
append-only implementation is corrective-pass HEAD
`edcd249e234a461163cd02e04765aa18a6ba96a8`. Each case uses a fresh
temporary MDBX environment. `import_elapsed_ms` includes importer open,
publication, and close. `index_scan_elapsed_ms` is a separate read-only
`scan_by_scope_and_kind` operation after the importer has closed. The unit
counts are logical records returned by that scan. `db_bytes` is the final MDBX
file-size snapshot, not a count of bytes written during the run.

## Matched results

| body | chunks / scanned units | baseline import ms | v2 import ms | baseline index scan ms | v2 index scan ms | baseline db bytes | v2 db bytes |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 128 KiB | 128 / 128 | 465.243 | 197.12 | 14.2659 | 14.0241 | 16,777,216 | 16,777,216 |
| 1 MiB | 1,024 / 1,024 | 6,896.47 | 2,950.08 | 14.5536 | 14.0337 | 16,777,216 | 16,777,216 |
| 4 MiB | 4,096 / 4,096 | 46,827.3 | 26,166.5 | 16.4375 | 22.2090 | 33,554,432 | 33,554,432 |

The v2 importer is faster in this run because it avoids rewriting the growing
serialized list. Scan latency is comparable at these sizes; it is still a
linear search over the group's entries. The identical file-size snapshots are
MDBX allocation granularity observations, not evidence that the two layouts
perform identical physical writes.

## Logical index-write accounting

The baseline rewrites the serialized `UnitId[]` value once per newly published
Chunk. Its logical rewrite count is therefore 128, 1,024, and 4,096 for the
three cases (amplification `N`). v2 writes one bounded append-only index entry
per Chunk, so its logical entry count is the same 128, 1,024, and 4,096 while
its rewrite amplification is `1`. These are logical counters; no OS-level
write-trace or MDBX page-dirty accounting was collected, so they must not be
reported as physical bytes written.

## Reproduction

The baseline source does not contain the benchmark output fields, so the
baseline run uses the same temporary benchmark-only instrumentation as the v2
test. Apply the committed test-file delta before configuring the baseline
worktree:

```text
git diff 9d151088e788e39474c8d8e80689f24c06ebe7c4 edcd249e234a461163cd02e04765aa18a6ba96a8 -- tests/infrastructure/mdbx/mdbx_resource_importer_test.cpp | git -C E:/_repoz/agent-memory-cpp/.worktrees/m0-index-baseline apply -
cmake -S . -B .build/mdbx-baseline -G Ninja -DCMAKE_BUILD_TYPE=Release -DAGENT_MEMORY_BUILD_TESTS=ON -DAGENT_MEMORY_ENABLE_MDBX=ON -DAGENT_MEMORY_ENABLE_JSON=ON -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED
cmake --build .build/mdbx-baseline --target agent_memory_mdbx_resource_importer_test -j 4
.build/mdbx-baseline/bin/agent_memory_mdbx_resource_importer_test.exe --benchmark
```

From the corrective-pass worktree:

```text
cmake --build .build/mdbx-performance --target agent_memory_mdbx_resource_importer_test -j 4
.build/mdbx-performance/bin/agent_memory_mdbx_resource_importer_test.exe --benchmark
```

The v2 index keeps the existing DBI and key ownership. It does not add a DBI
for deletion: erase physically removes one append-only entry, but locating
that entry remains O(N) within the scope/kind group. A separate follow-up
should evaluate a reclaim-oriented projection or auxiliary lookup for bulk
erase; this PR deliberately does not introduce one.
