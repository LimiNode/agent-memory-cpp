# THQ FastScan v3 matched kernel bakeoff

Date: 2026-09-21
Status: `EXECUTED`

## Question

The #437 corrective replay established parity, but its SIMD prototype rebuilt
query LUTs inside every 32-document block, used a plane-major layout, and
converted every uint8 lookup back to FP32 with a per-pair scale. This gate asks
whether a Faiss-style implementation changes the winner, and whether its dense
scan result transfers to the production-shaped R4 candidate workload.

## Protocol

All arms score the same canonical THQ4 96-byte document codes. The benchmark
contains:

1. scalar exact byte LUT;
2. exact byte LUT with four independent accumulators;
3. scalar exact pair LUT;
4. exact AVX2 pair LUT over a block32 layout;
5. scalar per-pair-scale uint8 control;
6. query-prepacked per-pair-scale AVX2 LUT over a plane layout;
7. query-global-scale uint8 LUT with uint16 accumulation over a plane layout;
8. the same integer kernel over a true block32 layout.

The global scale reserves enough range for rounding and verifies that the sum
of per-pair maxima fits uint16. LUTs are packed once per query. Arms are
executed in a deterministic randomized order. Reported `total` timing includes
the arm-specific query-table setup.

Two workloads are intentionally separate:

- dense: persistent layouts are prebuilt, then 1M documents are scored and
  top-128 is selected;
- production-shaped: the frozen R4 stream supplies about 5k arbitrary document
  IDs; each arm includes code gathering, any required runtime packing, scoring,
  and top-128 selection.

The dense result is a bounded eight-query replay with five timed repetitions.
The production result covers all 152 canonical queries with one warmup and 20
timed repetitions per arm. The dense and production audits bind their result,
runner, and source inputs by SHA-256. The production audit additionally binds
the candidate stream, FP32 documents, and qrels. These are source-binding and
invariant audits, not independent computational replays.

## Dense 1M result

| arm | total p50 | total p95 | mean top-128 overlap |
| --- | ---: | ---: | ---: |
| byte LUT scalar | 119.49 ms | 123.81 ms | 1.000000 |
| byte LUT unrolled4 | 87.61 ms | 91.78 ms | 1.000000 |
| pair FP32 scalar | 220.67 ms | 225.12 ms | 1.000000 |
| pair FP32 AVX2 block32 | 75.08 ms | 79.29 ms | 1.000000 |
| pair uint8 scalar | 232.90 ms | 238.46 ms | 0.998047 |
| pair uint8 prepacked plane | 116.69 ms | 122.47 ms | 0.998047 |
| global uint8/u16 plane | 59.84 ms | 62.31 ms | 0.822266 |
| global uint8/u16 block32 | **34.13 ms** | **36.24 ms** | 0.822266 |

The exact pair-SIMD arm disproves the broad interpretation that pair
factorization is inherently slower than byte LUT. With a suitable persistent
layout it is about 14% faster than the optimized exact byte arm on this host.
The Faiss-style integer block32 arm is much faster again, but its global-scale
quantization changes too much of the exact THQ top-128 to be treated as an
exact substitute.

## Production-shaped R4 result

| arm | total p50 | total p95 | total p99 | THQ top-128 overlap | final FP32 top-10 overlap | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| byte LUT scalar | 0.805 ms | 0.896 ms | 0.999 ms | 1.000000 | 1.000000 | .654201 |
| byte LUT unrolled4 | **0.641 ms** | **0.726 ms** | **0.820 ms** | 1.000000 | 1.000000 | .654201 |
| pair FP32 scalar | 1.263 ms | 1.377 ms | 1.471 ms | 1.000000 | 1.000000 | .654201 |
| pair FP32 AVX2 block32 | 1.374 ms | 1.680 ms | 2.031 ms | 1.000000 | 1.000000 | .654201 |
| pair uint8 scalar | 1.356 ms | 1.484 ms | 1.604 ms | 0.998664 | 1.000000 | .654201 |
| pair uint8 prepacked plane | 1.389 ms | 1.675 ms | 2.023 ms | 0.998664 | 1.000000 | .654201 |
| global uint8/u16 plane | 1.267 ms | 1.558 ms | 1.793 ms | 0.919870 | 1.000000 | .654201 |
| global uint8/u16 block32 | 1.233 ms | 1.532 ms | 1.845 ms | 0.919870 | 1.000000 | .654201 |

All exact arms agree with the byte reference at top-128; the maximum recorded
FP32 score error is below `4.5e-7`. Both quantized families preserve the
candidate-FP32 top-10 on all 152 queries after the common FP32 rerank, despite
their different THQ top-128 membership.

The workload changes the decision. Runtime gather and block packing cost more
than the block kernels save at about 5k arbitrary IDs. The optimized doc-major
byte LUT is about 1.9x faster at p50 than the best block32 integer arm. Thus:

> block32 integer FastScan wins a persistent dense scan, while direct
> doc-major byte LUT wins the measured R4 candidate cascade.

This is not a contradiction: the two layouts optimize different access
topologies.

## Decision and limitations

- Keep `byte_lut_unrolled4` as the current production-shaped THQ filter
  candidate.
