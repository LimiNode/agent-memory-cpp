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
| `fp64_streaming_sequential` | streaming sequential FP64 sum | production-shaped scalar baseline without product materialization |
| `fp64_streaming_neumaier` | streaming compensated FP64 sum | production-shaped compensated baseline without product materialization |
| `fp64_pairwise` | recursive pairwise FP64 reduction | reduction-order control |
| `fp64_blockwise` | 32-value FP64 blocks plus compensated block reduction | portable blocked control |
| `fp64_overflow_guarded_blockwise` | blockwise path with non-finite fallback to Neumaier | finite-overflow diagnostic control |

The oracle accumulates float products in long-double Neumaier arithmetic and
uses deterministic ID tie-breaking. The benchmark reports separate kernel-only
dot timing and scan-level timing. Document/query norms are precomputed outside
the scan timing; reusable product/block workspaces are reserved before timing.
Streaming methods perform multiply-and-accumulate directly; materialized methods
also expose the cost of filling and reducing a reusable product workspace. This
separates streaming accumulation, product materialization, reduction, and scan
ranking costs instead of treating them as one FP64 number.
The receipt reports score and dot error metrics, top-1/top-k agreement, and
non-finite counts. `--self-test` includes cancellation and finite-cancellation
scope controls, an injected non-finite fallback control, pairwise/blockwise
boundary sizes, ordered score/ranking checks, mutation controls, and a finite
random ranking smoke test.

`tools/agent-memory-bench/validate_exact_vector_numerical_frontier.py` rejects
malformed or non-finite receipt fields and has its own mutation self-test. CI
uploads the validated receipt together with its SHA-256 sidecar.
The receipt records the actual compiled checkout/source-tree SHA, the expected
pull-request head SHA, and the checkout SHA separately. CI passes all three
expectations explicitly to the validator; `unknown` or stale provenance is not
accepted for a published artifact. These provenance and streaming-method fields
are part of receipt schema version 2; older receipts are rejected rather than
silently reinterpreted.

## Expected result

Compensated and pairwise methods should reduce cancellation error on adversarial
fixtures. The blocked and guarded controls should expose the cost of making the
reduction order explicit. No method is promoted to production from this PR.

## Interpretation policy

The executable measures direct arithmetic kernels and a research scan only. It does not measure
end-to-end `ExactVectorIndex::search()`, SIMD throughput, cache effects in the
production index, or independent request latency. `sample_count` is the number
of complete corpus/query sweeps, not the number of independent requests. p50/p95
are directional summaries of the observed local samples, not stability claims.

## Limitations and follow-up

- `long double` precision is platform-dependent; on some platforms it is the
  same precision as `double`.
- The guarded fallback is deliberately limited to non-finite reduction; it does
  not detect finite cancellation. The self-test records this limitation rather
  than presenting the guard as an adaptive accuracy detector.
- Pairwise and blockwise reductions are not assumed to reproduce Neumaier on
  every adversarial ordering. Boundary fixtures record their deterministic
  reduction behavior and compare each method with the independent oracle using
  an explicitly documented tolerance.
- A later production-path benchmark may compare a selected method against the
  real `ExactVectorIndex` under identical execution order and build settings.
- The CI benchmark is bounded and correctness-gated; timing is recorded but is
  never a pass/fail threshold.
- No public API, persisted representation, or production similarity kernel is
  changed here.
