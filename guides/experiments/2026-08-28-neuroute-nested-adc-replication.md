# NeuRoute nested multi-seed ADC replication

Date: 2026-08-28. Frozen protocol and completed measurement.

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

## Result

The complete result and all projection/statistics/ranking identities replayed
byte-for-byte. The single-seed quality suggested by #214 does not replicate.

| Width | All-seed mean loss | Std | p10 | p50 | p90 | Calibration-selected loss |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | .03872 | .00533 | .03301 | .03972 | .04373 | .04181 |
| 768 | .02446 | .00578 | .01884 | .02346 | .03099 | .02362 |
| 1024 | .02232 | .00445 | .01891 | .02073 | .02652 | .02357 |
| 1536 | .01717 | .00565 | .01006 | .01731 | .02320 | .01072 |
| 2048 | .01309 | .00373 | .00767 | .01428 | .01710 | .01748 |
| 3072 | .01144 | .00388 | .00678 | .01181 | .01569 | .01512 |
| 4096 | .00980 | .00416 | .00332 | .01119 | .01377 | .01326 |

No calibration-selected seed passes the mean `.003` and per-dataset `.0075`
gate. Width continues to help on average, but the gain is not enough and seed
variance remains material. Calibration on DE training queries also transfers
poorly: the selected 2048/3072 seeds are worse than the all-seed means.

For diagnosis only, the best held-out 4096 seed has mean loss `.00288`, but it
was not calibration-selected and its JA-25k loss is `.00863`. Thus even an
impermissible held-out seed cherry-pick would still fail the predeclared
per-dataset gate.

Result SHA-256:
`4c44765d9ec274ef37c6fab605295bda4ea96bc4aa821a2868fb4b19f9e7d4aa`.
Evidence SHA-256:
`577f7360a4a2ae22a14e61ef21190b668615c6681986fe49a268cd73764c6979`.

## Interpretation

The earlier ADC2048 result was a useful ceiling observation, not a stable
deployable codec. Random overcomplete ADC should not receive a physical
implementation benchmark under the frozen protocol. A learned final reranker
or a different structured quantizer remains a separate research direction;
adding more random widths or selecting a favorable held-out seed is not
justified.

## Evidence

The result binds the #214 result/evidence, the #205 materialization manifest,
all input manifests and frozen pools, master/prefix projection hashes, per-seed
ADC statistics, calibration rows, and paired held-out ranking hashes. The
evidence writer reruns the complete experiment and requires byte-identical JSON.

Generated JSON remains local under `tmp/neuroute-nested-adc-replication/` per
the raw artifact policy.
