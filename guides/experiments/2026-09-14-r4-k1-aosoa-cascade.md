# R4 K1 AoSoA full cascade (2026-09-14)

## Question

Does a compact INT8 K1 sidecar preserve the strong K16 representative route
when the complete cascade is executed, and does AoSoA storage improve the
coarse arithmetic without changing the quality frontier?

## Protocol

The replay uses the frozen THQ fixture, three frozen R4 seeds
(`2026082701`, `2026082702`, `2026082703`), all 152 queries, and the existing
K16 representative codec.  The K1 sidecar is scored natively as
per-dimension INT8 with either row-major scalar storage or AVX2 AoSoA tiles of
16 or 32 lanes.  For each layout, `A=8192` and `A=16384` addresses are refined
per seed, the three streams are fused by global score order, and the resulting
unique candidates are reranked with THQ4 interval-squared ADC to top-256 and
exact FP32 E5 to top-10.

This is a quality and arithmetic/layout experiment.  It does not claim MDBX
page behavior, OS cache behavior, physical media reads, or end-to-end service
latency.  The page/read control is reported separately in
`2026-09-14-r4-k1-page-read.md`.

## Evidence

The executed AoSoA receipts contain 1,216 rows per layout (152 queries × two
address budgets × four candidate budgets) and are independently checked by
`audit-r4-k1-aosoa-cascade.py`.  The audit binds the frozen manifests, native
executable, runner, raw output, every native order/result, and every K1 data
and scale sidecar.  It recomputes candidate and reranker teacher recall from
the row-level IDs and requires candidate, THQ-top-256, and exact-top-256 recall
to agree row by row.

The authoritative raw payloads are retained outside the Git checkout:

* AoSoA-16 raw SHA-256:
  `8568f9889a59eefb6b01e93efb9a0555fc48d4f4420c4e6fe1d5485cef5f83e0`
* AoSoA-32 raw SHA-256:
  `b342ef6eab1b4298efd1c713f89198853008240616c8493d49d15d365446ddea`

Both layouts produce the same quality frontier:

| Refined addresses/seed | 5k | 10k | 20k | 50k |
| ---: | ---: | ---: | ---: | ---: |
| 8,192 | .992763 | .993421 | .993421 | .994079 |
| 16,384 | .997368 | .997368 | .997368 | .997368 |

The FP32 candidate-recall delta is `-0.001316` for `A=8192` at 5k and zero
for `A=16384` in this replay.  AoSoA-16 and AoSoA-32 therefore preserve the
route quality of the FP32 comparison within the tested grid.

The corrective AoSoA-32 replay also binds layout identity as `(mode, lanes)`
and has a row identity `(mode, lanes, query, A, budget)`, so `--layout-filter`
cannot silently collide the two AoSoA widths. Its fail-closed audit requires
candidate recall equality with THQ top-256, exact top-256, and exact top-10;
the refreshed AoSoA-32 raw payload SHA is
`847ec704a826934adbc2f442edfce2ffce8c1ff547264344cd782bac4fc75077`.

Native K1 arithmetic is about 8.10/9.57 ms p50/p95 for AoSoA-16 and
7.88/8.38 ms for AoSoA-32 in the component benchmark, versus 28.73/32.77 ms
for row-major scalar.  The full cascade remains dominated by K16 refinement
(roughly 46 ms for `A=8192` and 96 ms for `A=16384` per seed in the captured
native samples), before fusion and storage work.

## Interpretation and next gate

AoSoA is a sound immutable K1 coarse-scan representation and does not alter
the quality frontier.  It is not a good sparse posting/document gather layout:
the page/read control shows that complete AoSoA tiles are fetched for sparse
address gathers.  K1 coarse data and R4 posting/document data should therefore
remain separate physical representations.

The next experiment is the full occupied-address native K8/K16/K32 bake-off,
with FP32-versus-INT8 quality parity, per-seed work, global unique-candidate
accounting, THQ top-256, exact top-10, and representative-store bytes.  Only
after that gate should a representative-layer top-R oracle or physical MDBX
materialization be selected.

## Limitations

The result is one frozen fixture and one native host configuration.  It is not
a multi-host performance claim, and it does not establish production
activation (`production_activation=false`).
