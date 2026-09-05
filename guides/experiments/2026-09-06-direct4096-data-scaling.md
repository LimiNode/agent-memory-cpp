# Direct4096 router data scaling

## Question

Is the gap of the supervised Direct4096 cell router primarily caused by too few
training queries?

## Protocol

The exact E5 teacher cache was extended with 1,000 additional MIRACL query
vectors scored against the frozen DE-1M document matrix.  The cache records the
document hash `d4f67ebe...`, exact teacher ids/scores, and manifest SHA-256
`88b94f070d0fe3f7ddb6b2d606f11fb1ef8cc323dae9c8163ea0193613e5b3d8`.
Direct4096 top-32 training was replayed with 153, 500, and 1,000 training
queries and seeds 13/37/101.  Candidate budgets and exact E5 final scoring were
unchanged.

Runner: `tools/agent-memory-bench/run-direct4096-data-scaling.py`.
Artifact: `tmp/ordinal-scaling-extra-1000/direct-scaling.json`.
Artifact SHA-256: `cccde7cabe2b019a028241c477c874506ad4285057da431e88ff08d711276c6e`.

## Result

At 64k candidates, configuration overlap was approximately `.709-.713` with
153 queries, up to `.721` with 500, and up to `.739` with 1,000.  Internal
overlap stayed nearly flat (roughly `.697-.708` across the matrix).  The best
seed therefore improves the configuration split modestly, but there is no
corresponding internal-set breakthrough.

## Interpretation

Training data scarcity contributes to the observed gap, but it is not the sole
cause.  More labels improve memorization/generalization on the configuration
distribution while leaving the harder internal distribution largely unchanged.
This does not justify a width sweep or a product claim for Direct4096.

## Limitations and follow-up

The extra labels are generated from the same exact E5 teacher and frozen
document basis; this is not a new corpus or independent teacher.  A useful next
test would require more diverse held-out queries and a calibrated objective,
not only more epochs.
