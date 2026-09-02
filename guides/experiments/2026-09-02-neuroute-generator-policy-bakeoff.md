# NeuRoute generator policy bake-off

Date: 2026-09-02

## Question

Does any already measured cheap address selector pass the complete frozen R4
cascade at `M <= 4096`, when K1, learned residual, address ANN, frozen
12/14/16 hierarchy, and prototype-ANN controls are compared under one policy?

Global FP32 K8 is an offline teacher/reference. Prototype ANN over K8 rows is a
quality-and-cost control, not the product architecture. The allowed product
path is a cheap address selector, at most 4,096 selected addresses, exact local
K8 inside that bound, then the unchanged R4 candidate/Hamming/ADC/exact cascade.

## Setup

The compact runner binds the validated results and evidence from the shortlist
generator, 8,141-query learned-router, and 12/14-to-16 hierarchy studies. It
uses only their shared configuration partition and drops every `M=8192` row.
The parent native executable hashes differ, so quality is directly comparable
but latency remains directional.

## Result

The validator accepted 57 configuration points at `M=1024/2048/4096`. No
product-eligible point passed at any budget. The most informative `M=4096`
rows are:

| Generator | Policy role | Mean / max-seed nDCG loss | Final top10 overlap | Directional generator + local-K8 p95, ms | Gate |
| --- | --- | ---: | ---: | ---: | --- |
| Prototype IVF | K8-prototype control | .000575 / .001726 | .9996 | 29.56 | pass, ineligible |
| Prototype HNSW | K8-prototype control | .000473 / .001704 | .9969 | 12,387.41 | pass, ineligible |
| K1 + signed delta, 153 queries | learned address selector | .007894 / .026948 | .9662 | 17.09 | fail |
| K1 + signed delta, 8,141 queries | learned address selector | .008950 / .026948 | .9662 | 17.04 | fail |
| Exact address K1 | address selector | .008856 / .026948 | .9654 | 17.11 | fail |
| Address IVF | address ANN | .009942 / .030648 | .9566 | 16.41 | fail |
| Frozen direct 16-bit | frozen learned selector | .017142 / .038780 | .8750 | 16.05 | fail |
| Frozen prefix12 beam 4x | frozen hierarchy | .016523 / .038780 | .8750 | 16.79 | fail |
| Frozen prefix14 beam 4x | frozen hierarchy | .017142 / .038780 | .8750 | 18.22 | fail |

The best eligible near-miss is the 153-query signed-delta residual, with common
gate distance `.050615`. Increasing its training pool to 8,141 queries did not
improve final overlap or worst-seed loss. The prototype-IVF control proves that
the downstream cascade can preserve quality when the shortlist is sufficiently
faithful, but obtaining that shortlist from an ANN index over K8 prototype rows
is outside the selected product architecture.

## Decision

No existing cheap selector is licensed for native or production integration.
The `M=8192` sensitivity rows are absent from this decision, global FP32 K8
remains offline-only, and the passing prototype-ANN rows remain controls.

The preregistered next branch is activated: train 12/14-bit prefix utility heads
jointly from the 8,141-query teacher cache, refine only bounded descendants, and
use exact local K8 for at most 4,096 selected addresses. This directly tests
whether supervised prefix aggregation fixes the frozen-head limitation without
falling back to global K8 scanning.

## Limitations

- This is a bound post-analysis of existing native replays, not a new timing
  run under one executable build.
- The configuration partition has already selected parent hyperparameters.
- Prototype ANN demonstrates attainable shortlist quality but is deliberately
  excluded from product eligibility because it indexes K8 prototype rows.

## Reproduction

The ignored compact result is stored under
`tmp/neuroute-generator-policy-bakeoff/`.

- Result SHA-256: `8b40e04e2cfa4e19a9c4c315077760e905bfa88602bcec2fb71b459705dfb662`
- Evidence SHA-256: `41905058550d1d846c99b2a38de3fbe1ad347e731413adb35f6f3db95609ec4d`
