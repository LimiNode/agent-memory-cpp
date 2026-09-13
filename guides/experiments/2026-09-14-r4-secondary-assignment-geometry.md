# R4 secondary-assignment geometry and capacity gate (2026-09-14)

## Question

Can the existing full-dimensional R4 postings be treated as VQ-like semantic
cells for a SPANN-style closure or a SOAR-style secondary assignment?  Before
materializing replicas, this gate measures the actual E5 geometry of the R4
postings and a teacher-only placement ceiling.

## Protocol

The frozen DE-1M fixture and the three materialized R4 seeds are unchanged.  For
each occupied address, the runner computes the mean of the normalized frozen E5
document vectors in that posting and L2-normalizes the mean.  It then reports
cosine distance from a deterministic sample of 10,000 corpus documents plus all
1,520 teacher occurrences (11,484 unique documents), nearest-centroid distance,
the rank of the document's primary R4 centroid among all occupied centroids, and
posting dispersion.  Route order is reconstructed from the frozen model-ranked
1,024-address prefix.

For every teacher/query pair whose primary address is absent from that prefix,
the teacher-only oracle ranks occupied addresses by true E5 centroid distance.
At `M={8,16,32,64}`, it asks whether at least one of those alternatives is
already visible in the query's frozen prefix and records the best route rank and
cumulative posting-entry cost.  Teacher IDs are used for this capacity oracle
only; no replica is materialized and no teacher-derived assignment is proposed
for serving.

Command:

```text
python tools/agent-memory-bench/run-r4-secondary-assignment-geometry.py \
  --thq-manifest <frozen-thq-manifest> \
  --r4-manifest <r4-materialization-manifest> \
  --r4-root <r4-materialization-root> \
  --output guides/experiments/2026-09-14-r4-secondary-assignment-geometry-result.json \
  --raw-output <external>/r4-secondary-assignment-geometry-raw.json
```

The fail-closed audit is
`tools/agent-memory-bench/audit-r4-secondary-assignment-geometry.py`.

## Results

| seed | primary-centroid rank p50 / p95 | primary/nearest distance ratio p50 / p95 | posting dispersion p50 |
| ---: | ---: | ---: | ---: |
| 2701 | 17,779 / 59,663 | 1.663 / 2.454 | .1082 |
| 2702 | 16,423 / 59,488 | 1.659 / 2.437 | .1077 |
| 2703 | 11,266 / 58,380 | 1.595 / 2.386 | .1082 |

The primary R4 centroid is therefore usually far from the nearest E5 centroid;
the address behaves like a routing code, not a semantic VQ cell.  Posting
dispersion is on the same scale as the nearest-centroid distance, rather than
being a tight residual around a meaningful primary center.

Teacher-only recoverability among primary-prefix misses:

| seed | misses | M=8 | M=16 | M=32 | M=64 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2701 | 139 | .2374 | .3309 | .4820 | .6691 |
| 2702 | 148 | .2095 | .3378 | .4865 | .7162 |
| 2703 | 152 | .3224 | .5263 | .6776 | .8487 |

These are capacity upper bounds with teacher leakage, not measured assignment
quality.  Even `M=64` remains below `.85` on the strongest seed and far below the
`.99` placement gate.  The best visible alternatives, when present, occur at a
median route rank of 155--279 and require roughly 5k--10k cumulative posting
entries, so “early” is not synonymous with a cheap secondary copy.

## Decision

The centroid geometry gate is negative.  Do not materialize a centroid-based
SPANN closure or apply the SOAR residual formula to raw R4 addresses: the
required VQ-cell premise is absent.  The next justified route is a
representative-aware assignment (K32/K8 representative scores) or a genuinely
new multi-anchor topology.  THQ-ADC/MDBX cascade activation remains gated.

## Limitations and provenance

This is a logical geometry/capacity oracle.  It does not measure physical pages,
MDBX latency, replica footprint, or serving-time assignment.  The capacity
rows are explicitly teacher-leaking and cannot be used as a production recall
claim.  The frozen input manifest SHA is
`f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b`; the R4
materialization manifest SHA is
`95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`.
The compact receipt is
`2026-09-14-r4-secondary-assignment-geometry-result.json`; the external raw
bundle has SHA-256
`c6713965fc2c839e4b0f9e477510488c0a861fd47d531f1c6cdea2733dbbe365`.
