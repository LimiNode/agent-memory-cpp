# Progressive packed THQ-ADC and MDBX-friendly physical layout

This roadmap follows the corrected post-backlog review.  HNSW is retained only
as a scientific ceiling because mutable graph adjacency and dependent random
page reads are a poor fit for the project's MDBX lifecycle.

## Required gates

1. The PQTable-style oracle must use the global coordinate offset when scoring
   every block (`lut[block_lo + local_col, level]`).  Ordinal-L1 and
   interval-squared-ADC are separate state generators, and budgets
   `1k/5k/20k/50k` are snapshots from one traversal.
2. The dynamic cutoff oracle must use a runtime warm-up cutoff, stable
   `(score, document_id)` ties, query-adaptive/ADC-aware orders, exhaustive
   top-256 parity, and coordinate-work accounting.  `evaluated_fraction` is
   not used as a proxy for work; the receipt reports documents reaching each
   checkpoint, fully evaluated documents, total coordinate evaluations, and
   equivalent flat-scan fraction.
3. The LSH oracle reports touched postings and posting entries in addition to
   candidate count and teacher recall.  It remains an in-memory research
   oracle, not a physical index.

## Physical candidate

After a positive frozen DE-1M oracle gate, materialize packed 2-bit ordinal
levels in immutable vertical/AoSoA tiles.  The intended layout is evaluated as
a page-matched sweep, not a single hard-coded tile shape.  In particular,
`512 documents × 32 coordinates × 2 bits` is exactly one 4-KB payload page and
must be included alongside larger and smaller tiles.

```text
tile(documents) -> coordinate block -> packed 2-bit levels
```

R4/K8/K32 selects candidate tiles or MDBX pages; progressive ADC then scans
active documents within those pages.  The current Python runner reports only
logical block-payload bytes and block/doc/coordinate work; it does not claim
OS/MDBX page faults, warm/cold latency, or an R4 cascade.  Those are separate
native follow-ups.  No production activation is implied by a positive oracle.

The materializer is `tools/agent-memory-bench/materialize-progressive-thq-aosoa.py`
and the physical scan runner is
`tools/agent-memory-bench/run-progressive-thq-aosoa.py`.  The layout manifest is
cryptographically bound to the frozen manifest and document-code payload.  Its
receipt remains pending until the external payload is supplied.

The next corrective gate also requires fixed-point (`uint16`/`uint32`) ADC LUT
evaluation to remove floating-point accumulation-order ambiguity from exact
parity claims.  This is a reproducibility strengthening step, not a reason to
materialize data early.

The frozen DE-1M payload is external to this checkout.  Until its manifest and
hashes are materialized, all new receipts remain `PENDING` and no numbers may
be described as authoritative DE-1M evidence.

The first frozen replay is now available as a non-authoritative logical-work
measurement in `2026-09-11-progressive-thq-aosoa-replay.md`.  It covers both
`4096×32` and page-matched `512×32` layouts, with fixed and ADC-expected block
orders.  Native page accounting, warm/cold controls, and R4 candidate masks
remain unexecuted follow-ups.

The physical runner distinguishes the diagnostic unweighted
`adc_lut_variance` order from corpus-weighted `adc_expected_cost` and
`adc_expected_variance`, using the materializer's per-coordinate level
histogram.  Warmup is document-masked, but
the top-k cutoff is only updated after each tile; this is reported as a
tile-granular bootstrap rather than equivalent to the oracle's per-document
warmup sweep.
