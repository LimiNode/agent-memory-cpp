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
`2fadf4631b2eb84ec55b2df5ca912e5c7826ec5a`. Each case uses a fresh
temporary MDBX environment. `import_elapsed_ms` includes importer open,
publication, and close. `index phase ms` covers the separate read-only
`scan_by_scope_and_kind` operation and, for v2, the repeated bounded synthetic
admission probes after the importer has closed. The probes are a post-import
control that demonstrates bounded cursor behavior; they do not directly count
production `ensure_index_admission()` executions. The unit counts are logical
records returned by that scan. `db_bytes` is the final MDBX file-size snapshot,
not a count of bytes written during the run.

## Matched results

| body | chunks / scanned units | baseline import ms | v2 import ms | baseline index phase ms | v2 index phase ms | baseline synthetic probes | v2 synthetic probes | baseline db bytes | v2 db bytes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 128 KiB | 128 / 128 | 486.449 | 496.921 | 30.2656 | 75.2107 | 0 | 128 | 16,777,216 | 16,777,216 |
| 1 MiB | 1,024 / 1,024 | 6,924.54 | 6,945.66 | 30.7757 | 27.4143 | 0 | 1,024 | 16,777,216 | 16,777,216 |
| 4 MiB | 4,096 / 4,096 | 45,331.7 | 32,753.5 | 37.0173 | 35.3794 | 0 | 4,096 | 33,554,432 | 33,554,432 |

The v2 importer is faster for the 4 MiB case in this run because it avoids
rewriting the growing serialized list. The 128 KiB and 1 MiB differences are
within the noise of this single Windows run; this receipt is not a claim of a
fixed speedup. The index phase remains a linear scan for result materialization.
The synthetic admission probe counter demonstrates the corrected existence
check: v2 examines one first entry per probe (`N` total for `N` sequential
inserts), not the accumulated range. It is not direct telemetry from the
production admission calls. Because these synthetic probes are included only
for v2, the reported index-phase timings are not a symmetric A/B comparison.
The identical file-size snapshots are MDBX allocation-granularity observations,
not evidence that the two layouts perform identical physical writes.

## Logical index-write accounting

The baseline rewrites the serialized `UnitId[]` value once per newly published
Chunk. Its logical rewrite count is therefore 128, 1,024, and 4,096 for the
three cases (amplification `N`). v2 writes one bounded append-only index entry
per Chunk, so its logical entry count is the same 128, 1,024, and 4,096 while
its rewrite amplification is `1`. `synthetic_admission_probe_records` is a
post-import read-path control, not a write counter or direct production
telemetry. No OS-level write-trace or MDBX
page-dirty accounting was collected, so none of these values may be reported
as physical bytes written.

## Reproduction

The baseline source does not contain the benchmark output fields, so the
baseline run uses the same temporary benchmark-only instrumentation as the v2
test. Apply the committed test-file delta before configuring the baseline
worktree:

```text
git diff 9d151088e788e39474c8d8e80689f24c06ebe7c4 2fadf4631b2eb84ec55b2df5ca912e5c7826ec5a -- tests/infrastructure/mdbx/mdbx_resource_importer_test.cpp | git -C E:/_repoz/agent-memory-cpp/.worktrees/m0-index-baseline apply -
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
