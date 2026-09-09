# Teacher-ranked K8 prototype binary metric

Date: 2026-09-03. This experiment is the follow-up to #280/#281 and is
intentionally distinct from #279.

## Question

Can a shared learned binary metric preserve the useful geometry of the K8
prototypes that already succeeded under the float prototype-IVF teacher in
#269? The query and every K8 prototype are projected by the same learned
hyperplanes. Positives and hard negatives are taken from the float teacher's
prototype ranking, rather than from document/prototype membership alone.

## Frozen protocol

The initial width frontier is 16, 24, 32, 48, and 64 bits. The first half of
the replay queries is used for pairwise training; the second half is held out.
For each training query, the top eight float-teacher prototypes are positives
and ranks 64, 256, and 1,023 are hard negatives. The resulting symmetric
query-versus-(positive-minus-negative) alignment matrix is eigendecomposed and
its leading vectors become shared sign hyperplanes. Thresholds are medians of
training-query and positive-prototype projections.

Each held-out query then performs an exhaustive XOR+popcount scan over all K8
prototype codes. The report records teacher prototype recall at 1,024,
2,048, 4,096, and 8,192 candidates, worst-query recall, Hamming radius,
entropy, and scan timing. A supplied frozen prototype code is retained as a
control. No MIH probe, native cascade, address scan, or production selection is
licensed by this geometry-only run.

## Directional three-seed result

The available semantic-anchor replay artifacts were run independently for all
three R4 seeds. At the 4,096-prototype budget, mean teacher-prototype recall
was:

| width | learned configuration | learned internal | frozen configuration | frozen internal |
| ---: | ---: | ---: | ---: | ---: |
| 16 | 0.1145 | 0.0922 | 0.0445 | 0.0384 |
| 24 | 0.1392 | 0.1060 | 0.0669 | 0.0581 |
| 32 | 0.1510 | 0.1172 | 0.0949 | 0.0809 |
| 48 | 0.1634 | 0.1365 | 0.1397 | 0.1195 |
| 64 | 0.1644 | 0.1528 | 0.1977 | 0.1756 |

The learned metric improves monotonically through 64 bits on this diagnostic,
but the curve is already flattening and remains far below the float
prototype-IVF control from #269 (about 0.9996 at the comparable shortlist).
The frozen code overtakes the learned metric at 64 bits. These are prototype
recall figures, not final nDCG or top-10 overlap; they do not license a product
router or justify 128/256 bits by themselves.

The experiment must be run with a teacher cache from #269 when available. If
the cache is absent, the runner constructs the exact float top-1,024 ranking
from `prototype_vectors`; this is equivalent as a ranking reference but is
reported separately by `teacher_build_ms` and must not be confused with a
production query path.

## Stopping rule

Widths 128 and 256 are conditional follow-ups only. They open if held-out
recall and the subsequent full R4 replay still improve materially through 64
bits without a visible plateau. The present three-seed curve does not meet
that bar: it has a small late gain for the learned method, a frozen-code
cross-over, and a large absolute gap to the float teacher. A gain in prototype
recall alone is not a
product gate: the final address deduplication, local K8, Hamming/ADC cascade,
nDCG, overlap, latency, and footprint must be measured in the next replay.

The global FP32 K8 scan and the exhaustive binary scan remain offline ceilings;
the product objective remains a cheap selector over a bounded shortlist.
