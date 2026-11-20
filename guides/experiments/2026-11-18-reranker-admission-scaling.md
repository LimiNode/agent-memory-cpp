# Reranker admission scaling research

Date: 2026-11-18
PR context: `research/reranker-admission-scaling` (Draft)

## Scope

This experiment measures the admission boundary implemented by
`HybridRetrievalEngine` and compares it with two research-only lookup indexes.
The production contract is the implementation merged by PR #625 at
`c93a7b867cd802e279a4118b07cc177f10322cf3`. The receipt also records the
actual source-tree SHA used to compile the executable.

The experiment does not change the production admission algorithm, retrieval
defaults, ABI, or persisted schema. It does not select a production rewrite.

## Variants and measurements

`production_hybrid_engine_admission` runs the real `HybridRetrievalEngine`
path against the same fixture and reranker output used by the controls. The
`sorted_identity_index` and `hash_identity_index` arms use the same identity,
metadata, score, and finite-value checks, but replace the linear identity
lookup with a sorted or hash index. Duplicate detection is performed by the
bounded hash insertion in both arms; protected-field comparison uses the
production-shaped ID fields directly and is allowed to propagate allocation
failures. Index construction is measured separately
from lookup/admission timing. The production arm is an end-to-end
`retrieve()` measurement; the indexed arms are explicitly lookup-only
controls, not end-to-end latency claims.

Each case records raw timing samples, summary statistics, allocation counts and
bytes for every measured lookup, and the trusted-record copy count. Warmups are
excluded. `sample_count` is the number of measured repetitions, not the number
of independent requests. The fixed candidate counts are 10, 50, 100, 500, and
1000; metadata widths are recorded in each case.

## Correctness controls

The self-test and every benchmark case require parity with the production
baseline for valid reorder/score output. Duplicate, foreign chunk, foreign
resource, mismatched section, metadata mutation, non-finite score, and oversized
reranker outputs must fail closed. Mandatory metadata filtering is checked on
the production query path. Allocation accounting disables its counter on both
normal and exceptional exits.

The result is a qualification receipt only. The receipt keeps
`production_end_to_end` separate from `admission_only`; their timing samples
must not be used as a direct production speedup comparison. CI validates the
expected pull-request HEAD, actual compiled checkout SHA, production-contract
SHA, source-manifest SHA-256, fixture SHA-256, raw samples, and a SHA-256
sidecar. Negative validator controls cover wrong HEAD, missing digest fields,
and corrupt receipt/sidecar bytes. A production admission rewrite requires
matched workloads, a separate end-to-end benchmark, and confirmation that all
security and provenance invariants remain unchanged.
