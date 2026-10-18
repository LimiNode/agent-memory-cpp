# M0 importer performance root-cause investigation

This note records an instrumentation-only investigation based on PR #521
(`df6987f16e8fc9f135da9ae95b5f02530f900d16`). It does not change the ContentHash
recipe, unit IDs, migration contract, publication fencing, or production MDBX
durability policy. The diagnostic counters are optional and inactive when the
caller does not provide `MdbxResourceImporterOptions::diagnostics`.

## Reproduction

Configure the Release MinGW MDBX test target:

```text
cmake -S . -B .build/mdbx-root-cause -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_BUILD_TESTS=ON -DAGENT_MEMORY_ENABLE_MDBX=ON \
  -DAGENT_MEMORY_ENABLE_JSON=ON -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED
cmake --build .build/mdbx-root-cause --target agent_memory_mdbx_resource_importer_test -j 4
```

The principal diagnostic commands are:

```text
.build/mdbx-root-cause/bin/agent_memory_mdbx_resource_importer_test.exe --benchmark
.build/mdbx-root-cause/bin/agent_memory_mdbx_resource_importer_test.exe --fencing-benchmark 4194304 8192
.build/mdbx-root-cause/bin/agent_memory_mdbx_resource_importer_test.exe --transaction-benchmark 1024
.build/mdbx-root-cause/bin/agent_memory_mdbx_resource_importer_test.exe --corpus-benchmark 1048576 english
.build/mdbx-root-cause/bin/agent_memory_mdbx_resource_importer_test.exe --corpus-benchmark 1048576 russian
.build/mdbx-root-cause/bin/agent_memory_mdbx_resource_importer_test.exe --corpus-benchmark 1048576 technical
```

The `--benchmark` path was repeated seven times for both the baseline binary
from `9d151088e788e39474c8d8e80689f24c06ebe7c4` and the candidate binary. Each
case uses a fresh MDBX environment. Timings are wall-clock milliseconds on the
same Windows/MinGW Release host; they are not a cross-machine capacity claim.

## Matched importer timings

The table reports medians of seven runs. The baseline executable has the
original serialized-list index; the candidate has append-only v2.

| body | chunks | baseline import ms | candidate import ms | candidate ownership checks | candidate logical intent bytes | candidate fence ms | candidate raw-store ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 128 KiB | 128 | 183.134 | 185.421 | 258 | 33,872,304 | 16.6 | 118.8 |
| 1 MiB | 1,024 | 2,709.14 | 2,707.09 | 2,050 | 2,150,021,550 | 754.8 | 1,380.3 |
| 4 MiB | 4,096 | 25,460.3 | 24,550.9 | 8,194 | 34,369,888,686 | 10,776.9 | 6,340.0 |

The candidate speed ratios are approximately `0.99x`, `1.00x`, and `1.04x`.
The earlier single-run 1.8–2.4x result is not reproducible under this matched
seven-run protocol. The index optimization remains structurally valid, but it is
not the dominant end-to-end cost on this host.

`ownership_payload_bytes` is a logical sum of deserialized intent values, not
physical disk I/O. At 4 MiB, 8,194 checks materialize about 34.4 GiB of intent
payload. The direct fencing control (`--fencing-benchmark 4194304 8192`) measured
12.284 s for full-intent `find()` calls and 9.012 ms for equal-count reads of a
32-byte token. This confirms H1 as an algorithmic cost; it does not authorize
replacing the durable guard with a local or end-only check.

The diagnostic phase counters show that candidate 4 MiB time is split mainly
between fencing and raw-unit storage. Lexical work is roughly 0.3–0.4 s and
manifest publication is roughly 0.05 s in the representative runs. Constructor
open/recovery is about 15 ms and close is below 6 ms, so neither explains the
superlinear growth.

## Transaction control (H2)

`--transaction-benchmark 1024` writes 1 KiB values to an isolated diagnostic
DBI. It is deliberately not a KnowledgeUnit publication substitute. One run
produced:

| sync_durable | batch | commits | elapsed us |
|---|---:|---:|---:|
| true | 1 | 1,024 | 732,126 |
| true | 16 | 64 | 395,236 |
| true | 64 | 16 | 367,140 |
| true | 128 | 8 | 354,303 |
| false | 1 | 1,024 | 690,875 |
| false | 16 | 64 | 427,193 |
| false | 64 | 16 | 375,385 |
| false | 128 | 8 | 365,371 |

This confirms that transaction count is material in a controlled write loop,
but it does not measure a safe batched `KnowledgeUnitStore` publication. The
production configuration remains durable (`sync_durable=true`); relaxed mode is
diagnostic only. A future batching PR must preserve per-manifest visibility,
takeover fencing, crash recovery, and idempotent IDs.

## Corpus control

The original `bbbb...` body is retained as a pathological long-token stress
case. At 1 MiB, one-run candidate timings were approximately 6.22 s for the
English sentence corpus, 2.86 s for Russian UTF-8 text, and 4.20 s for the
technical identifier corpus, compared with about 2.71 s for the pathological
case in the repeated matched run. These are directional controls, not a
statistical corpus study; they demonstrate that tokenizer/corpus shape is an
independent variable and must not be conflated with MDBX index performance.

## Conclusions and sequencing

* H1 is confirmed by direct counters and an isolated read benchmark. The next
  design proposal should use a compact durable fencing token (for example a
  digest/epoch token) checked inside the existing guarded transactions, with
  atomic takeover, crash recovery, and an explicit wire-format compatibility
  story. No such replacement is implemented here.
* H2 is supported by the synthetic transaction control and by the per-unit
  `raw_unit_store_ns` counter, but a production-safe bounded batch has not yet
  been designed. It is a separate performance PR.
* The lexical corpus materially changes timings. Keep both representative text
  and long-token stress inputs in future receipts.
* The 30-second ownership lease is close to the observed 4 MiB runtime. Lease
  renewal and mid-import takeover tests are required before enabling any batch
  publication; increasing the lease blindly is not a performance fix.

No production refactor or merge is proposed by this investigation. The next
PRs should be, in order: compact-token design and negative fencing tests,
bounded transactional batching with crash/recovery tests, then larger-corpus
and lease-load measurements.
