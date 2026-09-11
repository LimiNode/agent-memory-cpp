# Cosine-LSH margin multiprobe oracle (2026-09-11)

The corrective revision normalizes every Gaussian hyperplane and enumerates
single- and two-bit perturbations by cumulative margin cost. This is a bounded
multi-probe oracle, not merely a sequence of independent single-bit flips.

The corrected exact-bucket LSH replay left margin-guided multiprobe open. This
oracle keeps the independent Gaussian tables fixed, then orders one-bit bucket
flips by the query hyperplane margins `|r_i · q|`. It reports candidate mass and
teacher recall for probe budgets without conflating bucket generation with a
hash-Hamming reranker.

The implementation is an oracle only: postings are in-memory, the DE-1M
payload is external, and no page/latency or production authorization is
claimed. Cross-polytope LSH remains a separate follow-up. `production_activation:
false`.
