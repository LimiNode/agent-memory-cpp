# 2026-09-11 independent cosine-LSH baseline (corrected evaluation)

This is the first actual cosine-LSH retrieval oracle in the post-backlog
line. Four independent tables use 16-bit Gaussian random-hyperplane
signatures; exact bucket unions are ranked by the 64-bit Hamming code. On
eight semantic queries the union averaged 41,553 candidates but achieved only
`.05 @256` survival (worst `.0`). This is a concrete exact-bucket baseline,
not a claim that all cosine-LSH or multi-probe variants are closed.

The experiment is representation/index evidence only. Margin-guided probes,
cross-polytope LSH, and larger table schedules remain open; no MDBX backend or
production activation is licensed (`production_activation: false`).

## Corrective replay

The original `.05 @256` combined exact-bucket candidate generation with a
64-bit hash-Hamming reranker and used only eight queries and one seed. It was
therefore incomplete as an evaluation of the generator itself.

The corrected replay uses all 152 queries and five independent seeds. Mean
teacher recall in the exact-bucket union is `.2009` (seed range `.1454--.2579`)
with 24k--75k mean candidates. Exact cosine reranking preserves that union
recall, as expected for global top-10 teachers; hash-Hamming reranking retains
only `.0368--.0513`. Every seed still has a zero-recall worst query.

The occupancy diagnosis explains the large unions: per-table entropy spans
only 5.71--10.87 effective bits despite 16 formal bits, and the largest
observed bucket contains 165,777 documents. The exact-bucket generator is
therefore negative on this fixture, while margin-guided multiprobe remains a
separate oracle question.
