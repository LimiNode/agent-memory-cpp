# 2026-09-11 packed ordinal-subvector multi-sequence oracle

This oracle tests exact packed ordinal subvectors and one-step coordinate
probes as a richer alternative to the earlier block-sum key. It uses the
frozen 1M-document/152-query THQ4-384 fixture, preserves all coordinate levels
(2 bits per coordinate in the key), and ranks candidates by exact ordinal L1.
It is an in-memory candidate-generation oracle; no MDBX or production path is
claimed.

## Result

Across all queries, 48 blocks of 8 coordinates with exact keys touched 784
postings and achieved `.032895 @256` survival. Radius-one probing reached
9,885 candidates and `.271711` survival (worst `.0`). Wider subvectors were
effectively collision-free: 32×12 radius-one averaged 41 candidates and
`.013158 @256`; 24×16 averaged 0.22 candidates and `.000658 @256`.
Increasing the shortlist budget did not recover missing teacher IDs because
they were absent from the probed unions. Posting and candidate byte counts,
probe counts, and per-query rows are in the raw report and compact receipt.

## Interpretation

The tested exact/radius-one packed schedules are negative as low-work
candidate generators, matching the bit-MIH frontier. This does not close
multi-probe schedules that enumerate larger ordinal neighborhoods, learned
cell ordering, or unrelated cosine-LSH families. Given the 319k-candidate
bit-MIH control and the excellent flat locality, the next gate should focus on
weighted collision voting or progressive flat scans before any persistence
backend. `production_activation: false`.
