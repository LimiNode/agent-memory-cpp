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

## Final serving gate

After the controls converge, freeze only Pareto finalists and run one fresh
row-aligned native benchmark over all 1,000,000 documents. The serving receipt
must bind payload IDs, payload/result hashes, source hashes, build manifest,
warm/cold protocol, p50/p95/p99, page counters, and exact top-10 parity. CI
self-tests alone are not numeric replay evidence.
