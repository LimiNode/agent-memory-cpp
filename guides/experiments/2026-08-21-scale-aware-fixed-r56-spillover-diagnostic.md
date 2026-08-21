# Scale-aware fixed-r56 MIH spillover diagnostic

Date: 2026-08-21.  Follow-up to the Spanish calibration matrix recorded in
`2026-08-16-scale-aware-native-mih-protocol.md`; it does not alter that matrix.

## Question

What does the fixed-`r56` native MIH candidate generator actually measure at
the `hamming_limit=768` downstream boundary?  In particular, is the final
Hamming shortlist an exact global Hamming top-768 result, or is its quality
substantially determined by candidates beyond the fixed-radius guarantee?

## Protocol

The diagnostic plan is
`tools/agent-memory-bench/mih-fixed-r56-spillover-diagnostic.example.json`.
It reuses only the existing Spanish calibration input artifacts and the same
selected query positions for three explicitly named representatives:

| Scale | Representative | Status in the original matrix |
| --- | --- | --- |
| 25k | `m21`, flat directory, two-pass generation dedup | selected MIH |
| 100k | `m19`, flat directory, two-pass generation dedup | fastest examined fixed-r56 MIH, not admissible |
| 1M | `m16`, flat directory, two-pass generation dedup | largest examined fixed-r56 MIH, not admissible |

The optional native diagnostic pass reconstructs the raw MIH candidate union,
then independently forms a deterministic full-corpus Flat Hamming top-768
(distance followed by document position for ties).  It records compact
per-query counts and overlaps, rather than exporting candidate lists.  It
asserts that every full-corpus match at Hamming distance `<=56` is present in
the raw union.

This is a mechanism diagnostic only: no E5 gates or bootstrap are replayed,
no backend is selected, the original matrix is not rerun, and French
confirmation data is not read.

## Results

All 648 existing Spanish query positions per scale were checked.  `d768` is
the largest distance in the deterministic Flat Hamming top-768.  “Union
recall” is raw MIH candidate-union overlap with that exact Flat top-768;
“shortlist recall” is the same overlap after MIH's own Hamming top-768.

| Scale | Union candidates, mean / p50 / p95 | `d768`, p50 / p95 | Mean Flat top-768 items `<=56` | P(`d768<=56`) | Union recall | Shortlist recall | Shortlist spillover `>56` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25k | 6,988 / 6,980 / 7,442 | 109 / 111 | 0.139 | 0.000 | 0.7196 | 0.7196 | 0.999819 |
| 100k | 16,649 / 16,644 / 17,962 | 104 / 106 | 0.140 | 0.000 | 0.6785 | 0.6785 | 0.999817 |
| 1M | 112,911 / 112,853 / 123,269 | 96 / 99 | 0.142 | 0.000 | 0.7629 | 0.7629 | 0.999815 |

The exact inclusion assertion passed for every query.  Yet the raw unions are
also almost entirely spillover: their fractions of candidates above radius 56
are respectively `0.999980`, `0.999992`, and `0.999999`.

## Interpretation

The fixed-`r56` schedule correctly guarantees inclusion of the very small
set of global Hamming matches at distance `<=56`.  It does **not** guarantee
the global Hamming top-768: for these inputs, the 768th exact neighbor is
always far beyond 56, and the candidate union retrieves only about 68–76% of
the deterministic Flat top-768.  Consequently, the downstream Hamming/ADC
quality in the fixed-`r56` experiment is materially m-dependent spillover
evidence, not a canonical exact-Hamming-top-K MIH result.

The equal union and final-shortlist recalls in these three diagnostic runs are
an observed property of these representatives, not an identity promised by
the algorithm.  A raw-union measurement was necessary to establish it; it
could not be inferred from the old final shortlist exports alone.

## Limitations and next check

The diagnostic does not rank alternative `m` values and does not prove that
any fixed-r56 configuration is production-competitive.  Its native pass uses
one warm timing repetition solely to regenerate deterministic candidates; its
timings are intentionally not benchmark results.

The next experiment may therefore extend the **fixed-r56 heuristic** grid to
larger `m` values, explicitly as exploratory spillover-frontier evidence.  It
must keep the radius, Hamming/ADC limits, dataset, and quality gates fixed;
must not use French; and must remain separate from a later true exact Hamming
top-K experiment, which would expand until equality with Flat at every chosen
K is proved.
