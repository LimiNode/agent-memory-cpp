# Source-bound residual replay: strong PQ, LSQ, and QJL

Date: 2026-09-24  
Status: `EXECUTED` for the arms listed below; no production selection.

The canonical DE-1M E5 bundle was recovered and validated.  The old 152-query
fused stream was not trusted as a THQ payload: its document IDs and row
boundaries were preserved, while a separate materializer rebound every record
to the canonical 96-byte ordinal THQ4 table.  The resulting 100-byte records,
raw manifest, and receipt are retained outside Git under
`E:\_repoz\agent-memory-workspaces\canonical-thq4-rebound-candidate-v1`.

All rows share the same frozen candidate shell, canonical THQ interval²
top-128, cosine metric, and 152-query qrels split.  TQ1-based residual arms
also bind the canonical TQ1 payload; standalone THQ/LSQ and TQ+ rows do not
use that payload.  The TQ1 baseline is independently regenerated in the same run (`mean nDCG@10 =
0.659176`).

The 152-query split is explicitly bound to the recovered 305-query source by
`2026-09-24-canonical-query-lineage.receipt.json` (exact vector equality,
persisted row order, and 1,557 qrels matches). The receipt does not establish
that the 153-row complement was untouched during earlier research, so these
results remain historical-152 reproduction rather than a blind holdout claim.

## Results

| arm | fit | side bytes/doc | mean nDCG@10 | delta vs TQ1 | audit |
| --- | --- | ---: | ---: | ---: | --- |
| TQ1 | canonical TQ1 | 56 | 0.659176 | 0 | regenerated |
| TQ1 + PQ4 | 25k rows, Faiss Kmeans, 25 iters, 3 restarts | 64 | 0.656730 | -0.002446 | PASS |
| TQ1 + PQ8 | 25k rows, Faiss Kmeans, 25 iters, 3 restarts | 68 | 0.660526 | +0.001351 | PASS |
| THQ25k + LSQ1024-32 | Faiss LSQ, frozen 25k-row THQ centroids; 1,024-row LSQ fit, 1/1/1/1, nperts=1 | 36 (32 + FP32 norm) | 0.655921 | -0.003255 | PASS |
| THQ25k + LSQ1024-48 | Faiss LSQ, frozen 25k-row THQ centroids; 1,024-row LSQ fit, 1/1/1/1, nperts=1 | 52 (48 + FP32 norm) | 0.663288 | +0.004112 | PASS |
| TQ1 + TQ+ | source-bound TurboQuant+ reference (direct) | reference-defined | 0.657829 | -0.001347 | PASS |
| QJL Gaussian m=32/64/128/256/384 | score correction, unit-norm denominator contract | 8/12/20/36/52 (+ global model) | 0.402911/0.508609/0.556806/0.586868/0.614050 | negative | PASS |
| QJL Rademacher m=32/64/128/256/384 | explicit heuristic control, unit-norm denominator contract | 8/12/20/36/52 (+ global model) | 0.436845/0.502227/0.595290/0.604507/0.639023 | negative | PASS |

### LSQ fit-row budget curve

With the optimization budget held at `1/1/1/1`, `nperts=1`, and the same
25k-trained THQ base, changing only the LSQ fit-row count gives:

| LSQ fit rows | LSQ32 mean nDCG@10 | LSQ48 mean nDCG@10 | audit |
| ---: | ---: | ---: | --- |
| 1,024 | 0.655921 | 0.663288 | PASS |
| 4,096 | 0.647225 | 0.663554 | PASS |
| 25,000 | 0.654756 | 0.636061 | PASS |

This is not a monotone capacity curve: more fit rows do not automatically
improve retrieval quality, and the 25k one-iteration LSQ48 arm is materially
worse.  It confirms that the 1,024-row `.663288` point is not sufficient to
close LSQ; optimization-budget and seed variance remain open.

For PQ, K=32/64/128 and the median-gap adaptive policy produced identical
quality within each codebook; only touched bytes changed.  Thus the stronger
fit changes PQ8 from the old bounded negative result to a small positive point
estimate, while PQ4 remains negative.  This is a source-bound research signal,
not a production claim: the evaluation fold has already been reused for prior
architecture decisions and requires confirmation on a fresh pre-registered
query split.  The deterministic `nredo=3` fit is audited, including aggregate
summary replay, but it is not an outer-seed variance estimate; independent PQ
fit seeds and paired confidence intervals remain open.

LSQ48 is also a positive point estimate, but this run is explicitly a bounded
fit control (1,024 rows and one iteration) on frozen THQ centroids fit from
25,000 rows.  The serving-shaped payload is 52 B/doc because direct cosine
requires a 4-byte FP32 norm sidecar; the sidecars are now materialized in the
persisted LSQ code artifact and independently replayed by the audit.  The old
48-B label was incomplete.  It
must not be described as a converged LSQ result.  Full 25k LSQ fitting and a
multi-seed held-out replay remain open because Faiss LocalSearchQuantizer is
substantially more expensive.

QJL is a score-correction primitive, not a reconstructed-vector codec.  The
audit now replays persisted projections/signs through score, top-10 and nDCG
summaries.  Widths m=256 and m=384 are paper-faithful sanity controls and
persist a global projection model (393,216 and 589,824 B respectively).  The
unit-norm denominator is a serving contract for normalized E5; source norms
remain diagnostics only.  Even m=384 Rademacher reaches only 0.639023, below
TQ1, so this implementation does not support a QJL production direction.

## Evidence hashes

- canonical rebound flat/raw/receipt: `b77d29f0`, `4e98f84c`, `7128cc29`;
- canonical TQ1 payload: `123e015bdca05edae64fb3e05a996c7c9f5ae72a117c96364a3739d4760a965a`;
- PQ4 result/audit: `245721d4`, `70ea4509`;
- PQ8 result/audit: `eca6d9f7`, `6e52555d`;
- LSQ result/models/codes/audit: `53459f76`, `595d6e09`, `79c9f578`, `2d94310d`;
- QJL result/artifact/audit: `7d646e6b`, `c9a7be19`, `a7e66750`;
- TQ+ result/audit: `bc923bf1`, `90364933`.

The QJL artifact now persists residual norms and packed sign sketches for all
Gaussian/Rademacher widths; its v3 audit independently replays residual norms,
source norms, projection shapes, packed signs, scores, top-10 IDs, and nDCG
aggregates. The complete raw reports and model/code payloads remain in the centralized
research workspace; only compact provenance is committed.  OPQ, TQ-domain
normalized residual correction, and a fresh held-out query split remain
explicit follow-up gates rather than being inferred from these results.
