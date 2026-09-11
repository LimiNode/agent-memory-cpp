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
* The corrected independent Gaussian cosine-LSH exact-bucket baseline is
  negative as a generator: five-seed mean union recall is `.2009`, at 24k--75k
  mean candidates, with zero-recall worst queries. The former `.05 @256`
  measured hash-Hamming reranking and is retained only as a diagnostic.
  Margin multiprobe and cross-polytope LSH remain open.

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

The final receipt audit is fail-closed and checks all eight post-backlog
receipts: active receipts carry the frozen-fixture, runner, raw-result,
query-count, and protocol bindings; historical receipts explicitly declare
their superseded/incomplete status. External raw reports may be absent from a
checkout, but their hashes remain mandatory. Every receipt has an explicit
false production flag.
