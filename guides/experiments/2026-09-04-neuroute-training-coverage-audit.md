# NeuRoute training coverage and initialization audit

Date: 2026-09-04. Follow-up to PRs #285/#286 and the asymmetric-hashing
review. The audit checks whether the sparse prototype codebook is actually
trained, whether hard-negative mining continues the same query encoder, and
whether semantic initialization changes the conclusion.

## Audit result

With 4,070 training queries, eight positive ranks, two fixed negative ranks,
and eight random negatives, only 45,942 of 454,322 prototype rows are touched
(10.11%). 408,380 rows (89.89%) remain untouched. Occurrence statistics are
mean `0.161`, median `0`, p95 `1`, max `88`; this is a sampled-row coverage
problem, not a balanced global codebook update. Code entropy is 0.999998 over
all rows, 0.999961 on touched rows, and 0.999998 on untouched rows, confirming
that global entropy cannot be used as evidence that the whole map was learned.

The audit is implemented by `audit-neuroute-training-coverage.py`. It reports
first-round coverage exactly from the teacher/random sampling and accepts an
optional hard-negative ID artifact when one is available.

## Corrected hard-negative continuation

The previous round constructed a new query encoder after mining hard negatives,
discarding the mined model parameters. The runner now copies the model state
into the second round (`continued_model: true`) while retaining the code table.
This is genuine fine-tuning, although the optimizer state is intentionally
reset and remains a separate limitation.

## Projection-init control

All prototype codes can now be initialized from a deterministic orthogonal
projection with per-coordinate median thresholds. A three-seed 64/128-bit
pairwise control produced 128-bit held-out recall@4096 of `0.30474`, `0.34932`,
and `0.36320` (mean `0.33909`), modestly above the erroneous #285 mean `0.32840`.
The hard round continued the same model in every cell.

However, the best projection cell (128-bit, seed 287) still failed address
utility:

| prototype budget | prototype recall | candidate docs | final top10 overlap |
| ---: | ---: | ---: | ---: |
| 1,024 | 0.05998 | 19,605 | 0.03750 |
| 2,048 | 0.10457 | 36,839 | 0.06776 |
| 4,096 | 0.17186 | 68,800 | 0.11513 |
| 8,192 | 0.27967 | 125,967 | 0.20658 |

P05 and worst-query final overlap are zero at every budget. Thus better
initialization and correct hard-negative continuation improve prototype
geometry but do not license native local-K8 or the full R4 cascade.

## Decision

The original #286 listwise result must not be interpreted as a capacity limit,
because it had both incomplete codebook coverage and a weak sampled objective.
The corrected projection control is a stronger diagnostic, but its address gate
still fails. Stop global neural-hashing expansion for now. The next meaningful
study is a non-neural local index (frozen float IVF coarse navigation plus
residual INT8/ITQ/PQ) or a genuine document-utility/alternating discrete-code
optimizer with global code updates.

Raw reports remain under `tmp/` and are not committed.
