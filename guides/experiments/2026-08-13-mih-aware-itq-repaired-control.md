# Repaired MIH-aware ITQ calibration control

## 2026-08-13 — fixed calibration protocol

### Question

Can a small, calibration-only semantic refinement preserve the balanced global
geometry of full-25k ITQ while making document-only E5 neighbours closer in
the downstream Hamming metric? This is a repaired control, not a retry of the
closed v1 objective: it has no MIH-work loss and cannot access held-out queries,
qrels, or an evaluation root.

### Frozen protocol

For five ITQ seeds, the treatment initializes from production full-25k ITQ.
It runs eight fixed epochs, uses bipolar code similarity
`(2b-1)^T(2b-1)/256`, anchors the projection to initial ITQ with weight 50,
and recalibrates thresholds to full-calibration medians after every epoch. The
final epoch is fixed in advance; there is no checkpoint or parameter selection.

The paired calibration diagnostic measures bit/band geometry, radius-one
candidate and posting work, and exact E5 calibration-neighbour Hamming
distance. Raw per-document and per-pair arrays are retained as release evidence.

### Gate

The control passes only if its five-seed mean satisfies all conditions:

| Property | Required value |
| --- | ---: |
| mean bit entropy | at least 0.99 |
| radius-one candidate work | at most 1.02× full ITQ |
| radius-one posting work | at most 1.02× full ITQ |
| E5-neighbour Hamming | strictly lower than full ITQ |

A pass does not establish held-out retrieval quality. It authorizes only a
separate predeclared held-out frontier, including `16 x 16` radius controls.

## 2026-08-13 — five-seed calibration result

The fixed calibration protocol passed all four gates. The means below are paired across
the five frozen ITQ seeds; this calibration-only result did not read an
evaluation root, queries, or qrels.

| Metric | Full-25k ITQ | Repaired control | Gate / change |
| --- | ---: | ---: | ---: |
| mean bit entropy | 1.000000 | 1.000000 | at least 0.990000 |
| radius-one unique candidates | 16,867.31 | 16,815.95 | 0.996955×; at most 1.020000× |
| radius-one posting visits | 29,270.61 | 29,651.40 | 1.013009×; at most 1.020000× |
| E5 calibration-neighbour Hamming | 90.7113 | 89.2710 | -1.4403; strictly lower |

The bipolar semantic target, full-calibration threshold recalibration, and
strong ITQ anchor preserve the previously lost code balance and radius-one
work while improving the calibration-neighbour Hamming metric. This repairs
the geometry failure observed in the first MIH-aware ITQ path, but it is not a
retrieval claim and does not select a production encoder.

The next, separately predeclared experiment may therefore use a held-out
evaluation root to test a small frontier, including the `32 x 8` radius-one
control and `16 x 16` radius `48`, `56`, and `64` controls. It must retain the
same fixed repaired artifact family and keep any frontier decision distinct
from this calibration gate.
