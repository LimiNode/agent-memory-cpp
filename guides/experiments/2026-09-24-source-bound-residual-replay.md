# Source-bound residual replay: strong PQ, LSQ, and QJL

Date: 2026-09-24  
Status: `EXECUTED` for the arms listed below; no production selection.

The canonical DE-1M E5 bundle was recovered and validated.  The old 152-query
fused stream was not trusted as a THQ payload: its document IDs and row
boundaries were preserved, while a separate materializer rebound every record
to the canonical 96-byte ordinal THQ4 table.  The resulting 100-byte records,
raw manifest, and receipt are retained outside Git under
`E:\_repoz\agent-memory-workspaces\canonical-thq4-rebound-candidate-v1`.

All rows use the same frozen candidate shell, canonical THQ interval² top-128,
canonical TQ1 payload, cosine metric, and 152-query qrels split.  The TQ1
baseline is independently regenerated in the same run (`mean nDCG@10 =
0.659176`).

## Results

| arm | fit | side bytes/doc | mean nDCG@10 | delta vs TQ1 | audit |
| --- | --- | ---: | ---: | ---: | --- |
| TQ1 | canonical TQ1 | 56 | 0.659176 | 0 | regenerated |
| TQ1 + PQ4 | 25k rows, Faiss Kmeans, 25 iters, 3 restarts | 64 | 0.656730 | -0.002446 | PASS |
| TQ1 + PQ8 | 25k rows, Faiss Kmeans, 25 iters, 3 restarts | 68 | 0.660526 | +0.001351 | PASS |
| TQ1 + LSQ32 | Faiss LSQ, 1,024 rows, 1/1/1/1, nperts=1 | 32 | 0.656379 | -0.002796 | PASS |
| TQ1 + LSQ48 | Faiss LSQ, 1,024 rows, 1/1/1/1, nperts=1 | 48 | 0.662620 | +0.003445 | PASS |
| QJL Gaussian m=32/64/128 | score correction, source-norm denominator | 8/12/20 | 0.402911/0.508609/0.556806 | negative | PASS |
| QJL Rademacher m=32/64/128 | explicit heuristic control | 8/12/20 | 0.436845/0.502227/0.595290 | negative | PASS |

For PQ, K=32/64/128 and the median-gap adaptive policy produced identical
quality within each codebook; only touched bytes changed.  Thus the stronger
fit changes PQ8 from the old bounded negative result to a small positive point
estimate, while PQ4 remains negative.  This is a source-bound research signal,
not a production claim: the evaluation fold has already been reused for prior
architecture decisions and requires confirmation on a fresh pre-registered
query split.

LSQ48 is also a positive point estimate, but this run is explicitly a bounded
fit control (1,024 rows and one iteration).  It must not be described as a
converged LSQ result.  Full 25k LSQ fitting and a multi-seed held-out replay
remain open because Faiss LocalSearchQuantizer is substantially more expensive.

QJL is a score-correction primitive, not a reconstructed-vector codec.  Both
the Gaussian reference and Rademacher control are far below TQ1 at these
widths, so this implementation does not support a QJL production direction.

## Evidence hashes

- canonical rebound flat/raw/receipt: `b77d29f0`, `4e98f84c`, `7128cc29`;
- canonical TQ1 payload: `123e015bdca05edae64fb3e05a996c7c9f5ae72a117c96364a3739d4760a965a`;
- PQ4 result/audit: `245721d4`, `70ea4509`;
- PQ8 result/audit: `eca6d9f7`, `6e52555d`;
- LSQ result/audit: `ba433dcb`, `aab8f26`;
- QJL result/audit: `b555328b`, `702347b6`.

The complete raw reports and model/code payloads remain in the centralized
research workspace; only compact provenance is committed.  OPQ, TQ-domain
normalized residual correction, and a fresh held-out query split remain
explicit follow-up gates rather than being inferred from these results.
