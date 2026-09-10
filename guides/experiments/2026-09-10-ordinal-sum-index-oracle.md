# 2026-09-10 ordinal block-sum index oracle

This follow-up tests one concrete ordinal-aware candidate generator after the
THQ4 absolute-geometry gate. It is an algorithmic oracle, not a persistence or
production benchmark. The frozen DE-1M fixture has 1,000,000 documents, 152
semantic queries, and exact E5 top-10 teacher IDs. Query and document codes
are the canonical raw THQ4-384 representation (four levels, three thermometer
bits per coordinate), with deterministic `(distance, document_id)` ordering.

## Method

The flat arm exhaustively computes THQ Hamming/ordinal-L1 distances. The
ordinal arms split the 384 coordinates into `m = 8, 12, 16, 24, 32` contiguous
blocks. Each posting key is the exact sum of ordinal levels in a block; a
query unions the postings for its exact per-block sums, deduplicates IDs, and
then applies the same THQ distance ranking. This is deliberately a simple,
data-independent ordinal multi-index control. Candidate budgets are
`256/512/1k/2k/5k/10k/20k/50k`; when an exact-sum union exceeds a budget, the
shortlist is cut by deterministic THQ distance.

The full JSON output is retained outside the repository:

`postbacklog-ordinal-index-full.json`

SHA-256: `7b6747d2b6aa6bf51147affdab76f76b46e8db4f476e0063aa221349ef640031`.
The runner source is hashed in the compact receipt. The Python timings below
are diagnostic only; the locked native flat reference remains 20.994 ms/query
p50 from the preceding gate.

## Full 152-query result

| system | mean unique candidates | mean postings touched | @256 survival (mean / worst) | @50k survival (mean / worst) |
|---|---:|---:|---:|---:|
| flat | 1,000,000 | 1,000,000 | .999342 / .9 | 1.000000 / 1.0 |
| ordinal sum m=8 | 267,249 | 305,384 | .336184 / .0 | .336184 / .0 |
| ordinal sum m=12 | 433,370 | 555,382 | .541447 / .1 | .541447 / .1 |
| ordinal sum m=16 | 567,852 | 818,346 | .715789 / .3 | .715789 / .3 |
| ordinal sum m=24 | 795,100 | 1,534,207 | .891447 / .6 | .892105 / .6 |
| ordinal sum m=32 | 911,364 | 2,333,240 | .963816 / .8 | .963816 / .8 |

The exact-sum unions are already hundreds of thousands of documents, and the
teacher survival barely changes from a 256 to a 50k shortlist because the
missing teacher documents are absent from the union itself. Even m=32 does
not reach the flat locality ceiling while touching roughly 91% of the corpus
and 2.3M posting entries per query.

## Interpretation and next gate

This closes only the **exact ordinal block-sum key** as a useful candidate
generator on this fixture. It does not close packed ordinal subvector keys,
multi-probe schemes that enumerate L1-neighbouring block states, or every
ordinal-aware index. The result reinforces the previous geometry finding:
excellent global THQ rank-locality does not imply that a coarse posting key can
enumerate the useful neighbourhood cheaply.

Before any MDBX implementation, the next oracle should compare a richer
coordinate-aware key/multiprobe against this locked flat baseline and include
an ordinary bit-MIH control on the same semantic queries. No ANN index or
production activation is licensed by this experiment.
