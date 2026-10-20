# M0 importer bounded raw-unit batching

Date: 2026-10-20
PR: importer integration/performance follow-up after merged PR #526
Commit context: `1e221eed` plus the batching integration branch

## Question

Does wiring the bounded `MdbxKnowledgeUnitStore::create_or_get_units_guarded`
API into `MdbxResourceImporter` reduce raw-unit publication overhead without
changing revision-bound identity, manifest publication, or fencing behavior?

## Setup and command

The harness uses a fresh MDBX environment for every cell, the same generated
body, the same Release MinGW toolchain, and raw chunks of 1024 bytes. It varies
only the importer batch size (`1`, `16`, `64`, `128`) and runs 128 KiB, 1 MiB,
and 4 MiB bodies.

```text
cmake -S . -B .build/m0-importer-batching-mingw -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=g++ \
  -DCMAKE_C_COMPILER=gcc -DAGENT_MEMORY_ENABLE_MDBX=ON \
  -DAGENT_MEMORY_BUILD_TESTS=ON -DAGENT_MEMORY_BUILD_BENCHMARKS=OFF \
  -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED
cmake --build .build/m0-importer-batching-mingw \
  --target agent_memory_mdbx_resource_importer_test -j 4
./.build/m0-importer-batching-mingw/bin/agent_memory_mdbx_resource_importer_test.exe \
  --batch-benchmark
```

`chunks` is the logical number of published raw units. `raw_store_calls` is
the number of guarded store calls (therefore, the number of durable batches).
`db_bytes` is the resulting MDBX file size, not the sum of logical payload
bytes.

## Results

Single local Release run, fresh environments:

| Body | Batch | Chunks | Import ms | Store calls | MDBX bytes |
|---:|---:|---:|---:|---:|---:|
| 128 KiB | 1 | 128 | 200.36 | 128 | 16,777,216 |
| 128 KiB | 16 | 128 | 85.09 | 8 | 16,777,216 |
| 128 KiB | 64 | 128 | 99.16 | 2 | 16,777,216 |
| 128 KiB | 128 | 128 | 104.68 | 1 | 16,777,216 |
| 1 MiB | 1 | 1,024 | 1,626.91 | 1,024 | 16,777,216 |
| 1 MiB | 16 | 1,024 | 523.81 | 64 | 16,777,216 |
| 1 MiB | 64 | 1,024 | 421.62 | 16 | 16,777,216 |
| 1 MiB | 128 | 1,024 | 423.82 | 8 | 16,777,216 |
| 4 MiB | 1 | 4,096 | 6,576.28 | 4,096 | 33,554,432 |
| 4 MiB | 16 | 4,096 | 2,190.58 | 256 | 33,554,432 |
| 4 MiB | 64 | 4,096 | 1,704.89 | 64 | 33,554,432 |
| 4 MiB | 128 | 4,096 | 1,568.36 | 32 | 33,554,432 |

## Interpretation

The importer now uses the guarded batch API. The default batch size is 128,
and every batch checks the durable fencing token before and after its writable
transaction. A fault after raw publication represents interruption after a
whole committed batch; recovery replays that batch by content-key dedupe and
does not duplicate IDs.

The measured run shows the expected reduction in store calls and a directional
speedup: approximately 1.9x at 128 KiB, 3.8x at 1 MiB, and 4.2x at 4 MiB for
batch 128 versus batch 1. MDBX file size is unchanged within each body-size
row, so the result is a transaction-overhead improvement rather than a claim
of reduced logical data volume.

## Limitations and follow-up

These are single-run local measurements, not a statistically stable release
benchmark. They use a synthetic repeated-byte body and do not measure lease
renewal under a transaction near the 30-second operational budget. Follow-up
work should repeat the matrix on representative corpora and test interruption
between batches, takeover, repeat import, duplicate IDs, and manifest retention
for larger documents before changing the default batch size.
