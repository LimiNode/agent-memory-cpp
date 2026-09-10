# Unified binary locality bake-off

Date: 2026-09-08. This note consolidates the new random-hyperplane study,
landmark-affinity study, and the previously frozen ITQ/raw-THQ controls.

## Nomenclature audit

The prior THQ/LTHQ experiment runners were audited for level/threshold
semantics.  The existing native and LTHQ paths already use `THQ-L` for L
levels and L-1 thresholds.  The only mismatch was the initial affinity runner
and its first two notes; those rows are relabelled below and the runner now
stores explicit `levels`, `thresholds_per_coordinate`, and
`thermometer_bits_per_coordinate` fields.  No older experiment result was
silently renumbered.

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
| Gaussian hyperplane 512 (3-seed mean) | 64 | .743 ± .017 | .852 ± .013 | .939 ± .012 | .963 ± .009 | 24 / 5,633 / 34,406 |
| Rademacher hyperplane 256 | 32 | .480 | .618 | .775 | .841 | 234 / 35,044 / 123,692 |
| Rademacher hyperplane 512 (3-seed mean) | 64 | .758 ± .010 | .867 ± .003 | .953 ± .006 | .974 ± .005 | 22 / 3,778 / 21,653 |
| affinity THQ4 M32 (k-means) | 12 | .018 | .047 | .102 | .144 | not retained |
| affinity THQ5 M32 (k-means) | 16 | .022 | .051 | .122 | .161 | not retained |
| affinity THQ3 M32 (k-means) | 8 | .014 | .032 | .076 | .119 | not retained |
| affinity THQ3 M64 (k-means) | 16 | .039 | .066 | .133 | .179 | not retained |
| affinity THQ4 M64 (k-means) | 24 | .047 | .086 | .161 | .195 | not retained |
| affinity THQ5 M64 (k-means) | 32 | .051 | .095 | .177 | .213 | not retained |
| affinity THQ3 M128 (k-means) | 32 | .047 | .085 | .157 | .194 | not retained |
| affinity THQ4 M128 (k-means) | 48 | .058 | .094 | .172 | .214 | not retained |
| affinity THQ5 M128 (k-means) | 64 | .066 | .118 | .188 | .230 | not retained |
| affinity THQ3 M256 (k-means) | 64 | .058 | .103 | .175 | .211 | not retained |
| affinity THQ4 M256 (k-means) | 96 | .076 | .116 | .197 | .234 | not retained |
| affinity THQ5 M256 (k-means) | 128 | .077 | .122 | .214 | .256 | not retained |
| affinity THQ3 M256 (Gaussian) | 64 | .910 | .964 | .991 | .993 | 10 / 639 / 4,533 |
| affinity THQ4 M256 (Gaussian) | 96 | .939 | .976 | .997 | .997 | 8 / 345 / 2,129 |
| affinity THQ5 M256 (Gaussian) | 128 | .949 | .978 | .997 | .999 | 8 / 257 / 1,853 |

The native rows do not have 5k/10k points in their frozen run (its measured
K grid was 128/256/512/1024); dashes are intentional, not interpolations.
K-means affinity rank quantiles were not retained in the corrected matrix;
Gaussian M=256 rank accounting is recorded in the separate corrected replay.
All rank columns are `r50/r95/r99` lower-bound Hamming ranks over the 1,520
teacher documents.

## Conclusions

1. Raw-coordinate THQ is the strongest compact locality representation in the
   current fixture.  ITQ Hamming and random hyperplanes are materially weaker
   at K=256.  Random Gaussian and Rademacher families are nearly tied, so the
   difference is not explained by the Rademacher-vs-Gaussian choice.
2. K-means landmark affinity is not a viable replacement.  Its FP32 dot oracle
   has effectively zero compact-budget survival and its THQ variants remain
   below `.26` at K=10k for M=256.  Gaussian random directions are a distinct,
   substantially stronger family: THQ5 M=256 reaches `.949 @256` and `.999
   @10k`, with `r95=257`.  They must not be collapsed into the k-means result.
3. These are representation/locality results, not evidence for or against a
   full LSH index.  A true LSH experiment still needs independent tables,
   band widths, multiprobe ordering, posting unions, random/sequential bytes,
   cold/warm p95/p99, and complete rerank quality.

## Product/research decision

Keep flat THQ3/THQ4 and the improved float prototype-IVF → local K8/K32/R0
cascade as the active product lines.  Keep random-hyperplane codes as a
documented zero-training control, not a promoted index.  Archive k-means
landmark affinity; keep Gaussian direction-affinity THQ as a research
candidate pending native cost and an independent held-out confirmation.

The next independent research PR may implement a genuine cosine-LSH
multi-table/multiprobe index, but it must beat the flat THQ baseline on the
full quality/bytes/latency contract before any physical index work is accepted.

## Sources

* [Cosine-LSH / SimHash locality](2026-09-08-cosine-lsh-locality.md)
* [Landmark-affinity THQ locality](2026-09-08-landmark-affinity-thq.md)
* [Flat compact codes and FP32-free final rerank](2026-09-06-flat-code-and-fp32-free-final-rerank.md)
* [Native flat THQ versus E5-IVF bake-off](2026-09-08-native-thq-ivf-bakeoff.md)

Corrected affinity artifact: `tmp/landmark-affinity-v2-full/result.json`,
SHA-256 `c018502c02b193f88046ed5d0d140483e7d6c365dd167d752fcb7874d0231ead`.
Gaussian M=256 rank artifact: `tmp/landmark-affinity-v2-ranks/result.json`,
SHA-256 `8e5501e9fdfd64aea55503fd807c37ceed4f5277dbf9fb2452f84789940b5704`.
