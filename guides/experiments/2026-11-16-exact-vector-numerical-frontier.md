# Exact-vector numerical and optimization frontier

Date: 2026-11-16
PR context: `research/exact-numerical-frontier` (Draft)
Source baseline: the `liminode/main` commit used to configure the executable

## Question

Which portable accumulation strategies improve numerical stability for cosine
ranking, and what is their direct arithmetic cost before any production-kernel
change is considered?

## Harness

`tools/agent-memory-bench/exact_vector_numerical_frontier.cpp` generates a
deterministic finite-float corpus and compares these research-only methods:

| Method | Accumulator | Purpose |
| --- | --- | --- |
| `float_lanes` | eight independent FP32 lanes | historical low-precision control |
| `fp64_sequential` | sequential FP64 sum | current wide scalar control |
| `fp64_neumaier` | compensated FP64 sum | cancellation robustness control |
| `fp64_pairwise` | recursive pairwise FP64 reduction | reduction-order control |
| `fp64_blockwise` | 32-value FP64 blocks plus compensated block reduction | portable blocked control |
| `fp64_guarded_blockwise` | blockwise path with non-finite fallback to Neumaier | guarded fallback control |

The oracle accumulates float products in long-double Neumaier arithmetic and
uses deterministic ID tie-breaking. The benchmark reports timing samples,
absolute/relative score error, top-1/top-k agreement, and non-finite counts.
`--self-test` includes a cancellation fixture, an injected non-finite fallback
control, and a finite random ranking smoke test.

## Expected result

Compensated and pairwise methods should reduce cancellation error on adversarial
fixtures. The blocked and guarded controls should expose the cost of making the
reduction order explicit. No method is promoted to production from this PR.

## Interpretation policy

The executable measures direct arithmetic scans only. It does not measure
end-to-end `ExactVectorIndex::search()`, SIMD throughput, cache effects in the
production index, or independent request latency. `sample_count` is the number
of complete corpus/query sweeps, not the number of independent requests. p50/p95
are directional summaries of the observed local samples, not stability claims.

## Limitations and follow-up

- `long double` precision is platform-dependent; on some platforms it is the
  same precision as `double`.
- The guarded fallback is exercised with an explicit non-finite reduction
  control because finite `float` inputs cannot overflow a `double` accumulator
  at practical vector widths.
- A later production-path benchmark may compare a selected method against the
  real `ExactVectorIndex` under identical execution order and build settings.
- No public API, persisted representation, or production similarity kernel is
  changed here.
