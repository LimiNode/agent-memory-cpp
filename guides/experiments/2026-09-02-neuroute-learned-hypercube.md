# Prototype-only learned binary hypercube ceiling

Date: 2026-09-02. This is an independent follow-up to the semantic-anchor
relocation ceiling and does not implement a neural query router.

## Hypothesis

Semantic K8 modes may admit a binary code geometry designed for their teacher
neighbourhoods that is healthier for Hamming search than a frozen ITQ code.
The experiment learns prototype codes only. Query codes are the bitwise
majority of teacher-positive prototype codes, so the result is a representation
ceiling rather than an actionable routing claim.

Each iteration updates prototype bits from teacher-positive votes and enforces
per-bit balance with a deterministic median/tie-break.  The report includes
teacher top-k recall, Hamming radius quantiles, bit entropy, inter-bit
correlation, occupancy-relevant code size, and frozen-vs-learned rows.

The NPZ may provide `teacher_positive_ids` (at least eight IDs per query).
For bounded inputs the runner can derive these IDs, but it refuses to build an
unbounded query-by-prototype score matrix; this keeps the experiment separate
from a global prototype-scan product path.

## Decision rule

The output always sets `production_selection_licensed=false` and
`metric_router_followup_licensed=false`; a later review may activate the latter
only after an external materialization and full R4 replay confirms a positive
quality/work frontier.  A gain in this ceiling does not imply that a 450k-way
prototype scan or a global 65k-address scan is a product path.
