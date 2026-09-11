# Current ANN baseline after post-backlog gate (2026-09-11)

The post-backlog batch is now landed in separate merge PRs #359--#364.  The
receipts and notes preserve the distinction between representation locality,
candidate-generation oracles, and physical/index authorization.

## Current status

* Raw THQ4-384 remains the strongest flat representation: `.999342 @256` and
  `1.0 @1k` teacher survival at 144 B/document.
* Continuous query-to-level interval scores improve exhaustive ranking to
  `1.0 @256`, but do not yet provide a selective index.
* Packed ordinal exact/radius-one schedules and weighted collision voting are
  negative at low work; packed two-bit storage is exact and reduces the
  payload to 96 B/document.
* The corrected progressive replay is positive at the algorithmic level:
  squared THQ-ADC with query-adaptive ordering leaves `.0214` active at 192
  coordinates and `.00332` at 256. Native vertical-layout cost remains open.
* The first independent Gaussian cosine-LSH exact-bucket baseline is also
  negative (`.05 @256` at 41.6k mean candidates); this does not close
  multiprobe or cross-polytope LSH.

## Architectural boundary

The recommended production-facing baseline remains the previously licensed
uniform INT5 final-document codec and conditional R4 representative policy.
None of #359--#364 activates an ANN route, MDBX backend, THQ index, or cosine
LSH path.  `production_activation: false` is asserted in every new receipt.

## Next research gates

1. Native SIMD/tiled packed-THQ scan with real semantic p50/p95 latency.
2. A richer ordinal multiprobe that predicts distant useful cells under matched
   candidate/byte budgets; MDBX is still gated on a positive oracle.
3. Gaussian-hyperplane multiprobe and, if justified, cross-polytope controls.
4. Held-out seeds and query splits for the interval-distance ranking gain.

The final receipt audit is fail-closed and checks every post-backlog receipt
for required schema fields and an explicit false production flag.
