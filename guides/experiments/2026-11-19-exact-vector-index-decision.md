# ExactVectorIndex R5 decision experiment

## Scope

This experiment evaluates the current `ExactVectorIndex::search()` path. It is
an offline decision experiment; it does not change similarity semantics,
production defaults, or the index implementation.

The executable uses one deterministic corpus/query generator for both the
production index and an independent compensated `long double` cosine oracle.
It records top-1/top-K identity parity, maximum score error, and raw timing
samples for two explicitly different scopes:

- `kernel_only_ms`: search on an already materialized `ExactVectorIndex`;
- `end_to_end_ms`: index construction followed by the same searches.

The fixture covers 128, 1024, 8192, and 65536 records, eight-dimensional FP32
inputs, four queries, and `top_k=10`. It includes cancellation, near-tie,
non-finite-input, and overflow controls. Scalar/SIMD selection is reported by
the executable; the current cosine production path still uses its compensated
accumulator independently of that diagnostic backend.

## Evidence contract

`tools/agent-memory-bench/validate_exact_vector_index_decision.py` runs the
executable, validates the matrix and oracle parity, emits a receipt and a
SHA-256 sidecar, and rejects a deliberately mutated parity result. The receipt
marks `production_activation=false`.

Timing samples are directional local measurements, not a claim of stable p99
latency. `sample_count` is the number of repeated in-process samples and is
not the number of independent requests. The long-double oracle is an offline
reference; on MSVC, `long double` may have the same precision as `double`, so
this experiment does not justify a production multiprecision dependency.

## Decision rule

The current implementation remains `KEEP CURRENT` unless a later cross-platform
run demonstrates a reproducible correctness failure or a separately reviewed
production-shaped change shows a stable end-to-end benefit without changing
the public contract. Kernel-only timing alone is insufficient for adoption.

## Reproduction

```text
cmake -S . -B build-r5-exact-vector -DAGENT_MEMORY_BUILD_BENCHMARKS=ON
cmake --build build-r5-exact-vector --target agent-memory-exact-vector-index-decision
ctest --test-dir build-r5-exact-vector -R agent_memory_exact_vector_index_decision --output-on-failure
```
