# ExactVectorIndex R5 decision experiment

## Question and boundary

Can the existing cosine accumulator be simplified without losing FP32 score
ordering? This is an offline synthetic decision experiment. Production source,
public APIs, storage formats and defaults are unchanged. Normative similarity
semantics remain owned by the index implementation and its contract tests.

Current production cosine uses a **Neumaier compensated double dot product**
and stable double inverse norms. It is not plain sequential FP64. The reported
SIMD backend applies to the dot/L2 primitives, not to cosine's compensated
numerator. Do not interpret `enable_simd=true` as SIMD cosine.

## Historical v1 observation (superseded methodology)

The `artifacts/r5-exact-vector-index/` v1 receipt and raw output are preserved
unchanged. They are preliminary, not decision evidence: the named
`compensated_long_double` oracle was actually sequential long double,
`kernel_only_ms` measured full prebuilt search, and the near-tie control was an
exact tie. MSVC long double also provides no independent wider reference.
These labels must not be reused as claims. v2 below replaces the method,
without rewriting historical hashes or SHAs.

The [R5 evidence disposition manifest](2026-11-20-r5-evidence-dispositions.json)
binds all three historical vector files by exact byte hash and marks them
`SUPERSEDED`. It also binds the replacement v2 receipt. The disposition validator
rejects their use as `PRIMARY` or a production decision even after renaming a
receipt. `EXECUTED` in an archived JSON records occurrence, not methodological
validity. The validator runs in the independent Documentation checks lane.

## v2: independent correctness and separately scoped timings

`validate_exact_vector_index_decision.py` recomputes scores in Python Decimal
with 100 decimal digits from the exact exported FP32 values. It rounds to the
public FP32 score type before sorting `score_desc, id_asc`. The oracle does
not call production arithmetic and does not depend on host long double.

Six correctness fixtures cover cancellation, actual near ties plus exact
ties, tiny/subnormal-sensitive inputs and independently generated random
cosine, dot and negative squared L2. Every fixture runs actual
`ExactVectorIndex::search()` with scalar and automatic dispatch, at
`K=0,1,10,N`, exporting full result IDs and scores. The cancellation fixture
uses products `2^120 + 1 - 2^120`: sequential double loses the positive term,
while Neumaier preserves its ordering relative to zero. Seven executable
controls cover nonfinite record/query inputs, dimension, empty query/index and
overflow under both dispatch settings.

Cosine's score tolerance is `max(8e-7*abs(reference), 2e-44)`, so returning zero
for the positive cancellation score cannot pass. Dot and L2 allow
`max(2e-5*abs(reference), 2e-6)` because their production FP32 primitives round
differently from the exact sum. Identity order must still match. The validator
also corrupts actual result IDs/scores, a corpus value, exception evidence,
dispatch coverage, timing samples and compiled-source evidence and requires
rejection. A boolean `parity=true` is not the correctness oracle.

The timing matrix uses four independent fixed query vectors (not corpus
self-queries), shapes `(128,8), (1024,128), (8192,32), (65536,32), (128,768)`,
cosine and dot, scalar/auto, and `K=1,10,N`. Each search measurement includes
validation, metadata filtering, scoring, selection and result materialization
on a prebuilt index. Construction is a separate single observation. Seven
repeated query batches are samples, not 28 independent requests; no stable
p99 or representative workload claim is made.

Arithmetic-only cosine controls compare FP32, sequential FP64 and Neumaier
with precomputed record inverse norms; dot controls call the actual
`VectorSimilarityComputer` scalar/automatic primitive. They exclude storage,
selection and materialization. Kernel timings are **not** end-to-end candidate
speedups. Candidate order rotates between kernel samples. Candidate kernels
that fail correctness are not promoted into alternative production indexes.

The permanent `agent_memory_exact_vector_index_test` also contains the exact
power-of-two R5 counterexample, with scalar/automatic dispatch and top-1/top-2.
It requires the positive result to outrank a lexically earlier zero-score ID
and to equal the representable FP32 score `2^-121`. Existing sign/permutation
regressions remain; the independent Decimal oracle is not copied into core
tests and still checks the broader exported research corpus separately.

Large timing cases report sorted top-ten prefixes and cardinality, not a
Decimal oracle over all 65536 records. The full independent correctness claim
is limited to the six exported suites. Inputs use a specified mt19937-to-dyadic
mapping, avoiding implementation-dependent real distributions.

## Evidence and decision

Local MinGW 15.2 Release C++17 validation passed: six correctness cases, ten
timing cases, all exception controls and nine evidence mutations. Cosine
scores matched the FP32-rounded Decimal reference exactly in this fixture;
maximum absolute errors were `4.76837158203125e-7` for dot and
`7.62939453125e-6` for negative squared L2. Sequential FP64 failed cancellation;
FP32 failed cancellation and tiny cosine. Neumaier passed.

Decision: **KEEP CURRENT** compensated cosine. There is a concrete
counterexample to the proposed simplifications, not merely an absent speed
measurement. No new production accumulator or multiprecision dependency is
proposed. This does not claim that all FP64 algorithms fail, nor qualify
synthetic timing as production latency.

The [local source-bound receipt](2026-11-19-r5-exact-vector-v2.receipt.json)
and adjacent `.sha256` bind the clean `6272e7b2` execution. Raw output remains
in the canonical external workspace named in that receipt; per-head CI
artifacts provide separate Ubuntu/MSVC runs. Later receipt/docs commits are
not retrospectively substituted for the tested source SHA.

Receipts separate `tested_source_head`, `expected_pr_head`, compiled checkout
and compiled-source hashes. The expected HEAD is enforced in CI; local
uncommitted-source runs are explicitly reported as such. The required
Ubuntu/Windows MSVC lane emits exact-head raw data, receipt and SHA-256 sidecar
and is included in the CI gate. JSON-OFF library/install checks stay separate:
this JSON-exporting benchmark intentionally requires JSON and MDBX is disabled.

## Reproduction

```text
cmake -S . -B .build/r5-vector -DCMAKE_BUILD_TYPE=Release -DAGENT_MEMORY_ENABLE_MDBX=OFF -DAGENT_MEMORY_BUILD_BENCHMARKS=ON -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=ON
cmake --build .build/r5-vector --config Release --target agent-memory-exact-vector-index-decision
ctest --test-dir .build/r5-vector -C Release -R ^agent_memory_exact_vector_index_decision$ --output-on-failure --no-tests=error
```
