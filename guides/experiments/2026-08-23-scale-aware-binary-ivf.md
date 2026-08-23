# Scale-aware BinaryIVF calibration

This external calibration-only experiment tests whether the strong 25k
BinaryIVF quality/candidate frontier transfers to Spanish 100k and 1M frozen
materializations. It uses Faiss 1.13.2 outside the C++ library, stores each
trained index artifact locally, and measures actual visited-list document mass
at target fractions 5%, 10%, and 25%.

`nlist` is scale-specific: 1024/4096 at 100k and 4096/16384 at 1M. The
standard ITQ-256 Hamming@768 and binary-ADC@256 cascade is retained. E5 oracle
survival is calculated against a fresh exact Flat E5 top-10 scan of the frozen
evaluation vectors, recorded separately from external Python/Faiss timing.

This is not a native latency comparison with MIH/HNSW or a confirmation study.
French data and production selection remain forbidden.

## Results

The 25k finding transfers directionally but degrades with scale. The strongest
observed point at each scale is the larger codebook at about 5% candidate mass:

| scale | BinaryIVF treatment | candidates | E5 survival after ADC | external Faiss p50 / p95 (ms) |
| --- | --- | ---: | ---: | ---: |
| 25k | nlist 4096, nprobe 205 | 5.66% | 92.38% | 0.546 / 1.039 |
| 100k | nlist 4096, nprobe 205 | 5.30% | 87.10% | 0.698 / 1.183 |
| 1M | nlist 16384, nprobe 819 | 5.20% | 87.08% | 2.600 / 3.264 |

At about 10% candidate mass, 100k reaches 91.57% and 1M reaches 90.56% with
the larger codebook. The 1M 4096-list variant is cheaper externally at that
budget (1.831 ms p50) but retains only 88.18%; 16384 lists trades more lookup
work for 90.56% survival.

This rejects both extremes: the 25k 92% result is not an artefact that vanishes
at 1M, but neither is it scale-invariant at 5% candidates. Global,
data-dependent BinaryIVF remains qualitatively much stronger than random MIH
substrings, while its native implementation and scale-specific tuning remain
open work. The timings are external Python/Faiss measurements, not a direct
native MIH/HNSW comparison.
