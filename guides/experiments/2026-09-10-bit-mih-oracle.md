# 2026-09-10 bit-MIH oracle on semantic THQ4 queries

This experiment is the ordinary bitwise-MIH control requested by the ordinal
gate. It uses the frozen 1M-document, 152-query DE-1M fixture and canonical
raw THQ4-384 (1,152 bits). Each code is split into equal byte-aligned bands;
the index unions exact bucket postings (`r=0`) or every one-bit neighbour in
each band (`r=1`), deduplicates IDs, and ranks the union by the same Hamming
distance and document-ID tie policy. It is an in-memory algorithmic oracle,
not a persistent or production index.

## Corrected full result

| bands × bits | radius | unique candidates | postings touched | @256 survival (mean / worst) |
|---|---:|---:|---:|---:|
| 48 × 24 | 0 | 784 | 784 | .032895 / .0 |
| 48 × 24 | 1 | 9,885 | 9,944 | .271711 / .0 |
| 72 × 16 | 0 | 42,538 | 43,539 | .442763 / .0 |
| 72 × 16 | 1 | 319,129 | 387,738 | .963158 / .7 |
| 144 × 8 | 0 | 965,314 | 3,414,276 | .998684 / .9 |
| 144 × 8 | 1 | 1,000,000 | 17,159,173 | .999342 / .9 |

The complete corrected per-query JSON is retained outside git as
`postbacklog-bit-mih-corrected.json`; its SHA-256 is recorded in the compact
receipt. Python posting-lookup timings are diagnostic only. The locked native
flat THQ reference remains 20.994 ms/query p50 from the preceding gate.

The matrix shows the expected collision/probe frontier: short bands have low
work but lose most teacher neighbours, while the first radius-one operating
point near the flat locality ceiling touches 319k documents (and has a .7
worst-query tail). Eight-bit bands are effectively a corpus scan.

## Interpretation

This closes only this exact-bucket/radius-one bit-MIH schedule as a low-work
replacement for flat THQ4 on this fixture. It does not close classical cosine
LSH with independent hyperplane tables, other probe schedules, or richer
ordinal multiprobe. No ANN index, MDBX backend, or production activation is
licensed. The next useful gate is a matched comparison with a richer
coordinate-aware ordinal multiprobe oracle, using the optimized flat scan as
the cost ceiling.
