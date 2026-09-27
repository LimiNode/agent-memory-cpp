# 2026-09-27 codec fidelity closure

This note defines the final source-bound closure wave for the compact residual
families. It is a protocol, not a production selection claim. All arms must
use the same canonical 1M bundle, the 152-query candidate stream, cosine
scoring, and the persisted THQ4 top-128 shell.

## Required controls

| family | control | required evidence |
| --- | --- | --- |
| LSQ | Faiss `LocalSearchQuantizer` at 25/16, 50/32, and 100/32 fit budgets | fit/encode time, nDCG@10, candidate overlap, codebook and seed hashes; repeat any improving setting on independent seeds |
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
in `2026-09-27-qinco2-convergence-diagnostic.result.json`; a persisted-code
replay remains pending, so no QINCo2 quality or family claim is made.

RSLM1 uses the official 4D `C4D` codebook: 96 four-dimensional symbols are
packed as 48 bytes, followed by the 2-byte inner UE7M9 scale. In relative mode
the full-vector reconstruction scale adds another 2 bytes, so the residual
codec side is 52 B and the THQ4 cascade total is 148 B. RSLM1 is included in
the same source-bound materializer and sample replay audit as RSLM2/3/4; its
presence does not by itself provide native latency or production-selection
evidence.

## Fail-closed interpretation

The existing LSQ multi-seed artifacts are source-bound and audit-PASS, but they
use the default 25-iteration Faiss fit and therefore remain a strong bounded
control rather than a paper-converged closure. Existing handwritten OPQ runs
must not support a family-level negative conclusion until the official Faiss
control is persisted. Under-converged or collapsed QINCo2 runs are diagnostic
only and do not establish that the family is dominated.

The first convergence attempt (LSQ32, 25k rows, seed `20260927`,
`train_iters=50`, `encode_ils_iters=32`) was stopped before artifact emission
after demonstrating that the full 32-stage fit is batch-scale work on the
available host. A second `50/16` attempt was likewise stopped before artifact
emission. These attempts produce no quality claim and no replay receipt. They
are recorded so a future scheduled batch run cannot be mistaken for missing
work or silently substituted with a compact synthetic result.

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

## Final serving gate

After the controls converge, freeze only Pareto finalists and run one fresh
row-aligned native benchmark over all 1,000,000 documents. The serving receipt
must bind payload IDs, payload/result hashes, source hashes, build manifest,
warm/cold protocol, p50/p95/p99, page counters, and exact top-10 parity. CI
self-tests alone are not numeric replay evidence.

An executed warm-process preparation gate exists for the frozen THQ4 top-128
rows (`2026-09-27-native-serving-top128.result.json`). A second executed gate
now runs the complete frozen R4 candidate stream through native THQ4 top-128
selection and reranks the full 463,258-document union for the payloads that
are fully materialized (`2026-09-27-native-full-candidate-finalists.result.json`).
`joint2`, faithful `RSLM3`, and faithful `RSLM4` all have exact top-10 parity
with an independent Python scorer over all 152 queries. The native warm-process
total p50/p95/p99 values are respectively `2.4459/47.8470/60.7997 ms`,
`3.4313/35.8281/45.6676 ms`, and `3.1543/7.1544/11.1523 ms`.

This is the complete candidate-to-top-128 serving gate for those finalists, not
a compressed-decode or cold/page-fault benchmark. LSQ/TurboQuant/BBQ remain
separate gates and are not substituted by these payloads; their full-union
serving rows are added only after their own source-bound artifacts exist.
