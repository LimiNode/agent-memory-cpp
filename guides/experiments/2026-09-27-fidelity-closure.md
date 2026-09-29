# 2026-09-27 codec fidelity closure

This note defines the final source-bound closure wave for the compact residual
families. It is a protocol, not a production selection claim. All arms must
use the same canonical 1M bundle, the 152-query candidate stream, cosine
scoring, and the persisted THQ4 top-128 shell.

## Required controls

| family | control | required evidence |
| --- | --- | --- |
| LSQ | Faiss `LocalSearchQuantizer` at 25/train-ILS8 and (only if improving) 50/train-ILS8; encode-ILS16 vs encode-ILS32 sensitivity | fit/encode time, nDCG@10, candidate overlap, codebook and seed hashes; repeat any improving setting on independent seeds; label 25→50 as training-budget/annealing-schedule sensitivity |
| PLSQ | Faiss `ProductLocalSearchQuantizer` practical controls `PLSQ8x4x8` and `PLSQ8x6x8` | same source-bound quality/storage audit; bounded control for the dense-solve LSQ ceiling |
| PQ/OPQ | official Faiss `ProductQuantizer` and `OPQMatrix` on the same residual/domain split | Faiss version, native module hash, compile options, exact `niter`, `niter_pq`, k-means iterations, rotation/codebook hashes, independent decode/ADC replay |
| RSLM | source-grounded RSLM1/2/3/4 | source revision, exact tables and transform hashes, packed-code replay, matched side bytes |
| QINCo2 | official upstream architecture and preset, with a convergence/occupancy sweep | upstream revision/license, immutable training plan, checkpoint and code hashes, validation trace, occupancy/entropy, persisted-code decode audit |
| TurboQuant | Qdrant Rust parity on frozen vectors; optional TQ1.5 frontier point | Rust revision and build, rotation/packed-symbol/scale hashes, decoded-vector and asymmetric-score tolerances; TQ1.5 is exploratory and cannot block the parity gate |

The QINCo2 gate is not satisfied by a short A16/B32 pilot. The closure run
must compare the existing A16/B32 checkpoint against the paper-scale A32/B64
evaluation beam, record occupancy and entropy after residual-quantizer
initialization and after every epoch, and continue beyond the three-epoch
learning-rate ramp. Source-faithful and paper-faithful controls are separate
arms; the receipt records the codebook-noise initialization (`0.1` versus the
paper control `0.025`) and the scheduler LR-floor variant. If an official
pretrained checkpoint can be materialized, it is an additional sanity arm, not
a substitute for the matched training control.

The current M16 convergence attempt emitted a checkpoint after four completed
epochs (checkpoint epoch field 5): validation MSE improved to `0.0425441`, but
4025/4096 codewords were reset and most later stages still have zero entropy.
The run was stopped at this stable collapse diagnostic rather than spending
another hour per epoch on the same under-occupied arm. The values are recorded
in `2026-09-27-qinco2-convergence-diagnostic.result.json`. The subsequent
epoch-4 persisted-code replay and structural audit are recorded in
`2026-09-28-qinco2-epoch4-replay.result.json`; the checkpoint is
residual-trained, so `train_residual -> eval_residual` is the matched bounded
control and `train_residual -> eval_raw` is the reverse-domain diagnostic.
Neither is a QINCo2 family or production claim.

The corrected persisted replay is `BOUNDED_MATCHED_DOMAIN_CONTROL`: M16/256
uses 20 B code-plus-norm side payload (`116 B` with THQ4), reaches mean qrels
nDCG@10 `0.6422150225` on `train_residual -> eval_residual`, and reports
`0.0627454353` for the reverse `train_residual -> eval_raw` diagnostic. The
result is bound to training-plan SHA
`f8413b5dc6cdd1894516f55bf9124816ce72a8dd89b08bf49ffdd1a5ae2081ff`, has
checkpoint field `5` / four completed epochs, and its persisted-code audit
passes. These are bounded controls, not convergence or production evidence.

The official implementation also passes a bounded synthetic A32/B64 inference
smoke (`2026-09-28-qinco2-a32b64-smoke.result.json`), including deterministic
codes and `decode(codes)` parity. This verifies that the wider paper-style beam
is accepted by the source API; it is not a trained-corpus A32/B64 quality run.

RSLM1 uses the official 4D `C4D` codebook: 96 four-dimensional symbols are
packed as 48 bytes, followed by the 2-byte inner UE7M9 scale. In relative mode
the full-vector reconstruction scale adds another 2 bytes, so the residual
codec side is 52 B and the THQ4 cascade total is 148 B. RSLM1 is included in
the same source-bound materializer and sample replay audit as RSLM2/3/4; its
presence does not by itself provide native latency or production-selection
evidence.

