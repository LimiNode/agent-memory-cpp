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

## Official Faiss OPQ/PQ control (executed)

The canonical 152-query split was replayed with Faiss `OPQMatrix(384, 32)`
and 4-bit `ProductQuantizer`, using `niter=50`, `niter_pq=40`,
`niter_pq_0=40`, and PQ k-means `40`. The fitted model was persisted before
the scoring pass and then reloaded for a second deterministic score replay.
The source-bound candidate-local result is:

| arm | payload | mean qrels nDCG@10 | candidate union |
| --- | ---: | ---: | ---: |
| official Faiss OPQ32x4 | 16 B | 0.6616096795 | 18,362 |

Result SHA-256 is
`2f8604ed4017d7f9589ea8995471d312d5eb0064140dede919def75d025ab89d` and
model SHA-256 is
`d211a7e2b5fd30608d7075889eea31451723326cf5abfd48201664b5ee2464a7`.
This is an executed fidelity control, not a production claim: it is
candidate-local, has no native latency/page evidence, and its fit wall time is
not recorded because the first fit completed before the model-replay wrapper
was corrected. The quality result is nevertheless bound to the persisted
rotation/codebooks and exact source hashes.
The independent persisted-model audit is `audit-thq-faiss-opq-control.py` and
passes shape, finiteness, orthogonality, row-cardinality, configuration, and
model-hash checks.

## Final serving gate

After the controls converge, freeze only Pareto finalists and run one fresh
row-aligned native benchmark over all 1,000,000 documents. The serving receipt
must bind payload IDs, payload/result hashes, source hashes, build manifest,
warm/cold protocol, p50/p95/p99, page counters, and exact top-10 parity. CI
self-tests alone are not numeric replay evidence.