- Keep exact pair-SIMD block32 as the dense/full-scan candidate.
- Do not promote global-scale uint8 solely from final top-10 survival: its
  top-128 membership loss is large and held-out confirmation is absent.
- Do not materialize the whole 1M corpus only in block32 form for R4 random-ID
  serving; scoring neighbouring documents would create read amplification.
- Timings are from one Windows host. Core/NUMA pinning and hardware counters
  were not available in this run.
- The common final stage in this gate is FP32. Final-codec selection remains a
  separate research question.

Artifacts:

- `2026-09-21-thq-fastscan-v3-dense-result.json`;
- `2026-09-21-thq-fastscan-v3-dense.audit.json`;
- `2026-09-21-thq-fastscan-v3-production-result.json`;
- `2026-09-21-thq-fastscan-v3-production.audit.json`.

Next: run the already prepared Faiss ResidualQuantizer 32/48-byte capacity
gate, then compare the resulting finalist against faithful RSLM and INT8 in a
native end-to-end cascade.

## 2026-11-20: R6 certified-refinement feasibility contract

Base: `main@9005a010b1044e1c93ef3690d41fd56a2c7bbb2e` after R5 closure.
Status before implementation: `NOT_EXECUTED`; correctness is the first gate.
This is a certificate/refinement extension of the existing FastScan research
harness, not a new algorithm family or a production/cascade qualification.

### Frozen reference and inputs

The reference is the existing scalar `score_pair`: start at FP32 positive
zero; for byte positions 0 through 95, add the low-nibble pair-table entry
and then the high-nibble entry, storing each of the 192 additions in FP32.
Rank by `(reference score ascending, document ID ascending)`. Correctness
requires identical ordered IDs and selected reference-score bits, not a
tolerance, set overlap, byte-LUT equivalence or exact raw-float retrieval.

The pilot accepts immutable 96-byte codes and distinct nonnegative int32 IDs.
Malformed shape, duplicate/negative IDs, nonfinite tables, nonpositive or
nonfinite scale, and tables whose absolute-sum/error bound permits FP32
overflow are rejected. `K>N` clamps to N; K=0 and an empty corpus return an
empty result after validation. Byte/tile tails do not introduce candidates.
This standalone component does not establish authorization, freshness,
snapshot consistency, storage I/O or concurrent-update semantics.

### Certificate and floating-point boundary

For the actual stored FP32 pair entries P[p,j], uint8 entries q[p,j] and
positive FP32 global scale alpha, bound each residual
`P[p,j] - q[p,j]/alpha` outwards in FP64. Sum per-pair minima/maxima with
outward rounding. For exact integer Q (at most 192*255=48960), enclose the
reference score by `Q/alpha + R_min - E` and `Q/alpha + R_max + E`, where
`E = gamma_192*A + 192*2^-150/(1-192*2^-24)`,
`gamma_192 = 192*2^-24/(1-192*2^-24)` and
`A = sum_p max_j abs(P[p,j])`. Every elementary bound operation rounds
outwards; the underflow term is calculated in FP64, never FP32.

Certification requires IEEE binary32/binary64, round-to-nearest,
gradual underflow, no excess precision or reassociation, and finite reference
intermediates. The target uses strict floating-point compilation; runtime
probes reject non-RN and FTZ/DAZ behavior. Unsupported environments are
explicit errors, not silently certified results. Nonfinite/overflowing LUT
construction is rejected before integer rounding/quantization.

Seed K distinct exact incumbents from the integer top-K and obtain a real
cutoff tau. Refine every other candidate with `lower <= tau`; prune only
`lower > tau`, retaining equality ties. The first pilot deliberately retains
the initial cutoff instead of adding another heap or adaptive policy.

### Falsifiers and stop rule declared before measurements

Correctness controls cover ties with reversed IDs, half-step/clipped
quantization residuals, signed cancellation, tiny/subnormal and large finite
values, NaN/Inf/overflow, invalid scale/IDs/shape, K=0/K>N/empty inputs and
non-block-aligned tails. Any escaped reference score or ordered-ID/score-bit
mismatch rejects this exact arm. Deliberately corrupt result/bound controls
must be rejected. Existing pair-AVX2 is separately checked for bitwise
eligibility; byte/unrolled equivalence is not assumed.

No performance measurements precede independent design/correctness review.
If that gate passes and source inputs become available, compare against the
fastest existing arm that proves the same frozen contract. Predeclared
continuation threshold: at least 10% lower matched full component-path median
with no p95 regression, after charging LUT/envelope setup, scan, selection,
gather/packing, refinement and memory. Near-exhaustive refinement or failure
of that threshold means `KEEP CURRENT`; unavailable payloads mean `DEFER`.
Historical exposed queries cannot establish new held-out retrieval quality.
No variant sweep is authorized to rescue this particular certificate.

Builds belong under canonical `.build/r6-certified-adc`; retained output
under canonical `.artifacts/r6-certified-adc` with source/input/command
manifest. No production API/default/schema or dependency changes are part of
this pilot.

### Local prototype correctness gate

