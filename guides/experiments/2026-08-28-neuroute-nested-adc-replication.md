# NeuRoute nested multi-seed ADC replication

Date: 2026-08-28. Frozen protocol; measurement pending.

## Question

Was the apparent ADC2048/4096 quality frontier in #214 robust, or was it an
artifact of one projection seed and independently generated widths?

## Protocol

Eight predeclared projection seeds each create one `384 x 4096` Rademacher
matrix. Widths 512, 768, 1024, 1536, 2048, 3072, and 4096 are strict column
prefixes of that matrix. This removes the old `seed + width` confound and makes
every within-seed width comparison genuinely nested.

The held-out input remains the exact same frozen ADC256 top-64 pools from
#205 for DE-25k, FR-25k, JA-25k, and DE-1M. FP32 and every ADC representation
rank the same 64 document IDs. No routing, Hamming, pool, qrel, or query split
changes in this PR.

Projection-seed selection is isolated from held-out queries. For each width,
the selected seed is the lowest-loss seed on the 153 frozen DE training queries,
using global exact-E5 top-64 pools. Held-out results report all eight seeds plus
mean, standard deviation, p10, p50, p90, and per-dataset losses. They may not be
used to replace the calibration-selected seed.

The physical ADC benchmark is licensed only if the calibration-selected seed
has mean held-out nDCG@10 loss at most .003 and every dataset loss at most
.0075. Passing this research gate is not a production selection.

## Evidence

The result binds the #214 result/evidence, the #205 materialization manifest,
all input manifests and frozen pools, master/prefix projection hashes, per-seed
ADC statistics, calibration rows, and paired held-out ranking hashes. The
evidence writer reruns the complete experiment and requires byte-identical JSON.
