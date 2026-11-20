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

### Executed bounded mechanics result: keep the existing pair AVX2 arm

The single preregistered run executed on source
`a587fa326d25b40a92dacf09ea93c772b9fcaa9c`, tree
`a255dad210d486157fb4f1d3a4b6532958e499ec`, on 2026-11-20 UTC. Later
documentation commits are not silently substituted for this tested source.
The executable source SHA-256 is
`8e487b8a8f769c4d0299ad294891b0fb615620eb4b4c25c0a2cd2d5117dfb1ca`;
executable SHA-256 is
`095d82ba0cbd56381e383c38c1a004ae89b1e1482810370e6b323e900274c26d`.
Build: MinGW GNU 15.2.0, Release/C++17, AVX2, strict flags above;
Windows 10.0.26100, Intel Xeon E5-2696 v3 at 2.30 GHz;
nlohmann_json 3.11.3. This is one local host, not a cross-platform timing result.

Global admission passed all 8,557,056 scalar/AVX2 score-bit and envelope checks,
all 48 certified cases and every actually refined score. All 720 timed rows
also passed ordered-ID/selected-score-bit checks. Correctness controls on this
source passed 32 fixtures, 24 invalid inputs, four corruption controls and
three FP-environment controls; old self-test and focused CTest passed (1/1).
The independent raw validator passed all rows and 17 deliberate corruptions.

The table reports descriptive sample distributions in milliseconds. Each row
has 40 samples from eight exposed queries, not 40 independent queries. The
fastest same-contract baseline was pair AVX2 in every fixed profile.

| N | K | Pair AVX2 p50 / p95 | Certified p50 / p95 | Certified / baseline p50 | Mean exact-refinement fraction | Decision |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 4,096 | 16 | 0.2425 / 0.2803 | 1.7779 / 2.0540 | 7.33 | 83.33% | `KEEP_CURRENT` |
| 4,096 | 128 | 0.2506 / 0.3124 | 1.8059 / 2.0673 | 7.21 | 99.86% | `KEEP_CURRENT` |
| 65,536 | 16 | 3.7993 / 4.1789 | 21.4116 / 24.8312 | 5.64 | 67.00% | `KEEP_CURRENT` |
| 65,536 | 128 | 3.7997 / 4.1774 | 23.0391 / 26.7015 | 6.06 | 87.12% | `KEEP_CURRENT` |
| 1,000,000 | 16 | 61.7785 / 65.4689 | 241.4050 / 346.0244 | 3.91 | 39.01% | `KEEP_CURRENT` |
| 1,000,000 | 128 | 62.2859 / 66.3608 | 331.5996 / 400.8537 | 5.32 | 67.58% | `KEEP_CURRENT` |

On the 1M profile the two exhaustive arms retain 12,000,000 bytes of temporary
vector payload versus 26,000,000 for certification. The table object occupies
135,984 bytes in every arm; only required LUT contents are computed. The
existing layout helper materializes both 96 MB layout vectors; its 1M build
cost was 189.7501 ms, separately reported and excluded from per-query time.
Common resident inputs/reference vectors and each prefix/ID copy are also
reported. Certification reads document-major codes rather than these SIMD
layouts, but the experiment keeps both resident. These numbers are not peak
RSS, allocation counts, an optimized per-arm storage footprint, serialized
index size or update cost; those properties remain `NOT_MEASURED`.

The certificate can reduce exact pair evaluations, but this scalar integer
screen, fixed initial cutoff, bounds and two selectors did not pay for their
full cost. The result rejects continuation of this implementation under the
frozen gate, not all certified screening or packed-LUT ADC. No rescue sweep,
production proposal or default change follows. K8 bounds and sparse IVF
replication remain `DEFER` pending their complete pinned inputs and evaluation
prerequisites; existing WAND/MIH/fusion gates remain unchanged.

Retained package, relative to the canonical checkout:
`.artifacts/r6-certified-adc/local-mechanics-v1/`. It contains `raw.json`,
`manifest.json`, correctness JSON/JUnit and `validation.json`. Bindings:

