# Bounded best-anchor-in-pool pilot

Date: 2026-09-08. This pilot tests whether choosing the best of a small cosine
screen of anchors inside an IVF pool improves the shared-alpha segment ranking.

For each IVF cell budget M=`1/4/8/16/32/64`, the top four prototypes by query
cosine are screened. Each screened anchor receives an exhaustive segment
ranking; `best_screen` is the maximum teacher top-10 survival among those four
rankings. This is a bounded oracle, not the full-pool ceiling or a serving
measurement.

Corrected 8-query smoke results:

| M | nearest @10k | best-of-4 screen @10k |
|---:|---:|---:|
| 1 | .975 | .975 |
| 4 | .963 | .988 |
| 8 | .988 | 1.000 |
| 16 | .988 | 1.000 |

The pilot validates the metric and shows that selector choice can matter once
the pool contains multiple plausible anchors. A full 152-query run and a
two-stage exact best-anchor ceiling remain required before any product claim.