The uncommitted prototype on the stated base passed the focused Release build
with GNU C++ 15.2, C++17, AVX2 and
`-fno-fast-math -ffp-contract=off -frounding-math`. This is a working-tree
correctness result, not a replay of the unchanged base commit or a frozen
performance receipt. `--certificate-self-test` passed 30 correctness fixtures,
24 invalid-input controls, four deliberate bound/result corruption controls
and three environment controls (non-RN, FTZ and DAZ). A literal three-document
case requires both displacement of the integer seed by exact refinement and
pruning of a distant candidate; the controls are not only exhaustive fallbacks.
The existing `--self-test` and the new focused CTest also passed (1/1).

Pair AVX2 preserved all score bits and ordered IDs on these fixtures; its
eligibility on the measured payload remains to be checked. The selector reports
exact-evaluation count and a conservative sum of vector payload capacities,
including sorted-ID validation and both selectors. This is not process RSS or
an allocator/stack measurement. Its scalar integer scan and duplicate-ID
validation remain charged costs; no optimization sweep is implied.

Performance and applied retrieval quality remain `NOT_EXECUTED`. Freeze an
exact prototype source revision and obtain independent correctness review
before the first benchmark. No API, production setting or persisted format is
changed.

### Frozen bounded mechanics configuration (before implementation)

Correctness was independently reviewed and frozen at
`0326b890c9a32ae9f7f56d3ac018a2661a02e7a5`. The next opt-in
`--certificate-benchmark` mode remains `NOT_EXECUTED` until its own reviewed
source revision is committed. It has no tunable profile sweep:

- dense document prefixes N = 4,096 / 65,536 / 1,000,000;
- K = 16 / 128 and historical query rows 0 through 7, without selection;
- five measured repeats and one warmup per N/K/query/arm;
- three arms: existing scalar pair FP32, existing pair AVX2 block32 and the
  frozen certified selector, without a rescue optimization;
- paired arm order chosen from the six permutations by raw MT19937 output
  with seed 20261120; every order and trial identifier is retained.

Before any timed trial, exhaustive scalar scores and AVX2 score bits are
checked for all 8,557,056 profile/query/document pairs (prefixes overlap). Every
certificate interval must enclose its scalar score, and all 48 N/K/query
certified results must reproduce ordered IDs and selected score bits. All
actually refined scores are checked, not just the selected ones. Any mismatch
rejects the whole run before measurement. Measured results are checked again
outside their stopwatch; these checks are not subtracted from a measured phase.

Each raw sample has one enclosing component-path stopwatch: required query
LUT construction and validation, environment checks, scan, allocation and
selection, and (for certification) duplicate-ID validation, envelope, integer
selection, incumbent scoring and refinement are inside. Scalar/AVX2 arms build
coordinate and pair LUTs only; certification additionally builds the actual
global uint8 LUT. Unneeded byte/local LUTs are skipped, while the old default
construction and 192-add reference order are unchanged. All arms share
deterministic sequential distinct IDs; the selector's own redundant ID check
remains charged. No timing is reconstructed by adding/subtracting phases.

This is persistent dense mode only. Code-prefix copies and both persistent
layout vectors are prepared before trials; their build wall time, payload
capacities and common resident input/reference payloads are reported. Layout
build is an explicitly excluded one-time cost, not a serving win. Each sample
reports its temporary vector capacities (including both selection buffers for
certification), exact-evaluation count/fraction, active pair/global LUT bytes
and the larger allocated table object's size. Capacities are not process RSS.
There is no random-ID gather, storage read, final rerank or quality arm.

The raw report retains all 720 timed samples, configuration, compiler macros,
input paths/byte counts, checks and descriptive timing summaries. The external
receipt must bind exact source/base SHA, source and executable hashes, command,
compiler/build flags and canonical input hashes; caller metadata alone is not
a verified source identity. Repeated samples are not independent queries;
there are only eight exposed query rows, so reported p50/p95 are descriptive
nearest-rank sample distributions, not statistically qualified serving tails
or held-out quality. Every raw row and reference case retains its ordered
selected IDs and reference-score uint32 bits for an independent validator.
The existing 10%-median/no-p95-regression criterion applies separately to each
fixed N/K profile against its fastest eligible same-contract baseline. A dense
component result cannot promote a production default. Mean exact-refinement
fraction >= 0.90 on a profile is predeclared near-exhaustive and yields
`KEEP_CURRENT`, even if its timing threshold happens to pass.

The separate `audit-certified-adc.py` replays all raw rows, reference payloads,
MT19937 order, integer refinement accounting, nearest-rank summaries and each
profile decision. Its validator-only synthetic self-test rejects 17 independent
corruptions without requiring the private historical data. The same checks run
on the actual raw report after execution. This audit validates report integrity;
it does not independently recompute document scores from unavailable inputs or
authenticate a caller's source metadata. Source, executable and input bindings
remain separate checks in the execution manifest and independent review.
Aggregate integer exact-evaluation counts before division; the boundary gate
uses integer `10*exact_sum >= 9*N*sample_count`, not a sum of rounded fractions.
If no profile qualifies, close this scalar-screen/fixed-tau implementation
only; the result does not reject every possible certified ADC design.