The source-bound RSLM quality replay now includes the missing RSLM1 arm
(`2026-09-28-rslm-faithful-quality.result.json`): `0.657398` IP nDCG@10 and
`0.658220` cosine nDCG@10 at `52 B` side payload (`148 B` including THQ4).
The full 152-query source/audit contract passes.

For the direct cosine serving contract, LSQ byte accounting includes the FP32
final-norm sidecar: LSQ32 is `32 B` code + `4 B` norm = `36 B` side and `132 B`
with THQ4; LSQ48 is `48 B` code + `4 B` norm = `52 B` side and `148 B` with
THQ4. The latter is therefore the same side budget as faithful RSLM1, not a
48-byte total payload.

The source-bound PLSQ controls use 25,000 residual training rows, eight Faiss
threads, and the bounded `25/train-ILS8` schedule. PLSQ8x4x8 reached mean
qrels nDCG@10 `0.649133` at `36 B` side (`132 B` including THQ4), while
PLSQ8x6x8 reached `0.656438` at `52 B` side (`148 B` including THQ4). The
latter is close enough to the measured historical frontier to enter the fresh
evaluation shortlist; excluding it without running it would have been
unjustified. Both runners persist THQ centroids and every subquantizer's
codebooks and offsets. The independent audit manually performs additive PLSQ
decode, THQ-base reconstruction, cosine scoring, and stable top-10 ranking;
both profiles pass all 152 rows with zero ranking mismatches. Full details are
recorded in `2026-09-29-plsq-practical-control.result.json` and the replay
artifacts. Large model/code files are externalized under the workspace recorded
in `2026-09-29-fidelity-artifact-manifest.json`; result/audit SHA-256 values are
the binding identity. These are bounded historical-152 controls, not product
selection.

The compact receipts now expose exact model accounting rather than charging
only per-document codes and norms:

| arm | codec model bytes | shared THQ base bytes | effective 1M bytes/doc |
| --- | ---: | ---: | ---: |
| LSQ32 (seed 20260921) | 12,583,176 | 6,144 | 48.589320 |
| PLSQ8x4x8 | 1,573,184 | 6,144 | 37.579328 |
| PLSQ8x6x8 | 2,359,744 | 6,144 | 54.365888 |

The historical LSQ48 receipt remains externalized; its exact model artifact
must be materialized and bound before quoting an effective footprint.

## Fail-closed interpretation

The existing LSQ multi-seed artifacts are source-bound and audit-PASS, but they
use the default 25-iteration Faiss fit and therefore remain a strong bounded
control rather than a paper-converged closure. Existing handwritten OPQ runs
must not support a family-level negative conclusion until the official Faiss
control is persisted. Under-converged or collapsed QINCo2 runs are diagnostic
only and do not establish that the family is dominated.

The first heavy LSQ extended-budget attempt used an excessive schedule
(`train_iters=50`, `train_ils_iters=32`, `encode_ils_iters=32`) and is recorded
as `BLOCKED_EXCESSIVE_SCHEDULE_COST`; it is not the intended convergence gate.
The 50-iteration LSQ32 fit took `5612.71 s` and reached mean qrels nDCG@10
`0.657163`, but the previously cited 25-iteration result used seed `20260925`
while this probe used `20260921`. The old `-0.003051` delta therefore mixes
seed variance with iteration count and is superseded as a convergence claim.
A source-bound 25/train-ILS8 control with the same seed, source split, and
encode-ILS16 schedule was then executed and independently audited; the paired
decision is recorded in `2026-09-29-lsq-paired-convergence.result.json`; its
source-bound bootstrap receipt is
`2026-09-29-lsq-paired-training-budget.bootstrap.json`. Because Faiss
recomputes its annealing schedule from `train_iters`, 25→50 is
training-budget/annealing-schedule sensitivity, not a continuation trajectory.
LSQ32/48 have dense `M*K` codebook solves (8192/12288 rows; roughly
512 MiB/1.125 GiB double Gram matrices before workspace), so the canonical fit
is a memory-bandwidth and cache/NUMA-sensitive workload, not a simple logical
thread-count benchmark. The current M8 smoke only demonstrates that
oversubscription can hurt a small workload; it does not establish LSQ32/48
scaling or that 36 threads are intrinsically slower.

The plumbing itself was checked separately on a bounded synthetic matrix:
`2026-09-28-lsq-synthetic-smoke.result.json` trains a two-stage 16-dimensional
Faiss LSQ, encodes 32 rows, and independently reconstructs the additive
codebook sum. It passes in `0.377 s` with the reduced smoke budget. This
isolates the failure mode of the large runs: the implementation and code
layout work, while the canonical 32/48-byte fit is CPU-bound by the product of
25k rows, 32--48 stages, and large ILS/ICM iteration budgets. The smoke is a
regression check only and does not satisfy the deferred true LSQ convergence
ceiling gate.

