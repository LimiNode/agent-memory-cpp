# Semantic-anchor Hamming relocation ceiling

Date: 2026-09-02. Protocol introduced on the post-#273 NeuRoute research
line; no production codec or router is selected by this experiment.

## Hypothesis

For useful documents, the minimum Hamming distance to one or more
document-derived semantic anchors (`ITQ(anchor)`) may be materially smaller
than the distance to `ITQ(query)`.  If so, moving the Hamming centre can reduce
MIH work without changing document codes or stable document identity.

## Matched controls

The runner compares `q_global`, `q_restricted`, `c_seeded`, `p_seeded`, and
`p_oracle`.  The restricted control uses exactly the same anchor postings as a
seeded treatment but retains the query code as centre; this separates a gain
from shrinking the searchable universe from a gain caused by centre relocation.
Centroid and K8-prototype anchors are kept separate.  `p_oracle` is privileged
and is a ceiling only.

For each query the protocol records r50/r90/r95/r99, radius hit rates, raw
posting entries scanned, unique documents after union, exact-E5 top-ten survival at fixed budgets, and
per-query contributions.  Qrels are terminal utility diagnostics only and may
not select a treatment.  Analytic MIH probe counts are labelled estimates;
native bucket/posting benchmarks remain a required follow-up.

## Input contract

`run-neuroute-semantic-anchor-relocation.py run` consumes an NPZ containing
`documents`, `queries`, `document_codes`, `query_codes`, and for each anchor
kind (`centroid`, `prototype`) the arrays `*_vectors`, `*_codes`,
`*_offsets`, and `*_documents`.  Optional `target_documents` freezes a teacher
target; otherwise exact FP32 top ten is materialized deterministically.

The supplied self-test is synthetic and only checks shape, control separation,
and deterministic diagnostics.  It is not evidence for a production gate.
