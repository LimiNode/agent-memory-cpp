# 2026-09-11 independent cosine-LSH baseline

This is the first actual cosine-LSH retrieval oracle in the post-backlog
line. Four independent tables use 16-bit Gaussian random-hyperplane
signatures; exact bucket unions are ranked by the 64-bit Hamming code. On
eight semantic queries the union averaged 41,553 candidates but achieved only
`.05 @256` survival (worst `.0`). This is a concrete exact-bucket baseline,
not a claim that all cosine-LSH or multi-probe variants are closed.

The experiment is representation/index evidence only. Margin-guided probes,
cross-polytope LSH, and larger table schedules remain open; no MDBX backend or
production activation is licensed (`production_activation: false`).
