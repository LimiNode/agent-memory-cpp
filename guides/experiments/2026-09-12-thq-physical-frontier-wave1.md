# THQ physical-frontier wave 1

Date: 2026-09-12  
Fixture: frozen `thq-full-scan-v2` (1,000,000 documents, 152 queries,
THQ4-384).  The materialized 512×32 AoSoA layout and its immutable summary
manifest are bound to the frozen layout SHA; payload files remain external.

This wave covers four requested questions: Block-Min THQ-ADC metadata,
semantic physical locality, bitmap/range selection with secondary-layout
controls, and an integrated coarse-tile → exact THQ-ADC cascade.

## Block-Min metadata

`materialize-thq-block-min-metadata.py` stores one four-level presence mask per
coordinate and block.  The resulting lower bound is safe:

```text
sum_i min_{level in S(block,i)} ADC_i(query, level)
```

The fail-closed smoke replay (`run-thq-block-min-oracle.py`, query 0,
`execution_status: EXECUTED_SMOKE`) preserved
exact top-256 parity and teacher survival `1.0`, but skipped **0/1,954 tiles,
0/23,448 blocks**, reading 96,000,000 logical payload bytes plus 750,336 bytes
of summaries.  On document-ID order, presence masks are therefore too loose to
support useful pre-I/O Block-Min pruning on the tested query/layout.  This is a valid negative result, not
a page-saving claim.  A follow-up 32-query diagnostic found every marginal
coordinate mask equal to `0xF` (`fraction_marginal_masks_1111 = 1.0`).  After
correcting the upper-tail LUT direction, pairwise joint summaries use the same
384 B/tile budget but are zero for 99.95% of tile/query pairs (about 1.97 unique
values per query).  Four-coordinate summaries cost 3,072 B/tile (~6 MB total)
and produce nearly one distinct value per tile (about 1,953 unique values per
query), yet teacher-tile rank remains near random (median 1,040.5, p90 1,750).
Thus joint occupancy removes the implementation bug but still does not provide
a useful ranking signal in this layout.  A tighter hierarchy (semantic tiles,
posting/range
metadata, or learned bounds) is required before a native page experiment.

## THQ code-prefix physical-order control

The companion materializer writes a 144 MB secondary packed representation in
deterministic ascending first-eight-byte order.  This is a code-prefix control,
explicitly not a semantic or R4 mapping, and does not use teacher IDs to
build the order.  In the eight-query smoke, teacher tile span changed
from document-ID order `[2,9,5,4,9,8,10,10]` to surrogate order
`[10,10,8,8,10,10,10,10]`; locality did not improve.  No semantic reorder is
licensed by this control.  The secondary payload is external and its logical
payload hash is recorded in the generated layout manifest
(`9ec1b81a2a103ae38926429fdc7192db803a39452d40c9a0e3c1499a15ad8146` for the
144,000,000-byte payload; order vector
`1e90908090e2d4b072f84211d6b395de40c32937d2741931fec906a41c34fac0`).

## Block-Min tile-ranking oracle, bitmap/range storage, and secondary representations

The runner treats each 512-document tile as an immutable range and selects
tiles by the Block-Min bound for budgets 1k/5k/20k/50k.  Across all eight smoke
queries, selected candidates had `0.0` teacher recall at every budget; exact
THQ reranking inside those candidates consequently had `0.0` teacher survival.
This negative result shows that the current bound-based tile-ranking oracle does not form a
useful bitmap/range index; it does not rule out bitmap/range storage with a
stronger coarse router.  Existing 256×64 and 128×128 AoSoA materializations remain
secondary columnar controls, but are logical payload layouts only; OS/MDBX
pages, cold/warm latency, and simultaneous replica serving were not measured.

## Hybrid cascade

The same run executes `coarse tile selection → exact interval-squared THQ-ADC`
on the selected tiles, with deterministic `(score, document_id)` ordering.
Because the coarse selector had zero candidate recall in the smoke, the cascade
also had zero recall.  This is an end-to-end negative control for the current
Block-Min/range selector, not evidence against the general R4→THQ architecture.
No historical R4 postings or teacher IDs were used as a substitute for a
verified frozen-corpus mapping; production activation remains forbidden.

## Reproducibility and next gate

Results are in `2026-09-12-thq-block-min-oracle-result.json` and
`2026-09-12-thq-physical-frontier-wave1-result.json`.  They record fixture,
layout, summary, and runner SHA-256 values and explicitly state logical-byte
semantics.  The physical frontier is therefore currently:

* Block-Min metadata: safe but ineffective on document-ID tiles;
* code-prefix semantic surrogate: no locality gain;
* bitmap/range selector: negative at 1k–50k budgets;
* integrated cascade: negative because its coarse selector is negative.

Before wave 2 (IMI/SPANN-like layouts), the highest-value follow-up is a true
query-dependent semantic/R4 page mapping or a tighter multi-level bound.  Only
after that oracle shows candidate recall should an MDBX/native page backend be
materialized.  `production_activation: false`.
