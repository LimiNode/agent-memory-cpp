# Packed INT8 MDBX prototype (2026-10-03)

This is the first post-merge persistent-layout prototype.  It intentionally
measures physical read amplification rather than claiming a production ANN
path: candidates are the exact FP32 top-128 for each of the 305 fresh queries,
and the payload is a deterministic per-document INT8 proxy.  The fixture
generator is `tools/agent-memory-bench/prepare-mdbx-prototype-fixture.py` and
the native runner is `tools/agent-memory-bench/native-thq-mdbx-serving.cpp`.

## Results

The runner uses one warm-up read followed by five measured repeats per query,
exact reconstructed-cosine scoring, and ordered parity against an independent
NumPy fixture generator.

| layout | physical bytes | materialize ms | durable commits | reopen ms | p50 / p95 / p99 ms | read p50 ms | score p50 ms | parity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| row KV | 536,870,912 | 257,953.302 | 1 | 1.162 | 0.2166 / 0.6259 / 0.8796 | 0.1525 | 0.0580 | 305/305 |
| segment blob (4096) | 402,653,184 | 1,482.760 | 245 | 202.457 | 138.6463 / 162.5034 / 172.5282 | 138.5764 | 0.0623 | 305/305 |

The compact receipt is `2026-10-03-mdbx-packed-int8-prototype.result.json`.
Raw fixture binaries, MDBX files, and JSONL samples remain external/local and
are bound by the fixture and runner SHA-256 values in that receipt.

## Interpretation

The row layout is roughly 643x faster at this sparse 128-row read workload,
while the segment layout is 25% smaller on disk and over 170x faster to build
because it amortizes writes.  Segment blobs are therefore a plausible storage
candidate only when larger read amplification is acceptable or reads are
batched.  These values must not be compared directly with the three-mode ANN
latencies: no learned route, THQ scan, or finalist codec payload is included.

## Follow-up

Add cold-cache/reopen repetitions, concurrent readers plus generation
publication, interrupted rebuild/recovery, and finalist-specific packed
payloads before using MDBX in product selection.
