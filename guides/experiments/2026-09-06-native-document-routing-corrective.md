# Native document-routing corrective replay

## Question

Does the apparent Direct4096 advantage survive an apples-to-apples routing
policy, the historical 180-epoch training protocol, and a complete native
Hamming@768 -> ADC@64 -> exact@10 cascade?

## Corrections

The original bake-off mixed policies: centroid routes were pure centroid
order, while Direct4096 silently received a PCA fallback.  This replay emits
pure routes and explicit `*_hybrid8` seed-plus-PCA-fallback routes separately.
The Direct4096 checkpoints are retrained with the historical 384 -> 128 GELU
-> 4096 model, 153 training queries, the documented hard-negative ranking plus
weighted BCE objective, and 180 epochs.  The materialization manifest records
the training-array hashes, seed list, optimizer, epoch count, and hashes for
every model tensor.

The native executable is still a scalar calibration harness.  It reports
relative policy cost, not an AVX2/production latency ceiling.

## Frozen evidence

Materialization: `tmp/native-document-routing-bakeoff-v2/manifest.json`.
Materialization manifest SHA-256:
`6dbcc0e0c52334cb5567772e7458df92ce23e7b7d558c89d1afae353ed8dda`.

Replay: `tmp/native-document-routing-bakeoff-v2/corrective-result.json`.
Replay SHA-256:
`5e69421e66dba085ad2be5cbe6d8443f17b4f1d5816b74d690ee05aeda7627f1`.
The full replay used 152 held-out queries, budgets 32k/64k, and one measured
pass per query/policy.  The result includes candidate, Hamming, ADC, final
top-10 overlap, qrels nDCG, p05/worst-query values, and p95 timing.

## Corrected result (64k budget)

| Policy | candidate | Hamming | ADC/final | qrels nDCG | p05 / worst final | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PCA threshold (pure) | .7217 | .6954 | .6684 | .5649 | .30 / .00 | 37.2 |
| PCA centroid K=1 (pure) | .6007 | .5822 | .5586 | .4728 | .20 / .10 | 38.0 |
| E5 centroid K=1 (pure) | .5178 | .5013 | .4816 | .4080 | .20 / .00 | 39.0 |
| E5 centroid K=2 (pure) | .6204 | .5967 | .5730 | .4811 | .20 / .00 | 41.2 |
| E5 centroid K=4 (pure) | .7171 | .6888 | .6599 | .5513 | .30 / .10 | 45.1 |
| E5 centroid K=8 (pure) | **.8053** | **.7724** | **.7382** | **.5959** | **.40 / .10** | 52.3 |
| E5 centroid K=4 + PCA fallback (8) | .7309 | .7046 | .6776 | .5710 | .30 / .00 | 44.6 |
| Direct4096 top-32 (pure) | .327--.336 | .318--.325 | .313--.318 | .325--.339 | .00 / .00 | 39--41 |
| Direct4096 top-32 + PCA fallback (32) | .704--.711 | .678--.684 | .653--.658 | .565--.571 | .30 / .10 | 39--41 |

The Direct4096 ranges are across seeds 13/37/101.  Its pure policy is not a
usable document router at this budget; its earlier result depended on the
unreported fallback topology.  Once the fallback is made explicit, it does
not beat the PCA threshold control and does not approach pure E5 K=8.

## Interpretation

The stage-loss table confirms that routing and downstream codec loss are
separate.  E5 K=8 has the strongest candidate and downstream survival, while
Direct4096 hybrid seeds recover no measurable advantage over PCA fallback.
The corrected replay therefore removes the prior claim that Direct4096 is a
competitive native route.  E5 K=8 remains a quality-oriented research
profile; PCA threshold remains the low-cost control.  These conclusions are
qrels and cascade conclusions, not prototype-cell recall claims.

## Remaining limitation and next check

The replay still uses in-memory postings and scalar Hamming/ADC/exact loops.
Before an MDBX decision, freeze the candidate pools from PCA threshold, E5 K=8,
and (for completeness) Direct4096 hybrid and compare downstream THQ3/THQ4,
Hamming, and ADC kernels on identical pools.  Only the best two or three
combinations should then receive an MDBX-backed replay with AVX2 stage timing,
bytes read, and p50/p95/p99.
