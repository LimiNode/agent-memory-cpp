# Data-dependent product locator

This calibration-only line tests whether local codebooks trained from the actual
ITQ-256 distribution can restore the routing quality that fixed mathematical
product centers did not provide. It is separate from the earlier static-product
experiment: codebooks and any bit decomposition are fit only from frozen train
data, never from the 648 evaluation queries or French confirmation data.

The planned treatments are deliberately progressive:

1. contiguous balanced ITQ-bit blocks with train-only local Hamming medoids;
2. a train-only bit permutation before the same local-medoids treatment;
3. local float k-means on frozen E5 vectors, retaining the frozen ranking
   ITQ-256 code for the downstream cascade.

Each runs at controlled implicit-cell budgets of 4,096, 16,384, and 65,536,
rather than assuming a sparse `16 blocks x 8` product space is useful. Query
cells are expanded best-first with a lexical cell-key tie break until the
requested candidate mass is reached. The two binary treatments use summed
local Hamming cost; `float_e5_product` uses summed squared L2 cost in its local
E5 blocks. The unchanged downstream treatment is Hamming@768, binary ADC@256,
and exact E5 rerank.

The 54-row 100k/1M plan reports candidate mass, E5 survival, reranked nDCG,
local probes, non-empty-cell traversal, and codebook/index bytes. A pragmatic
exploratory gate is predeclared: at 5% candidate mass, a treatment below 70%
E5 survival after ADC does not justify native trie or empty-cell traversal
engineering. Passing that gate would only justify a follow-up implementation
study; it is not a production-selection or confirmation claim.

The recorded p50 routing times are single-host diagnostic observations from the
deterministic routing replay. They expose the relative cost of exhaustive
best-first empty-cell traversal, but are not repeated native latency evidence
and must not be used to select a production backend.

The frozen storage input has no document-side pre-ITQ projection payload: it
contains E5 document vectors, packed ITQ codes, and query-side ITQ projections.
Consequently, the float treatment is explicitly defined over frozen E5 vectors
and its codebook is fit only on the corresponding frozen calibration E5 train
vectors. This avoids inventing an unavailable payload while preserving the
intended data-dependent float-routing control.

## 2026-08-25 result

The complete 54-row Spanish calibration matrix was measured and independently
replayed into a deterministic evidence archive. The product-localizer was
better when its local codebooks were fit in E5 space than when they were fit as
Hamming medoids over the ITQ code, but it did not clear the predeclared 70%
E5-survival gate at 5% candidate mass on either scale.

| scale | treatment | best 5% budget | E5 survival after ADC | reranked nDCG@10 | p50 routing time |
| --- | --- | ---: | ---: | ---: | ---: |
| Spanish 100k | float E5 product | 65,536 | 55.77% | 0.5636 | 43.18 ms |
| Spanish 100k | binary medoids | 65,536 | 34.51% | 0.4155 | 70.34 ms |
| Spanish 1M | float E5 product | 65,536 | 57.59% | 0.5161 | 46.39 ms |
| Spanish 1M | binary medoids | 65,536 | 35.52% | 0.3874 | 73.81 ms |

At 1M, float-product routing improved monotonically with the implicit cell
budget: 46.44%, 51.45%, and 57.59% E5 survival at 4,096, 16,384, and 65,536
cells respectively. This cost 101, 398, and 1,747 median cell probes per
query. The 65,536-cell float treatment reached 87.08% survival and 0.6675
nDCG@10 only after expanding to 25% of the corpus; the frozen float semantic
IVF control remains stronger at that same mass (92.41% and 0.6883).

The train-only entropy bit permutation produced distinct serialized artifacts
and candidate traces, but the same aggregate quality as contiguous binary
medoids in this matrix. It is therefore not a promising selector by itself.

The experiment is a negative result for this local, sampled-medoid product
locator follow-up: none of its 5% rows reaches the exploratory quality gate, and the
quality improvement from more cells comes with an increasingly expensive
best-first empty-cell traversal. It does not invalidate data-dependent
semantic partitions in general; the float semantic IVF control remains the
relevant positive evidence. A future learned router should be trained against
the semantic partition/retrieval objective rather than replacing it with local
ITQ-bit structure.

The untracked local deterministic evidence archive is
`tmp/data-dependent-product-locator-v1-evidence.zip`, SHA-256
`af8a6864f59d31fa098cad7e9ef4f1c34911232ed57c82e69085630fca3b8d03`
(667,612,546 bytes; 301 members). It remains local pending review and any
separately approved evidence-release decision.

## 2026-08-25 provenance amendment

The completed measurement used squared local E5 distance for
`float_e5_product`, not local Hamming distance. The amended machine contract
states that routing rule per treatment, while retaining the original contract
SHA as `amends_measurement_contract_sha256`; this is a provenance clarification
of the completed treatment, not a replacement measurement.

The amendment also pins the deterministic 1,024-position Hamming-medoid train
sample, its four update iterations, and the float k-means iteration/restart/
seed settings. Future and resumed runs persist one complete measured row per
matrix entry, including timing summaries captured at measurement time. Resume
therefore restores the original `measured` row rather than emitting an
incomplete `reused_complete` surrogate. Existing complete rows may be adopted
from a byte-bound prior summary and are still independently replayed by the
evidence packager.
