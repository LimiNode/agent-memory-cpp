# Native production-kernel control (2026-09-30)

## Purpose

The earlier full-corpus run is retained as a scalar correctness/reference
baseline.  This note records the first bounded production-shaped control after
normalising the hot path.  It is an implementation check, not a codec ranking
or a production selection.

## Protocol

`agent-memory-native-full-corpus-codec-benchmark --production-control` uses the
same frozen 1M THQ4/INT8 materialisation and 152-query fixture as the scalar
control, but changes the measured route as follows:

- THQ top-128 is a bounded fixed-capacity array heap; the timed path does not
  materialise one million `Candidate` objects, allocate a heap buffer, or call
  `partial_sort` over the full corpus;
- direct and cascade arms both return an ordered top-10 result;
- page proxies are computed after timing from the retained top-128 IDs;
- one query LUT is prepared before the timed arms;
- two warmups and ten timed repetitions are run per query, with the two arms
  randomised per query from a fixed seed;
- the INT8 scorer uses the exact float-query AVX2 dot path when the target is
  configured with `AGENT_MEMORY_NEUROUTE_ENABLE_AVX2`; otherwise it falls back
  to the scalar implementation;
- timing is reported as mean, p50, p95, and p99 over per-query samples.

The current run was compiled by the repository CMake Release target with AVX2
enabled (`AGENT_MEMORY_NEUROUTE_ENABLE_AVX2=ON`) on an
Intel Xeon E5-2696 v3 (18 physical / 36 logical cores).  Thread count,
affinity, NUMA placement, power policy, and OS cold-fault behaviour were not
controlled, so this remains a single-host bounded control.

## Result

| Arm | Mean ms/query | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| Direct INT8 top-10 | 235.907 | 234.046 | 250.231 | 272.490 |
| THQ bounded top-128 → INT8 top-10 | 92.926 | 92.012 | 98.252 | 107.050 |

The output contract is now identical (`top10` ordered by descending score,
then ascending document ID).  The run emitted 152 rows, each with 128 THQ
candidate IDs and page proxies.  The median/p95/p99 rerank payload page proxy
was 125/133/136; the THQ scan proxy was 23,438 at every query.  These are
namespace-local byte-layout counters, not OS page-fault measurements.
The bounded THQ selector reproduced the scalar control's ordered top-128 IDs
for all 152 queries.  Direct and cascade top-1 also reproduced their scalar
control rows, and the production-shaped direct/cascade ordered top-10 lists
matched on all 152 queries for this INT8 control.

## Evidence binding

- source: `tools/agent-memory-bench/native-full-corpus-codec-benchmark.cpp`
- source SHA-256: `86383a4e7d53150d5616e4aab2761e298be26d0c3fa497d5489cd8cb0904a66c`
- runner binary SHA-256: `4d8779ed63c44c2ee607f1a655f57b66a5ee98328b2ebc5d8802fd25d7f17268`
- query fixture SHA-256: `abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191`
- raw JSONL SHA-256: `fd585b3edebc3e2f3f19c786c0c14184eb50d66784064aefdc3b63d0aa85b9c9`
- raw JSONL path: external `build-fidelity/production-152-fixedheap.jsonl`

## Interpretation and next gate

The bounded control removes the known benchmark asymmetries, and its self-test
checks scalar/AVX2 score parity.  The ten-repeat timing series is sufficient for
this bounded kernel comparison, but it still does not measure MDBX layout, cold
or restart latency, writes, update/delete visibility, rebuild/publication, or
fresh qrels.  The result therefore authorises only the next implementation
step: a row/segment layout bakeoff with the same top-10 contract and repeated
isolated-process samples.  No codec winner or production threshold is inferred
from these numbers.

## 2026-09-30 normalized v2 follow-up

The original result above remains the historical fixed-heap control. It is
superseded for kernel timing by
[`2026-09-30-native-production-kernel-normalized-v2.md`](2026-09-30-native-production-kernel-normalized-v2.md),
which includes query preparation, block32 dense THQ scoring,
register-accumulating INT8 AVX2, stage timings, two-level percentiles,
scale-sidecar page accounting and an independent raw-JSONL audit. The v2
result remains an in-memory THQ/INT8 control rather than a finalist comparison
or codec-selection gate.
