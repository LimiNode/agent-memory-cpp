# 2026-08-11 MIH ADC ceiling and stage-loss study

## Question

For a fixed budgeted-confidence 32x8 MIH candidate union, is the current
binary ADC scorer already close to the useful second-stage ceiling, or does a
larger scorer gap justify an asymmetric continuous or residual follow-up?

## Frozen setup

The held-out MIRACL Russian E5 root has 22,607 documents and 1,252 queries;
the disjoint calibration root has 25,000 vectors. Every row uses 256-bit ITQ,
50 ITQ iterations, seeds 42--46, and 32 equal 8-bit bands. Candidate generation
is fixed to budgeted-confidence probing with the shared optional-probing target
pairs `(8192, 11000)`, `(12288, 19000)`, and `(16384, 30000)` for candidates
and posting visits.

Each target/seed row computes the complete diagnostic grid without retraining
the ITQ projection:

```text
Hamming K1: 512 / 768 / 1024 / 1536
K2:          64 / 128 / 256 / 512
scorer:      Hamming prefix
             binary ADC over hard document codes
             continuous pre-sign ITQ-projection L2
             exact E5 order within the same Hamming K1
```

The final two scorers are diagnostic ceilings, not document-payload or serving
latency claims: they read retained continuous projections or original E5
embeddings for the already selected Hamming shortlist. All comparisons use the
same E5-oracle top-10 survival funnel from raw MIH union through Hamming and
K2.

The matrix contains 15 target/seed reports. Each report contains 64 cells
(`4 K1 x 4 K2 x 4 scorers`) and paired per-query contributions for every cell.

## Decision rule

If binary ADC is close to exact-E5-within-Hamming across the practical K1/K2
region, freeze ADC and move the next research line to true variable-width MIH
bands or MIH-aware ITQ. If a material gap remains, evaluate a bounded
asymmetric continuous/residual scorer before changing the code-learning stage.

## Result

The frozen matrix completed with all 15 target/seed rows and 480 predeclared
paired bootstrap comparisons (10,000 replicates each). The compact K1=768
slice below reports five-seed mean E5-oracle top-10 survival. The continuous
projection and exact-E5-within-Hamming diagnostic rows have identical means in
this slice, so they are shown together as the within-Hamming ceiling.

| MIH candidate target | K2 | Binary ADC | Diagnostic ceiling | Ceiling - ADC |
| ---: | ---: | ---: | ---: | ---: |
| 8,192 | 64 | 0.949888 | 0.962412 | +0.012524 |
| 8,192 | 128 | 0.958898 | 0.962412 | +0.003514 |
| 8,192 | 256 | 0.961821 | 0.962412 | +0.000591 |
| 8,192 | 512 | 0.962396 | 0.962412 | +0.000016 |
| 12,288 | 64 | 0.971661 | 0.987780 | +0.016118 |
| 12,288 | 128 | 0.982812 | 0.987780 | +0.004968 |
| 12,288 | 256 | 0.986789 | 0.987780 | +0.000990 |
| 12,288 | 512 | 0.987764 | 0.987780 | +0.000016 |
| 16,384 | 64 | 0.974728 | 0.992109 | +0.017380 |
| 16,384 | 128 | 0.986821 | 0.992109 | +0.005288 |
| 16,384 | 256 | 0.991102 | 0.992109 | +0.001006 |
| 16,384 | 512 | 0.992109 | 0.992109 | +0.000000 |

At the practical K2=256 operating point, binary ADC is therefore close to the
diagnostic within-Hamming ceiling: the observed gap is 0.000591--0.001006 in
this K1=768 slice. This does not support broadly replacing ADC. At tight K2=64
the gap is materially larger (0.012524--0.017380), with K2=128 between those
regimes. A bounded asymmetric continuous or residual scorer is consequently a
targeted follow-up for tight second-stage budgets, rather than a replacement
for the existing K2=256 path.

The evidence archive is staged as a draft release while this PR is under
review. It contains the 15 reports, 15 per-query NPZ contributions, the 480
paired bootstrap reports, matrix configuration, source snapshots, and compact
and full manifests. Its archive SHA-256 is
`1ebc52ca0f85440a682d9ecf36c4e5c71d8c7740088caef1324dfcaea37aa641`; the
internal bundle-root SHA-256 is
`37fd169c01561880f847a830ae259d993443771257a303940bcc8e2c1e1edcf7`.

## Limitations and follow-up

The continuous and exact rows are diagnostic ceilings only: retained
continuous document projections and original E5 vectors are not the compact
serving representation. The study measures oracle survival, not production
latency or payload. The next branch should decide whether a bounded residual
or asymmetric continuous scorer can recover a useful fraction of the K2=64/128
gap without invalidating the compact binary pipeline.
