# NeuRoute width by scale by budget frontier

Date: 2026-08-28. Protocol PR; measurements are intentionally absent.

## Question

Can independently trained 14-, 16-, or 18-bit semantic-address heads reduce
the 1M routing candidate mass and native Hamming work relative to 12 bits while
preserving the frozen cascade quality?

This study does not append random or post-hoc bits to the frozen 12-bit model.
Each width trains a complete output head under the same raw-Euclidean mined-pair
recipe, seeds, German 25k corpus, and frozen training-query partition. The
configuration/evaluation queries are forbidden during model and probe-budget
selection.

## Matrix

Widths 12, 14, 16, and 18 are crossed with three frozen seeds. Calibration on
the training-query partition evaluates 64, 128, 256, 512, 1024, 2048, and 4096
probes and selects one shared budget per width. The selection is the smallest
budget satisfying the frozen survival, quality, and 10% candidate gates for all
seeds; a deterministic best-survival fallback is recorded if no budget passes.

Evaluation uses the separate 76-query configuration partition on the nested
German 25k, 100k, and 1M collections. Every route reports the fixed 256-probe
mechanism row and the calibration-selected row; when the budgets coincide one
physical row carries both roles.

## Native measurement

The native MDBX evaluator replays the exact Python candidate, Hamming, ADC, and
final E5 sequences. Eighteen-bit addresses require the declared v2 key layout
with a big-endian 32-bit address field; postings remain packed little-endian
document positions. Timing retains the corrected hardware-popcount Hamming
decomposition from the frozen scale-transfer study.

Only calibration-selected rows may select a width. A width must satisfy the
candidate, ADC-survival, and exact-quality gates at every scale and seed. The
winner is then the passing width with the lowest maximum DE 1M native total
p95. Fixed-256 rows explain the width mechanism and cannot win the production
decision directly.
