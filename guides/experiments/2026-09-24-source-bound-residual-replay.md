# Source-bound residual replay: strong PQ, LSQ, and QJL

Date: 2026-09-24  
Status: `EXECUTED` for the arms listed below; no production selection.

The canonical DE-1M E5 bundle was recovered and validated.  The old 152-query
fused stream was not trusted as a THQ payload: its document IDs and row
boundaries were preserved, while a separate materializer rebound every record
to the canonical 96-byte ordinal THQ4 table.  The resulting 100-byte records,
raw manifest, and receipt are retained outside Git under
`E:\_repoz\agent-memory-workspaces\canonical-thq4-rebound-candidate-v1`.

The complete canonical 1M THQ4 table is now reproducibly materialized at
`E:\_repoz\agent-memory-workspaces\canonical-thq4-table-v1`. Its SHA-256 is
`0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70`, matching
the table bound by the candidate materialization receipt. This removes the
previous ambiguity between that canonical 96-byte ordinal table and an older
144-byte thermometer table.

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

### Strong full-budget LSQ32 follow-up (2026-09-25)

The first full-budget outer seed completed on the recovered canonical table
with `train_rows=25,000`, `base_train_rows=25,000`,
`train_iters/train_ils_iters/encode_ils_iters/icm_iters=25/8/16/4`,
`nperts=4`, and base seed `20260925`. The historical runner derived the
effective Faiss seed as `20260957` (`base + 32`); this is now recorded
explicitly, and new payload-separated runs pass the user seed directly. It is a single historical-152
exploratory seed, not a converged production selection.

| arm | side bytes/doc | total THQ+side | mean nDCG@10 | mean teacher overlap | fit seconds | candidate-union docs | candidate-union encode seconds | audit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| strong LSQ32 | 36 | 132 | 0.660215 | 0.890789 | 1651.11 | 18,362 | 69.70 (263.45 docs/s) | PASS |

This point is below the earlier bounded LSQ48 `.663288` and strong PQ8
`.660526` point estimates in the current historical-fold comparison, but those
are not seed-matched confirmation. The full-budget fit is materially more
expensive than the one-iteration control, so fit cost is part of the result.
The `69.70 s` number is only candidate-union `compute_codes()` assignment, not
end-to-end insertion throughput; it excludes THQ encoding, norm generation,
serialization, and storage writes. The independent audit replays source-derived THQ centroids, top-128 selection,
persisted additive subcodebook decode, final norm sidecars, cosine top-10, and
nDCG. Additional outer seeds, strong LSQ48, and fresh-query confirmation
remain pending.

### Strong full-budget LSQ32 outer-seed confirmation (2026-09-25)

The same full-budget configuration was fit with outer seed `20260926` and
the corrected runner (the effective Faiss seed is exactly `20260926`). The
independent audit passes on the same source-bound historical-152 fold, but the
point estimate is lower than seed `20260925`:

| arm | seed | side bytes/doc | total THQ+side | mean nDCG@10 | mean teacher overlap | audit |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| strong LSQ32 | 20260925 | 36 | 132 | 0.660215 | 0.890789 | PASS |
| strong LSQ32 | 20260926 | 36 | 132 | 0.659335 | 0.892105 | PASS |

The two full-budget seeds therefore differ by `-0.0008794` nDCG. This is
seed variance, not evidence of a stable LSQ32 improvement; CPU/OMP provenance
for these historical artifacts predates the hardened timing schema, so their
encode seconds are not suitable for cross-run performance claims. New LSQ
replays record CPU model, physical/logical cores, Faiss compile options and
OMP threads fail-closed.

### Official multi-bit RaBitQ replay (2026-09-25)

The pinned Apache-2.0 `VectorDB-NTU/RaBitQ-Library` snapshot
`a010649f8faabc286070e5ed18c7dc121e01ffe3` was run through its official
multi-bit quantizer and compact split scorer on the same canonical THQ
top-128 shell. Widths are total bits/dimension; cosine is evaluated by
normalizing vectors and using the upstream inner-product path. The persisted
split payload includes the upstream binary factors and ex-code factors; its
logical side payload is therefore larger than the bare packed code.

| arm | side bytes/doc | total THQ+side | mean nDCG@10 | candidate-union compact encode | candidate score | audit |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| official RaBitQ B=2 | 116 | 212 | 0.5842888 | 0.614 s (29,910 docs/s) | 20.73 ms | PASS |
| official RaBitQ B=3 | 164 | 260 | 0.6348183 | 0.690 s (26,596 docs/s) | 20.88 ms | PASS |
| official RaBitQ B=4 | 212 | 308 | 0.6513442 | 0.595 s (30,865 docs/s) | 21.57 ms | PASS |

The independent audit replays canonical THQ top-128, rotation orthogonality,
the expanded-code RaBitQ score equation, official compact/full scorer parity,
top-10 IDs, nDCG, and source/model SHA bindings. These are historical-152
engineering results, not final production selection; the global rotation
matrix is additionally persisted and charged in the model accounting.

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
A five-draw Gaussian m=384 seed stability diagnostic gives means
`0.632900/0.621886/0.614363/0.613028/0.618578` (mean `0.620151`), so the
single-seed result is not an unusually unlucky draw; all five remain below
TQ1.  A separate source-bound audit regenerates each Gaussian projection and
packed sign code without using the producer scorer API, then replays score,
top-10, each seed's nDCG, and the min/max/mean summary.  This is still the
reused historical-152 fold, not confirmatory evidence.

## Evidence hashes

- canonical rebound flat/raw/receipt: `b77d29f0`, `4e98f84c`, `7128cc29`;
- canonical TQ1 payload: `123e015bdca05edae64fb3e05a996c7c9f5ae72a117c96364a3739d4760a965a`;
- PQ4 result/audit: `245721d4`, `70ea4509`;
- PQ8 result/audit: `eca6d9f7`, `6e52555d`;
- LSQ result/models/codes/audit: `53459f76`, `595d6e09`, `79c9f578`, `2d94310d`;
- strong LSQ32 seed result/models/codes/audit: `2ee70c37`, `74749100`, `4c49d6ba`, `8192c83d`;
- strong LSQ32 seed `20260926` result/models/codes/audit: `7046a84a`, `55ea740e`, `4ea044e3`, `f1276385`;
- official RaBitQ B=2/3/4 result/audit: `873980ff`, `354ff5e9`;
- QJL result/artifact/audit: `7d646e6b`, `c9a7be19`, `a7e66750`;
- QJL m=384 five-seed result/audit: `8d93a034`, `49b47b3a`;
- TQ+ result/audit: `bc923bf1`, `90364933`.

The QJL artifact now persists residual norms and packed sign sketches for all
Gaussian/Rademacher widths; its v3 audit independently replays residual norms,
source norms, projection shapes, packed signs, scores, top-10 IDs, and nDCG
aggregates. The complete raw reports and model/code payloads remain in the centralized
research workspace; only compact provenance is committed.  OPQ, TQ-domain
normalized residual correction, and a fresh held-out query split remain
explicit follow-up gates rather than being inferred from these results.