- raw SHA-256: `56719a53443acd82d1f2bc77d0d6a2a49fc1b2a5e0e17b31440c10a4c2c4ec4f`;
- execution manifest SHA-256: `8e803d80ca4dc9b231464f48d7c7ce6af5fc761affbfcbe50d4b9b883c40f778`;
- validation output SHA-256: `08a767ebedda2941975ff46605daeb8476167d169583c76d14830e98e4af6ad8`.

The manifest binds the exact canonical input paths/sizes/hashes listed in the
[R6 inventory PR](https://github.com/LimiNode/agent-memory-cpp/pull/676),
compiler/link commands, source blob, executable, invocation and timestamps.
Run `python tools/agent-memory-bench/audit-certified-adc.py --raw <raw.json>
--manifest <manifest.json> --mutation-self-test` to replay result integrity;
`--self-test` runs validator-only synthetic controls without historical data.
To recompute the native experiment, use the frozen source and
`agent-memory-thq-fastscan-v3-benchmark --certificate-benchmark --codes <codes>
--thresholds <thresholds> --queries <queries> --output <new-raw.json>`; input
hashes must match the inventory before execution. Do not overwrite the retained
package or call a different source an exact replay.

The raw package remains local evidence, not a published permanent archive or
`PRIMARY` registry result. Public computational replay of the full source
data is `NOT_ESTABLISHED`. Applied retrieval quality, a fresh sealed Pareto
curve, random-ID serving, final reranking, storage/authorization/freshness and
production readiness remain `NOT_EXECUTED`. Exactness is relative only to the
declared FP32 pair-ADC reference.

### Windows self-test stack correction

The Windows R4 CI lane on PR head `aedb1a77` built successfully but failed at
the new certificate self-test before producing its JSON. Local reproduction
of that source with MSVC 19.44.35228 returned native status `0xC00000FD`
(`STATUS_STACK_OVERFLOW`), not a numerical certificate rejection. The existing
self-test still passed. PE inspection showed the unchanged default 1 MiB stack
reserve. MSVC prologues reserved 279,776 bytes in `main` and 817,968 bytes in
`certificate_self_test`: those two frames alone exceeded the reserve, before
the 136,976-byte `make_tables` frame could run.

The correction moves the six large test-only table fixtures into one owned
heap array and reuses its invalid-input scratch entry. It does not enlarge
the linker stack, change scoring/bounds/selection/benchmark functions or relax
any control. The resulting local MSVC self-test frame is 274,160 bytes, with
the same 1 MiB PE reserve. Release MSVC and GNU 15.2 both pass the unchanged
32 fixtures, 24 invalid inputs, four mutation controls, three environment
controls, existing self-test and focused CTest (1/1 per compiler).

No performance replay accompanies this test-storage correction. The executed
mechanics result and artifact bindings above remain bound to their original
source `a587fa326d25b40a92dacf09ea93c772b9fcaa9c`, not the corrected test source.
The canonical build executable was regenerated during this correction; the
original measured executable bytes were not separately retained. Its recorded
hash was independently verified before that rebuild and is historical, not
the identity of the executable currently at the manifest's build path.
The original raw report and execution manifest remain byte-for-byte intact.

## 2026-11-20 R6 implementation-efficiency corrective contract

Status: preregistered contract, now executed below; research-only, with no
production selection or merge authorization.
This continuation preserves PR #677, its source-bound v1 timings and raw files.
It is stacked on corrected source `34a24d2c29d9c701d5b289ecf42e1aac60e75434`;
historical measured source remains `a587fa326d25b40a92dacf09ea93c772b9fcaa9c`.
The existing FP32 pair-ADC contract and outward envelope remain normative.
Ordered numeric IDs and reference FP32 score bits must agree, including ties.

Question: is the v1 loss primarily implementation overhead, or inadequate
certificate selectivity after an efficient, contract-equivalent implementation?
The previous six KEEP_CURRENT decisions reject v1 only, not certified pruning.

Before timing, validate isolated changes A1 SIMD integer scan, A2 monotone integer
cutoff, A3 construction-validated trusted IDs, A4 caller-local reusable workspace,
A5 block32 exact refinement and A6 dynamic tau. A0 calls unchanged v1.
Within the common template scaffold, each isolated arm toggles only its named
mechanism; A0 itself calls the historical function. Scaffold timing parity is
not assumed. At most one combined arm may follow, selected from isolated
evidence, not an all-combinations sweep.
Untrusted candidate IDs retain validation; no shared mutable query workspace.
For cutoff use binary search on the *same outward lower-bound function* over
Q=0..48960. Its constituent correctly rounded operations are monotone for positive
finite scale; the greatest Q with lower(Q)<=tau is an exact prefix endpoint.
Represent the empty prefix separately. Test every Q, threshold neighbours, ties,
underflow, overflow and unsupported floating-point environments before use.

Timing uses original eight exposed queries, N=4096/65536/1000000, K=16/128,
one warmup and five repeats, fixed arm-order seed. These are mechanics, not
held-out applied quality. Keep uninstrumented full-query timing separate from
intrusive diagnostic stages (ID checks, LUT, envelope, scan, initial selection,
incumbents, bounds, refinement, final selection, allocation). Baselines retain
unchanged scalar pair and pair-AVX2, with equivalent prebuilt layout assumptions.
Record persistent build and bytes separately. Random-ID runs charge gather and
packing; byte_lut_unrolled4 is a separate, not bit-exact, engineering baseline.
Do not silently substitute rebound candidate data for the historical stream.

Record refinements, pruning, interval widths, integer ordering and fixed-tau
excess work. Report workspace vector payload/capacity separately from process
peak RSS and live allocation counts; unavailable measurements stay unavailable.
No continuation/adoption claim without >=10% matched full-path gain, no p95 or
memory-budget regression, and supported GNU/MSVC confirmation. Otherwise retain
the narrow negative outcome. Production/API/default/schema/storage changes,
dependency changes, release, tag and merge are outside this task.

Retain immutable run directories with measured executable, source snapshot,
producer, input hashes, raw samples and SHA-256 manifest *before rebuilding*.
An independent auditor must reject score/order, sample, provenance or decision
corruption. CI runs correctness controls, not the private full-data benchmark.
Final outcomes distinguish IMPLEMENTATION_DEFECT_CONFIRMED,
SIMD_PARITY_ACHIEVED_BUT_NO_GAIN, ALGORITHMIC_BOUND_LIMITATION, CONDITIONAL_WIN,
RESEARCH_QUALIFIED and NOT_EXECUTED. These were not measured at preregistration;
the executed disposition is recorded below.

### Corrected-source independent confirmatory execution

One full GNU confirmation ran on unchanged corrected source `34a24d2c` using
the same frozen inputs, eight queries, one warmup and five repeats. The raw
720 samples independently pass all 17 validator mutation controls, input and
retained executable/source/producer hashes. All six decisions remain
`KEEP_CURRENT`, with identical exact-refinement counts to the historical run.
This new measurement is not rebound to the old source or old executable.

| N | K | Pair-AVX2 p50/p95 ms | Corrected v1 p50/p95 ms |
| ---: | ---: | ---: | ---: |
| 4096 | 16 | 0.2755 / 0.3460 | 1.9816 / 2.2583 |
| 4096 | 128 | 0.2792 / 0.3571 | 1.9713 / 2.1406 |
| 65536 | 16 | 4.1403 / 4.4067 | 24.1969 / 27.7988 |
| 65536 | 128 | 4.1139 / 4.4642 | 26.9695 / 27.9574 |
| 1000000 | 16 | 67.1640 / 70.7369 | 269.2313 / 409.1382 |
| 1000000 | 128 | 66.3550 / 69.1184 | 393.8744 / 430.8196 |

Canonical retained package:
`.artifacts/r6-adc-efficiency/confirmatory-34a24d2c-gnu-v1/`.
Raw SHA-256: `718ec34bdf43ec06b155d02ba1bdbe64dc7ebcaad607cdc7cf76589a250834ec`;
manifest SHA-256:
`0dd1cd7bc10fb9d0a2c7d95eb45b1228568d759a423c0b3ee52fda37978ab0e3`.
The measured binary is retained as `measured.exe`, unlike the historical v1
binary. Replay with `audit-adc-efficiency.py --directory <package>
--legacy-confirm --verify-inputs --mutation-self-test`. Applied quality remains
`NOT_EXECUTED`; public full source-data replay remains `NOT_ESTABLISHED`.

### Combined and candidate-local confirmation boundary

The isolated GNU/MSVC packages on `155cf08f` are immutable observations, not
the final combined result. A0 calls the historical function while A1--A6 use
a common template scaffold. Add an uninstrumented `A0_template_control`
with mask zero before attributing differences to individual mechanisms; parity
of results alone does not establish parity of scaffold cost. Replay the fixed
dense matrix with this control and exactly one evidence-selected combination.
Do not choose another combination after observing its measured outcome.

The available rebound stream permits a **new** candidate-local comparison,
not a replay of the missing historical FastScan stream. Frozen inputs are
`candidate-canonical-flat.bin` (76,208,200 bytes, SHA-256
`b77d29f0435faf581576581ca7ccb812700ea87fe831610cae595fbdadb46096`)
and `candidate-canonical.raw.json` (37,898 bytes, SHA-256
`4e98f84c39a617c36dd5e749a2165d5a1e187ee2a2039d96d748cdce32a0ada7`),
under `.artifacts/legacy/canonical-thq4-rebound-candidate-v1/` in the
canonical checkout. Records are little-endian int32 ID followed by 96 ordinal
code bytes. Counts in the 152 ordered raw rows define group boundaries;
group i maps to query row i in the existing bound 152x384 FP32 query matrix.
Before timing, verify all groups for unique/in-range IDs and every payload
against the global canonical code table. Never use adjacent rebound payloads
as a free substitute for random gather.

Measure the exposed first eight groups only, K=16/128, one warmup and five
repeats, with the same seed/order/quantile policy as dense. Every arm validates
the untrusted query list, gathers codes from the global 1M table and packs any
required local layout **inside** the full-query stopwatch. A trusted-ID arm
may use an owned validated list only after charging its construction to this
query. Record validation/gather/packing and query workspace capacities. Use
scalar pair, pair-AVX2, original A0, zero-template control, the one selected
combined arm, and `byte_lut_unrolled4`. Compare bitwise ordered IDs/scores of
pair-contract arms with the independent scalar reference; byte LUT has a
different FP32 summation order and is checked against its own exhaustive
reference, never certified as pair-bit-exact. Record its quality status as
NOT_EXECUTED. Candidate-local decisions remain separate from six dense profiles.
Malformed offsets/counts/IDs/payloads and score/order/accounting corruption must
fail closed. No new query tuning, training, production change or merge follows.
The new candidate-local path must own one heap `QueryTables` object per call
in every arm: MSVC materialized multiple large stack temporaries in the first,
unmeasured conditional-return implementation. Charge allocation, construction
and destruction inside the full-query timer. Report its 135,984-byte object
payload separately from vector capacities, and include it in the owned-payload
budget comparison. No table cache, increased stack reserve or historical scorer
change is authorized by this correction.

The one combination is now fixed as A1+A2+A3+A4+A5, mask 31, for both compilers.
Selection uses the isolated `155cf08f` packages: A1/A2/A5 have lower p50 than A0
in all twelve compiler/profile comparisons. A3 is retained for its established
construction-time trust boundary and removal of the 4 MB ID scratch at 1M,
not as a universal latency improvement (MSVC 1M/K128 p50 regressed 5.2%).
A4 removes the full-N index arrays and meets the vector-payload budget;
its isolated 1M payload is 10,000,256/10,002,048 bytes at K16/128 versus
26,000,000 for A0. A3+A4 together should be checked, not assumed, at
6,000,256/6,002,048 bytes versus the exhaustive control's 12,000,000.
A6 is excluded: every MSVC profile has higher p50 than A0, and its reduced
logical work does not establish a full-path gain. This is not a conclusion
about every dynamic-bound policy. Do not add the intrusive stage reductions
or multiply isolated speedups to predict the combination.

### Isolated implementation evidence (before combined confirmation)

Both isolated packages were executed on `155cf08f22186e44d52adcbf800b9bd3861d7a1f`
(C++ SHA-256 `102925a22ba637f1550738c9a115c569144e683ba0965317a4dacd7278adef31`).
Per compiler: 2,160 uninstrumented rows, 240 matched trials, 432 warmup
executions and 336 separate diagnostic rows. All 8,557,056 source score,
integer and enclosure comparisons passed. Native controls passed 363 fixtures,
1,420 integer comparisons, 538,572 cutoff comparisons over all 48,961 Q values,
20 invalid-input, three corruption, three FP-environment, four signed-zero
and one independent-reader controls. Focused CTest passed 2/2 per compiler.
These counts describe this source, not a later extension.

Observed p50 ratios to original A0 follow (GNU / MSVC). These are implementation
comparisons; the common-scaffold control was not present on this source.

| N / K | A1 | A2 | A3 | A4 | A5 | A6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4096 / 16 | .754 / .644 | .921 / .901 | .970 / .953 | .969 / .944 | .689 / .788 | .990 / 1.009 |
| 4096 / 128 | .757 / .657 | .932 / .913 | .976 / .964 | .999 / .980 | .693 / .765 | .994 / 1.016 |
| 65536 / 16 | .667 / .600 | .866 / .906 | .960 / .997 | .933 / .955 | .690 / .803 | .962 / 1.028 |
| 65536 / 128 | .714 / .621 | .902 / .906 | .966 / .987 | .957 / .961 | .657 / .765 | .996 / 1.004 |
| 1M / 16 | .519 / .444 | .798 / .933 | .942 / .988 | .965 / .955 | .948 / .955 | 1.031 / 1.055 |
| 1M / 128 | .622 / .568 | .850 / .906 | .874 / 1.052 | .938 / .914 | .705 / .834 | .961 / 1.009 |

Canonical package locators are
`.artifacts/r6-adc-efficiency/isolated-155cf08f-gnu-v1/` and
`.artifacts/r6-adc-efficiency/isolated-155cf08f-msvc-v1/`.
Manifest SHA-256, respectively:
`572b8f3f8f9a3bab385250b0dc774c41ff72faf20edc8ebafb7f07472577048a`,
`6e50d027685124975c7ca6cef3b3fa3171f07cbc84f8f6bc4a59281c34fe4c20`.
Each retains actual measured executable, source, producer, raw, diagnostics,
compiler metadata and auditor dependency. Original integrity checks passed
32 rejection controls; the later stricter auditor also binds per-row physical
work and vector capacity to their reference cases. No isolated arm passes the
complete baseline gate; that is not the yet-unmeasured combined decision.

### Executed combined confirmation: implementation overhead confirmed, keep current

Measured source is `04b466d41193df3925bc21068d6432c2f5342840`, not the later
documentation HEAD. C++ SHA-256:
`49e0f2dc9fe4dff174b803a5c8963844fef95f55b8c043eafb5acfb30ad0f682`.
The historical scoring/certificate/benchmark region, from `make_tables` up to
the corrective arms, matches corrected #677 exactly: 53,573 normalized
characters. Historical #677 receipts have not been replaced.

GNU 15.2 and MSVC 19.44 Release runs used the same fixed mask31, exposed eight
queries, five repeats and one warmup. Each dense package contains 2,640 raw
rows, 240 matched trials, 528 warmup executions and 432 separate intrusive
diagnostic rows. All 8,557,056 pair-score, integer-score and enclosure checks
passed per package; 528 arm/reference cases passed. Correctness controls per
compiler pass 443 dense and 96 candidate fixtures, 17 malformed-candidate,
1,420 integer-score, 538,572 cutoff comparisons covering every Q=0..48960,
20 invalid-input, three corruption, three FP-environment, four signed-zero
and one independent-reader controls. Focused CTest passes 4/4 per compiler.
Dense validator-only self-test passes 75 rejection controls across both schema
matrices; auditing each actual confirmation package passes 39 mutations.

Full uninstrumented dense times follow. AVX2 is the faster exhaustive pair
control in every profile. Each p50/p95 aggregates 40 samples, not 40 independent
queries. Quantiles are descriptive, without stable-tail or significance claims.

| Compiler | N / K | Pair-AVX2 p50 / p95 ms | A0 / zero-template p50 ms | Combined p50 / p95 ms | Combined / AVX2 p50 / p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| GNU | 4096 / 16 | .2711 / .3030 | 1.9139 / 1.9027 | .5889 / .6403 | 2.172 / 2.113 |
| GNU | 4096 / 128 | .2748 / .2933 | 1.9958 / 1.9750 | .7176 / .7942 | 2.611 / 2.708 |
| GNU | 65536 / 16 | 4.2225 / 4.5821 | 24.9554 / 24.4036 | 5.2132 / 6.2873 | 1.235 / 1.372 |
| GNU | 65536 / 128 | 4.1830 / 4.5157 | 26.6252 / 26.4948 | 5.4411 / 6.4124 | 1.301 / 1.420 |
| GNU | 1M / 16 | 68.0171 / 70.5531 | 255.4329 / 252.2836 | 61.1646 / 90.3058 | .899 / 1.280 |
| GNU | 1M / 128 | 67.2718 / 69.7217 | 383.1919 / 359.2316 | 76.6243 / 87.7638 | 1.139 / 1.259 |
| MSVC | 4096 / 16 | .3694 / .4147 | 2.3515 / 2.4234 | .6898 / .7871 | 1.867 / 1.898 |
| MSVC | 4096 / 128 | .3764 / .4103 | 2.4164 / 2.4600 | .8108 / .8488 | 2.154 / 2.069 |
| MSVC | 65536 / 16 | 4.9463 / 5.3615 | 31.3746 / 32.2642 | 5.4651 / 6.4785 | 1.105 / 1.208 |
| MSVC | 65536 / 128 | 4.9470 / 5.3561 | 33.2932 / 34.1267 | 5.6835 / 6.7631 | 1.149 / 1.263 |
| MSVC | 1M / 16 | 75.8418 / 80.9580 | 352.2131 / 350.8917 | 80.0153 / 96.4939 | 1.055 / 1.192 |
| MSVC | 1M / 128 | 76.3758 / 80.8437 | 463.9078 / 460.8900 | 80.3241 / 95.6652 | 1.052 / 1.183 |

The combination reduces A0 median time by a factor of 2.78--5.86 across the
twelve comparisons. Thus `IMPLEMENTATION_DEFECT_CONFIRMED` is supported for
the scalar implementation overhead. But no profile meets the declared complete
gate. GNU 1M/K16 just meets the 10% median threshold, then fails descriptive
p95 (+28.0%) and MSVC confirmation. Every dense profile remains `KEEP_CURRENT`.
The qualified-gain disposition is `SIMD_PARITY_ACHIEVED_BUT_NO_GAIN`: exact
SIMD parity is achieved, but not the required cross-compiler/full-path gain;
this label does not deny the isolated GNU median improvement. No second
combination or rescue sweep was selected.

### What costs changed, and what the certificate still requires

The ten stages below are means across eight queries at 1M, shown K16 / K128
in ms. Complete per-profile/per-query stages are retained in each diagnostic
package. These are separate intrusive invocations, not decompositions of the
uninstrumented medians above. In particular, diagnostic `A0_original` and
`A0_template_control` both use the instrumented **zero-mask template scaffold**;
neither is direct instrumentation of the unchanged historical function. Its
actual uninstrumented A0 cost is retained separately above. Do not add stage
speedups, subtract these means from full-query medians, or infer causal isolated
contributions from the original nine-arm matrix without its scaffold control.

| Diagnostic stage | GNU zero mask | GNU combined | MSVC zero mask | MSVC combined |
| --- | ---: | ---: | ---: | ---: |
| ID validation/copy/sort | 15.032 / 14.791 | <.001 / <.001 | 15.724 / 16.006 | <.001 / <.001 |
| Query LUT | .105 / .101 | .103 / .101 | .126 / .129 | .155 / .130 |
| Error envelope | .229 / .200 | .227 / .218 | .260 / .263 | .281 / .270 |
| Integer scan | 132.636 / 133.007 | 15.101 / 14.775 | 213.909 / 213.992 | 13.007 / 12.777 |
| Initial selection | 13.829 / 13.964 | 1.886 / 2.002 | 20.099 / 20.123 | 2.320 / 2.375 |
| Exact incumbents | .008 / .049 | .008 / .049 | .009 / .057 | .008 / .050 |
| Bound checks | 89.482 / 89.772 | 3.665 / 3.768 | 72.407 / 71.010 | 7.027 / 6.207 |
| Exact refinement | 107.709 / 183.587 | 49.006 / 52.880 | 99.717 / 165.990 | 48.527 / 54.977 |
| Final selection | 10.795 / 14.410 | 2.325 / 2.546 | 16.666 / 20.072 | 2.757 / 2.831 |
| Buffer allocation | 1.740 / 2.039 | .458 / .426 | 1.808 / 1.907 | .361 / .362 |

Logical exact work and physical FP32 score evaluations are distinct. SIMD
computes lanes that do not require logical refinement; seeded scores may be
recomputed in their block. Fractions below are means over the eight queries
and match on both compilers. The final-tau column is an untimed *work-only*
counterfactual using the exact final incumbent with the same certificate,
not an executed faster algorithm or latency bound.

| N / K | Fixed logical exact % | Combined physical exact % | Dynamic logical exact % | Ideal final-tau screened % | Integer top-K overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4096 / 16 | 83.328 | 87.253 | 82.782 | 82.355 | .8594 |
| 4096 / 128 | 99.857 | 103.064 | 99.741 | 99.667 | .8691 |
| 65536 / 16 | 67.004 | 75.991 | 64.657 | 63.791 | .8203 |
| 65536 / 128 | 87.117 | 94.158 | 84.494 | 83.337 | .8506 |
| 1M / 16 | 39.007 | 49.825 | 36.485 | 34.848 | .8125 |
| 1M / 128 | 67.576 | 77.268 | 60.740 | 59.635 | .8223 |

At 1M the fixed-minus-dynamic differences are 25,223.75 / 68,366 mean
evaluations, not the complete potential saving from an oracle-final tau.
The corresponding fixed-minus-ideal differences are 41,596 / 79,415.5.
Each query's envelope width is effectively constant at the displayed precision
over documents in this global quantization model; outward floating-point
rounding can still change the last bits. Across the eight queries, mean p50
and p95 widths are both .2355883472. Individual widths and maxima remain in
`selectivity`.
Cheaper screening alone cannot make these bounds discard all but K scores:
even the final-tau work counterfactual retains 34.85% / 59.63% at 1M and
99.67% at 4096/K128. The latter is an `ALGORITHMIC_BOUND_LIMITATION` in this
profile, not a rejection of all certificate methods. No theoretical latency
speedup is asserted from logical counts: physical lanes, selection, validation
and preparation remain real work. The >=90% near-exhaustive stop applies.

### Dense preparation and memory

At 1M, A0 has 26,000,000 vector-capacity bytes, exhaustive pair 12,000,000,
and combined 6,000,256 / 6,002,048 at K16/K128. This is query vector payload,
not allocation traffic, peak live allocations, stack use or process RSS.
The same prebuilt profile retains 96,000,000 bytes each for prefix codes,
plane-major and block32 layouts, plus 4,000,000 ID bytes (292 MB total),
in addition to the shared source corpus. Plane-major is retained by the common
harness; these are actual retained capacities, not a minimal serving layout.
At 1M, prefix-copy / trusted-ID-build / layout-build costs are
29.909 / 15.018 / 206.999 ms on GNU and 32.979 / 17.800 / 217.499 ms on MSVC.
They are outside every dense search timer, not free in an end-to-end build.
One cold combined query0 takes 89.537 / 77.617 ms on GNU and
96.992 / 83.308 ms on MSVC for K16/K128. These single query0 observations are
not estimates of cold overhead relative to an eight-query steady distribution.
Peak RSS and peak live allocation counts remain `NOT_MEASURED`.

### Executed new rebound candidate-local comparison

Both compilers complete 480 rows, 80 matched trials, 96 warmup executions,
16 query/K references and 96 arm/reference cases. Before timing they verify all
152 groups / 762,082 records against canonical codes, then all 40,085 exposed
candidate pair scores, integer scores, byte-reference scores and enclosures.
Every timed query revalidates untrusted IDs, gathers from the global 1M corpus,
packs block32 where required and constructs/frees its owned table. Workspace
is not reused. Native malformed-input controls and the independent auditor's
42 rejection controls pass on both actual packages, including coordinated
scratch underreporting and omitted packing. This is **not** historical R4 replay.

| Compiler / K | Pair-AVX2 p50 / p95 ms | A0 / zero-template p50 ms | Combined p50 / p95 ms | Byte LUT p50 / p95 ms |
| --- | ---: | ---: | ---: | ---: |
| GNU / 16 | 1.7509 / 1.8515 | 3.2326 / 3.1435 | 2.1058 / 2.2296 | 1.2947 / 1.4052 |
| GNU / 128 | 1.7357 / 2.7460 | 3.3453 / 3.4009 | 2.2338 / 3.4185 | 1.2865 / 1.8070 |
| MSVC / 16 | 1.8267 / 1.9392 | 3.7497 / 3.8442 | 2.2226 / 2.3947 | 1.3744 / 1.4753 |
| MSVC / 128 | 1.6756 / 1.9173 | 3.8443 / 3.8094 | 2.1758 / 2.4096 | 1.2346 / 1.3848 |

Combined loses to the faster pair control by 20.3--29.9% at p50 and
20.4--25.7% at p95; both K decisions are `KEEP_CURRENT` on both compilers.
Mean logical exact fractions are 81.267% / 95.156%, and physical evaluations
4,681.125 / 5,077.75 against 5,010.625 mean candidates. K128 is near-exhaustive.
The exhaustive block32 control scores 5,024 physical lanes, not 5,010.625.
Maximum combined owned payload is 1,150,984 / 1,152,776 bytes versus 1,180,868
for pair-AVX2: this includes preparation vectors, score/selector vectors and
one 135,984-byte table, not allocator overhead or transient peak live storage.
The tested byte engineering control uses at most 698,560 owned-payload bytes
and is faster here, but has a different FP32 summation contract and its own
independent exhaustive coordinate oracle. It is **not** pair-bit-exact or an
applied-quality winner. Its tested factory still constructs an unused pair
LUT; these numbers do not describe an optimal byte-only preparation path.

### Immutable execution receipts and closure

All packages below are under canonical `.artifacts/r6-adc-efficiency/`.
Each retains measured executable, source, producer, auditors/dependencies,
compiler metadata, correctness JSON and raw results. Dense also retains the
separate diagnostic run. Manifest hashes bind those files, not just a build
path that can later be overwritten.

| Package | Manifest SHA-256 | Raw SHA-256 |
| --- | --- | --- |
| `combined-04b466d4-gnu-v1` | `593be46c68ab3da396a7d2c01cdf3338af31f63c226b676b0964530776b7b3aa` | `989d0303379687fc4649cdb09e52fabbbc4177ecea8fd8c8afbb09601e55a863` |
| `combined-04b466d4-msvc-v1` | `03b0cd48cfc6856270468195b811e2e41fc9ea1831db467bef2b3ec94e4447fb` | `e9fad3c45ec89e454874df38f25d8c5b55b28e47bd3dc12f578979ab3223e72b` |
| `candidates-04b466d4-gnu-v1` | `d460c58d2ba610e82fe0eedb37f50fb3789e4fb29dfe8c587a0803fbb8d94007` | `058871a39325378566d58462639f64197ea8bd1e129009c23764079a5c142f4c` |
| `candidates-04b466d4-msvc-v1` | `4a2b047944fda9c977da5e4062691ede9baf73b7e13e934927273f356fbe1294` | `a0343bb2eac8b87336e5dd10032f80dfa801b82ca6208fe9673baa8a86ab18f1` |

Retained GNU executable SHA-256:
`8475d1db4b6d2c0ffda6d5336d4746d272f099b65c1e6533e6db97f759c60983`;
MSVC: `21caf1ccd9a2e01092c82697bcc45d8da31956f5111d116d3300d0e93a09c60a`.
Each compiler's same binary executes dense and candidate modes.
Audit with `audit-adc-efficiency.py` or `audit-adc-candidates.py
--directory <package> --verify-inputs --repository <canonical-checkout>
--expected-source 04b466d41193df3925bc21068d6432c2f5342840
--mutation-self-test`; these actual commands return zero on all four packages.

Measured-source CI [44779565582](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44779565582)
and packaging [44779565581](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44779565581)
pass. They execute correctness controls, not private full-data timings.
Evidence remains local, `PUBLIC_FULL_REPLAY_NOT_ESTABLISHED`, applied quality
`NOT_EXECUTED`; neither eight exposed queries nor repeated samples establish a
new quality/Pareto curve. Close this bounded implementation-efficiency pass
with no adoption and no additional rescue combination. The next independent
research is complete-search MultiProbe deduplication after its own preregistered
contract and controls; K8/IVF stay `NOT_EXECUTED` for qualified missing/invalid
prerequisites. No R6 merge, production change or first-agent storage work follows.