The same bounded smoke, on a Xeon E5-2696 v3 (18 physical / 36 logical CPUs),
measured `4096 x 128`, eight stages at `2.30 s` (one thread), `0.80 s` (four),
`0.79 s` (sixteen), and `2.87 s` (thirty-six). This is a diagnostic only: the
runner did not record full BLAS backend, affinity, NUMA, repeat, or
`threadpoolctl` provenance, so it must not be extrapolated to canonical LSQ32/48.

## Official Faiss OPQ/PQ control (corrected replay)

The first heavy fit used a strong custom schedule (`niter=50`, `niter_pq=40`,
`niter_pq_0=40`, PQ k-means `40`), but its scorer used the wrong row-major
orientation for Faiss `LinearTransform`. That result is superseded and must
not be quoted. The persisted fit was retained and replayed with the corrected
orientation (`residual @ A.T`, inverse `decoded @ A`). The source-bound
candidate-local result is:

| arm | payload | mean qrels nDCG@10 | candidate union |
| --- | ---: | ---: | ---: |
| strong Faiss OPQ32x4 (`50/40/40`) | 16 B | 0.6482593499 | 18,362 |

Result SHA-256 is
`a1b41e4f449ef37262a67c707a0342bf8ea4db230ece903d587e4db9d434dcb5` and
model SHA-256 is
`d211a7e2b5fd30608d7075889eea31451723326cf5abfd48201664b5ee2464a7`.
The old result SHA `2f8604ed...` and quality `.6616096795` are
`SUPERSEDED_INVALID_REPLAY` because of that orientation bug. The corrected
result is still a fidelity control, not a production claim: it is
candidate-local and has no native latency/page evidence. The independent
audit now replays THQ candidate selection, Faiss `LinearTransform`, Faiss PQ
assignment/decode, reconstructed vectors, cosine scores, deterministic top-10
and qrels nDCG for all 152 rows; it passes.

## TurboQuant source-bound replay

The existing Python TurboQuant reference was also replayed on the same
canonical split against the Qdrant source revision
`6ab21cac18ebb6f4ae29102c7f8f5cc11affd5de`. It produced mean nDCG@10 of
`0.6591756211` for TQ1 (52 B side including the final norm) and
`0.6562538770` for TQ2 (100 B side), over 18,362 unique candidate documents.
Result SHA-256 is
`fad8ce7642389e678b223f8e635b46f2e81a4fd99abeb0ecff9379860c7e9f83`.
This closes the source-bound Python control only. A direct Qdrant Rust replay
was then run from the same upstream revision (`6ab21cac18ebb6f4ae29102c7f8f5cc11affd5de`)
on the frozen 18,362-row residual payload. Rust normal-mode Dot-compatible
encode/decode matched the Python control within `7.46e-9` maximum absolute
error for both TQ1 and TQ2. The compact parity result is recorded in
`2026-09-27-turboquant-rust-parity.result.json`; the independent audit is
`rust-tq-parity.audit.json` in the external replay workspace. This is a
decode-fidelity gate, not a native latency claim, and does not cover TQ+ shift/
scale correction.
Packed wire bytes, extras/scale sidecars, asymmetric score parity, and Rust
harness/build hashes remain explicitly pending; decoded-vector parity must not
be read as full serving-wire parity.

## Final serving gate

After the controls converge, freeze only Pareto finalists and run one fresh
row-aligned native benchmark over all 1,000,000 documents. The serving receipt
must bind payload IDs, payload/result hashes, source hashes, build manifest,
warm/cold protocol, p50/p95/p99, page counters, and exact top-10 parity. CI
self-tests alone are not numeric replay evidence.

An executed warm-process preparation gate exists for the frozen THQ4 top-128
rows (`2026-09-27-native-serving-top128.result.json`). A corrected second
executed gate now runs the complete frozen R4 candidate stream through native THQ4 top-128
selection and reranks the full 463,258-document union for the payloads that
are fully materialized (`2026-09-28-native-full-candidate-finalists-v2.result.json`).
`joint2`, faithful `RSLM3`, and faithful `RSLM4` all have exact THQ top-128
set parity, an ordered-parity diagnostic, and exact final ordered top-10 parity
after independent decoded-payload replay over all 152 queries. RSLM relative
payload accounting is `148 B` and `196 B`
respectively (including inner and outer UE7M9 scales). The native warm-process
total p50/p95/p99 values are recorded for orchestration only; their tail values
are not treated as codec-comparable because scheduler/cache state dominates the
predecoded path.

This is the full-candidate native orchestration/predecoded-rerank gate for those
finalists, not a compressed-decode or cold/page-fault benchmark. LSQ/TurboQuant/BBQ remain
separate gates and are not substituted by these payloads; their full-union
serving rows are added only after their own source-bound artifacts exist.
