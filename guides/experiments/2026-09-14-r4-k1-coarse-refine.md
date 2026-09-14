# K1 coarse address scan with K16 refinement (2026-09-14)

## Question

Can the expensive full K16 representative route be replaced by a cheaper
deterministic two-stage router: score one coarse vector per occupied address,
refine only the top `A` addresses with their K16 representatives, then fuse
the three R4 posting streams under the existing unique-document budgets?

Two coarse vectors are compared on the same frozen topology:

- `first`: the first document representative at each address;
- `mean`: the FP32 mean of that address's clipped K16 representatives.

The second mode is a centroid-like control, not a trained centroid index.

## Setup

- frozen DE-1M: 1,000,000 documents, 152 queries, 384 dimensions;
- three semantic R4 seeds and their complete occupied-address postings;
- 195,338 coarse vectors/query (one per occupied address across the three
  seeds);
- K16 exact FP32 refinement for `A` in
  `{128, 256, 512, 1024, 2048, 4096, 8192, 16384}` addresses per seed;
- global unique-document budgets 5k/10k/20k/50k;
- teacher IDs used only for evaluation;
- one Python/NumPy pass per query and grid point, so timings are directional
  logical controls, not native or MDBX latency.

If a selected address prefix exhausts before a requested budget, the receipt
retains the achieved candidate count and marks `budget_exhausted`; it is not
reported as a full-budget point.

## Results

Mean teacher recall (all 152 queries):

| coarse mode | A/seed | @5k | @10k | @20k | @50k |
| --- | ---: | ---: | ---: | ---: | ---: |
| first | 4,096 | .8480 | .8579 | .8711 | .8875 |
| first | 8,192 | .9178 | .9270 | .9336 | .9493 |
| first | 16,384 | .9605 | .9678 | .9691 | .9776 |
| mean | 4,096 | .9737 | .9783 | .9796 | .9849 |
| mean | 8,192 | .9849 | .9888 | .9888 | .9928 |
| mean | 16,384 | .9875 | .9921 | .9921 | .9954 |

At `A=16384`, every requested budget was reached for both modes.  Lower A
values have exhausted rows at higher budgets and are retained only as achieved
prefix results.  The hard-query tail remains visible: for mean/A=16384 the
minimum recall is `.90` at 5k/10k/20k and `.90` at 50k, while the p05 is `.90`
through the first three budgets and `1.00` at 50k.

Mean coarse vectors are substantially better than an arbitrary first
representative.  They cross `.99 @ 50k` at A=8192 and reach `.9954 @ 50k`
at A=16384, but still do not reach the full K16 route's `.9908 @ 5k` frontier.

Logical work and single-pass Python p50 decomposition:

| mode | A/seed | vectors scored/query | coarse+refine+fusion p50 (ms) |
| --- | ---: | ---: | ---: |
| first | 8,192 | 470,435 | 789 |
| first | 16,384 | 742,109 | 1,094 |
| mean | 8,192 | 435,892 | 633 |
| mean | 16,384 | 693,952 | 900 |

The full native K16 control scores about 2.10M representatives/query across
three seeds.  This logical control therefore identifies a plausible lower-work
frontier, but its Python timings must not be compared directly with the native
scalar measurements.

## Interpretation

The coarse/refine idea is viable only with a semantic centroid-like coarse
vector.  A first-representative K1 router is a clear no-go: even 16,384 refined
addresses per seed remain below `.98 @ 50k`.  The mean-of-K16 control is much
stronger and reaches `.9954 @ 50k` with roughly 694k representative vectors
scored/query, about one third of the full K16 logical work.

This is not yet a production result.  The mean vectors are materialized in
Python, the refinement is FP32/NumPy, and there is no native SIMD kernel,
quantized centroid store, page accounting, or THQ/exact cascade in this
experiment.  The result justifies a focused native control for mean coarse
vectors followed by K16 refinement; it does not justify another posting
topology or HNSW deployment.

## Provenance and audit

Raw output and receipt are retained outside Git under
`E:\\_repoz\\agent-memory-workspaces\\r4-k1-coarse-refine-raw`.
The independent audit reports `PASS` for 9,728 quality rows, 2,432 timing
rows, and 80 compact summaries.  The audit recomputes candidate recall from
the raw missed-teacher IDs, verifies exhausted-budget semantics, and
recomputes every receipt aggregate.

| artifact | SHA-256 |
| --- | --- |
| runner | `dd9c5cb361c91e268e3c8b377f8fe58d10ac64be4ae0b66e6f2379ee66b56a31` |
| audit | `dd10498cde529ca73f6f6ffafc2384df6c9bdb4885fe8485d281370a2d3e7eae` |
| raw output | `2ce0084c001f7d33f83b82f9aa663d12d4cf7680333b2b0ddc3be6fa00529153` |
| receipt | `a693235f8c8e2da4f974e951276a5134577efbcd2ac701b286cdb070bfa61e6f` |

## Next check

Materialize the mean coarse vectors as a compact native representation and
measure the same A/budget grid with a native K1 score plus K16 refinement.  If
the native work frontier remains credible, feed its selected postings through
the already validated THQ interval-squared and exact top-256 cascade; only
then measure physical pages/MDBX.
