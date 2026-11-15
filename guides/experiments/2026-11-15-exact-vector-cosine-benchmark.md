# ExactVector cosine accumulation benchmark (2026-11-15)

## Status

Research-only directional measurement. This note does not establish a
production latency target or justify reverting the numerical-correctness fix
in #622.

## Question

What is the bounded latency cost of the #622 cosine arithmetic change, which
replaced the pre-#622 float dot/norm path with stable norm computation and a
double-precision cosine numerator?

## Protocol

The executable is `agent-memory-exact-vector-cosine-bench`. It generates a
deterministic synthetic corpus and query set for dimensions 384, 768 and 1536.
Each case has 512 documents, 24 queries, top-k 10, two warm-up repeats and
eight measured repeats. Each query-level sample runs the legacy and current
scan in alternating order. `sample_count` is therefore a measured sample
count, not a count of independent requests; p95/p99 are directional summaries
of this bounded run.

The legacy path reconstructs the cosine arithmetic from the parent of #622
(float norm and the selected float SIMD dot product). The current path uses
the #622 stable norm and the current production sequential scalar double
accumulation. The fixture self-test also contains a near-cancellation case
that intentionally exposes the known limitation until the separate corrective
PR is integrated. Both paths use the same generated vectors and top-k
comparator. The benchmark is intentionally
scoped to cosine scan arithmetic; it is not an end-to-end `ExactVectorIndex`
throughput claim.

Correctness is checked against an independent long-double reference for every
query. Score errors are matched by candidate ID, not by rank position, so a
ranking difference cannot hide a large error. The fixture self-test covers
near-cancellation, near-ties with deterministic ordering, and normalized
vectors. The current path must return the same top-k IDs as that reference
before latency results are accepted.

## Reproduction

```text
cmake -S . -B build-bench \
  -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_BUILD_BENCHMARKS=ON \
  -DAGENT_MEMORY_ENABLE_JSON=ON
cmake --build build-bench --config Release \
  --target agent-memory-exact-vector-cosine-bench
ctest --test-dir build-bench --build-config Release \
  -R agent_memory_exact_vector_cosine_benchmark_self_test
build-bench/tools/agent-memory-bench/agent-memory-exact-vector-cosine-bench \
  exact-vector-cosine-report.json
```

The report records compiler, optimization profile, CPU identifier, legacy
SIMD backend, source commits, corpus seed/hash, latency percentiles and the
independent compensated long-double-style reference result. The current
implementation's cosine accumulation is reported as current production scalar
double; the legacy SIMD backend must not be interpreted as an acceleration of
the new numerator.

## Historical observed run

The table below is retained as the original directional receipt from the
straightforward-double benchmark. It predates the independent oracle and
fixture protocol and must not be used as the current performance result.

Environment: GCC 15.2.0, `-O3 -DNDEBUG`, x86_64, CPU identifier
`Intel64 Family 6 Model 63 Stepping 2, GenuineIntel`; legacy vector backend
selected AVX2. The generated case hashes are pinned in the raw report:

| Dimensions | Legacy p50 (ms) | Current p50 (ms) | Current / legacy | Current p99 (ms) | Top-k reference |
| ---: | ---: | ---: | ---: | ---: | :--- |
| 384 | 0.04485 | 0.25220 | 5.62x | 0.317608 | PASS |
| 768 | 0.09050 | 0.50405 | 5.57x | 0.627916 | PASS |
| 1536 | 0.16950 | 1.00015 | 5.90x | 1.178301 | PASS |

The current score error against the double reference was at most
`7.44e-09`; the legacy path's maximum was `4.87e-08` on this run. These
numbers qualify the arithmetic trade-off on this synthetic scan only. They do
not predict full-index latency, cache behavior on a production corpus, or
cross-machine p99 stability.

## Interpretation and next checks

This historical run confirmed the expected cost of the then-current scalar
double numerator. The report must be regenerated for any changed source or
environment; the independent oracle and near-cancellation diagnostic are now
part of the protocol. It does not
prove that the cost is acceptable for production workloads. Before any
optimization PR, repeat the same protocol on representative real embeddings,
measure the complete `ExactVectorIndex::search()` path, and compare a
correctness-preserving SIMD/fused implementation against the same long-double
reference.
