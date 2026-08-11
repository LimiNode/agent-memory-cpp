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

Pending the frozen 15-row matrix.
