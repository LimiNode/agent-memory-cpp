# R4 representative-max anchor route (2026-09-14)

## Question

If raw R4 addresses are not semantic centroids, can a query rank addresses by
the maximum E5 cosine similarity to their existing document representatives?
This is a teacher-free multi-anchor control using only frozen
`representative_documents` sidecars.

## Protocol

For each query and each of the three materialized R4 seeds, the runner scores
the query against every existing representative vector in that seed.  An
address receives the maximum score of its representatives; addresses are then
read in descending score order and complete postings are consumed under a
global unique-document budget.  A fourth arm globally merges the three score
streams by score, de-duplicates document IDs, and uses the same budget.  Teacher
IDs are evaluation-only and exact payload reranking is not performed.

This is intentionally an end-to-end logical route control, not a claim that the
representative score pass is cheap.  The receipt records the full number of
representative vectors scored per query.

## Results

| arm | @5k | @10k | @20k | @50k | @100k | representative vectors scored/query |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| seed 2701 max-representative | .9730 | .9803 | .9862 | .9914 | .9947 | 891,610 |
| seed 2702 max-representative | .9750 | .9809 | .9882 | .9928 | .9974 | 876,204 |
| seed 2703 max-representative | .9796 | .9868 | .9895 | .9941 | .9954 | 898,743 |
| three-seed score fusion | .9993 | .9993 | .9993 | .9993 | 1.0000 | 2,666,557 |

The three-seed fusion reaches the `.99` gate already at 5k candidates and
`.999342` mean teacher recall at 50k (p05 `1.0`, minimum `.9`).  At 50k it
touches 57,245 posting entries on average with duplication ratio 1.145; at
100k it reaches 1.0 mean recall with 120,389 posting entries and duplication
ratio 1.204.

The quality result is positive but the cost qualifier is decisive: the route
generator performs a full 2.67M-representative similarity pass per query for
the three-seed arm, more vector work than the 1M-document corpus.  It is
therefore a quality/control frontier, not yet a deployable ANN generator.

## Decision and next gate

This is the first current R4 arm that clears the `.99 @ 50k` quality gate, so
the THQ-ADC cascade is no longer blocked by route quality alone.  Before
claiming a product direction, sweep representative prefixes `K=1/4/8/16/32`
per address and measure the recall-versus-representative-score-work frontier.
Only a prefix that preserves the gate at substantially lower work should move
to progressive THQ-ADC, physical layout, and MDBX measurement.

## Limitations and provenance

The score pass is a dense logical computation; no native SIMD, MDBX pages,
latency, cold/warm I/O, or exact rerank was measured.  The route is not a
SPANN/SOAR implementation and does not materialize replicas.  Frozen manifest
SHAs are THQ `f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b`
and R4 `95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`.
The external raw bundle SHA-256 is
`523f424936a0021718aa23c201471c5a8c1fb26aa415e217ce467a7ae9b21020`.
