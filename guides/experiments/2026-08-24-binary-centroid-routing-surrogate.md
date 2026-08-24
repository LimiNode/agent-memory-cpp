# Binary-centroid routing surrogate

This external calibration-only experiment follows the float semantic IVF
control. It does not train a new semantic partition: every centroid and every
document assignment is loaded byte-for-byte from the completed float-control
artifact.

For each frozen float centroid set, a deterministic seeded Rademacher projection
and sign produces 128-, 256-, or 512-bit centroid codes. A query scans all centroid
codes by Hamming distance, takes `2 x nprobe` or `4 x nprobe`, then computes
exact float inner products only for that shortlist and selects the original
`nprobe` semantic lists. The downstream ITQ-256 Hamming@768, ADC@256, and E5
rerank cascade is unchanged.

| scale | centroid counts | candidate targets | code lengths | binary shortlist multiplier |
| --- | --- | --- | --- | --- |
| Spanish 100k | 1024, 4096 | 5%, 10%, 25% | 128, 256, 512 | 2x, 4x |
| Spanish 1M | 4096, 16384 | 5%, 10%, 25% | 128, 256, 512 | 2x, 4x |

The 72 rows report recall of the exact-float control’s selected centroid lists,
the separate binary-scan and float-rerank timing distributions, candidate mass,
and the final E5 survival/nDCG. It is deliberately not a centroid HNSW, MIH,
or ADC experiment. Those optimizations would obscure the first question:
whether compact binary codes preserve enough access to the already-good
semantic partition.

The runner and archive packager may only accept a complete, validated float
semantic IVF result root. This protocol neither opens French confirmation data
nor makes Faiss a production library dependency.
