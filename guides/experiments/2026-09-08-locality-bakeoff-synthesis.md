# Unified binary locality bake-off

Date: 2026-09-08. This note consolidates the new random-hyperplane study,
landmark-affinity study, and the previously frozen ITQ/raw-THQ controls.

## Comparable evidence

All rows below use the frozen DE-1M fixture (1M normalized E5 documents, 152
queries, exact E5 top-10 teacher IDs).  “Survival” is the mean fraction of
teacher top-10 IDs present in the exhaustive code top-K result.  The native
THQ/ITQ rows use the packed native runner; random-hyperplane and affinity rows
use portable Python reference runners, so timings are not directly
interchangeable.

| representation | bytes/doc | @256 | @1k | @5k | @10k | rank quantiles |
|---|---:|---:|---:|---:|---:|---|
| ITQ256 Hamming (native) | 32 | .853 | — | — | — | not retained |
| raw THQ3 (native) | 96 | .997 | — | — | — | not retained |
| raw THQ4 (native) | 144 | .999 | — | — | — | not retained |
| Gaussian hyperplane 256 | 32 | .476 | .628 | .801 | .859 | 231 / 32,587 / 135,969 |
| Gaussian hyperplane 512 | 64 | .733 | .851 | .934 | .963 | 29 / 5,930 / 30,389 |
| Rademacher hyperplane 256 | 32 | .480 | .618 | .775 | .841 | 234 / 35,044 / 123,692 |
| Rademacher hyperplane 512 | 64 | .750 | .864 | .946 | .968 | 23 / 4,478 / 23,807 |
| affinity THQ3 M32 | 12 | .018 | .047 | .102 | .144 | not retained |
| affinity THQ4 M32 | 16 | .022 | .051 | .122 | .161 | not retained |
| affinity THQ3 M64 | 24 | .047 | .086 | .161 | .195 | not retained |
| affinity THQ4 M64 | 32 | .051 | .095 | .177 | .213 | not retained |
| affinity THQ3 M128 | 48 | .058 | .094 | .172 | .214 | not retained |
| affinity THQ4 M128 | 64 | .066 | .118 | .188 | .230 | not retained |
| affinity THQ3 M256 | 96 | .076 | .116 | .197 | .234 | 123,010 / 839,779 / 964,880 |
| affinity THQ4 M256 | 128 | .077 | .122 | .214 | .256 | 106,550 / 830,101 / 966,711 |

The native rows do not have 5k/10k points in their frozen run (its measured
K grid was 128/256/512/1024); dashes are intentional, not interpolations.
Affinity rank quantiles were retained for the M=256 rerun; smaller landmark
counts were not rerun because their quality ceiling was already lower.  All
rank columns are `r50/r95/r99` lower-bound Hamming ranks over the 1,520 teacher
documents.

## Conclusions

1. Raw-coordinate THQ is the strongest compact locality representation in the
   current fixture.  ITQ Hamming and random hyperplanes are materially weaker
   at K=256.  Random Gaussian and Rademacher families are nearly tied, so the
   difference is not explained by the Rademacher-vs-Gaussian choice.
2. Naive landmark affinity is not a viable replacement.  Even its FP32 oracle
   (`phi(q) dot phi(x)`) has effectively zero compact-budget survival; THQ on
   that profile remains below `.26` at K=10k for M=256.
3. These are representation/locality results, not evidence for or against a
   full LSH index.  A true LSH experiment still needs independent tables,
   band widths, multiprobe ordering, posting unions, random/sequential bytes,
   cold/warm p95/p99, and complete rerank quality.

## Product/research decision

Keep flat THQ3/THQ4 and the improved float prototype-IVF → local K8/K32/R0
cascade as the active product lines.  Keep random-hyperplane codes as a
documented zero-training control, not a promoted index.  Archive naive
landmark-affinity THQ and do not build affinity-MIH/LSH on top of it.

The next independent research PR may implement a genuine cosine-LSH
multi-table/multiprobe index, but it must beat the flat THQ baseline on the
full quality/bytes/latency contract before any physical index work is accepted.

## Sources

* [Cosine-LSH / SimHash locality](2026-09-08-cosine-lsh-locality.md)
* [Landmark-affinity THQ locality](2026-09-08-landmark-affinity-thq.md)
* [Flat compact codes and FP32-free final rerank](2026-09-06-flat-code-and-fp32-free-final-rerank.md)
* [Native flat THQ versus E5-IVF bake-off](2026-09-08-native-thq-ivf-bakeoff.md)
