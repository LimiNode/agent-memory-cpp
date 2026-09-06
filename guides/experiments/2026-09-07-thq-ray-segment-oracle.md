# Continuous THQ ray/segment geometry oracle

Date: 2026-09-07. This corrective experiment follows the review of the
directional-MIH study. The earlier transition-cost runs tested an additive
per-coordinate surrogate; this run tests the original geometric hypothesis
with one shared progress parameter along a semantic segment.

## Protocol

For each of 152 DE queries, K8 prototype teacher targets provide an offline
anchor. For one anchor, the score for prototype `x` is the squared Euclidean
distance to the segment `q + alpha * (p - q)`, `alpha in [0, 1]`. The optional
four-anchor arm takes the minimum distance to four teacher-prototype segments.
All 454,322 prototypes are scored exhaustively; no THQ code, MIH table or
continuous prefilter is used. The reported survival is membership of the
teacher top-10 prototypes in the best `K` geometric candidates. Runner:
`tools/agent-memory-bench/evaluate-thq-ray-segment-oracle.py`.

## Result

| geometry | K=256 | K=512 | K=1,024 | K=2,048 | K=5,000 | K=10,000 |
|---|---:|---:|---:|---:|---:|---:|
| one teacher anchor | .9020 | .9289 | .9526 | .9678 | .9776 | .9816 |
| four teacher anchors, min segment distance | not recomputed in this correction | | | | | |

These are routing-ceiling figures, not final retrieval nDCG. The one-anchor
10k value is the mean over all 152 queries.

## Interpretation

This result materially corrects the previous status. The additive ordinal
transition-cost formulation is still closed (`.025/.038/.116` recall/survival
frontiers), but the true shared-alpha ray/segment geometry is not disproven.
It reaches .9816 teacher top-10 survival at 10k with a single anchor. That is
below the current .995 promotion gate, yet close enough to justify a dedicated
discrete approximation study rather than declaring all directional sectors
invalid.

The four-anchor minimum is worse because it broadens the geometric acceptance
region and admits more off-manifold prototypes. More anchors are therefore not
a free quality improvement; they need a calibrated mixture or posterior model.

## Limitations and next gate

Teacher anchors are unavailable at serving time, and exhaustive FP32 segment
scoring is only an oracle. The experiment does not measure THQ-top-256 recall,
bytes, random reads or p95/p99. The next bounded study should approximate the
shared-alpha score using ordinal threshold crossing events, then test an
empirical a-posteriori transition distribution on held-out queries. A physical
selective index remains disallowed unless that discrete approximation reaches
the predeclared quality gate with materially less work than a sequential THQ
scan.
