# Fixed-r56 MIH spillover frontier through m24

Date: 2026-08-21.  This record continues the Spanish-only scale-aware native
MIH study.  It is exploratory and does not select a configuration or confirm
one on French data.

## Question and protocol

Could extending the fixed-`r56` heuristic to larger `m` produce a useful
native frontier, and what fraction of its apparent Hamming shortlist quality
is guaranteed rather than candidate-union spillover?

The predeclared grid is
`tools/agent-memory-bench/scale-aware-fixed-r56-m24-grid.example.json`:

| Scale | New `m` values |
| --- | --- |
| 25k | 22, 23, 24 |
| 100k | 20, 21, 22, 23, 24 |
| 1M | 17, 18, 19, 20, 21, 22, 23, 24 |

Every row reuses the frozen Spanish input/E5 roots and the canonical
flat-open-address/two-pass-generation-array implementation.  The runner
fail-closes on the actual ITQ artifact bytes, each input manifest, each E5
manifest, and every E5 payload declared in that manifest.  It does not read
French data.

The companion diagnostic
`tools/agent-memory-bench/diagnose-scale-aware-fixed-r56-m24-spillover.py`
reruns only deterministic candidate generation and a Flat Hamming reference.
It verifies the original config/report binding and the historical sum-based
checksums as regression guards, then records new SHA-256 query-bounded raw
candidate sequence, raw candidate-set, and Hamming-shortlist sequence
digests.  The historical checksums are not cryptographic proof of candidate
set identity.

## Results

The distance spectrum is independent of `m` for each frozen scale: Flat
`d768` p50 is 109 at 25k, 104 at 100k, and 96 at 1M.  Thus no fixed-`r56`
row has a true global exact Hamming top-768 guarantee.  The table gives the
mean raw-union size, raw-union recall against deterministic Flat top-768, and
raw-union spillover above 56.

| Scale | `m` | Candidates/query | Union recall@768 | Spillover `>56` |
| --- | ---: | ---: | ---: | ---: |
| 25k | 22 | 8,437 | 0.7734 | 0.9999835 |
| 25k | 23 | 9,135 | 0.8000 | 0.9999848 |
| 25k | 24 | 11,156 | 0.8541 | 0.9999876 |
| 100k | 20 | 21,398 | 0.7475 | 0.9999934 |
| 100k | 21 | 27,219 | 0.8139 | 0.9999948 |
| 100k | 22 | 33,151 | 0.8557 | 0.9999958 |
| 100k | 23 | 35,712 | 0.8752 | 0.9999961 |
| 100k | 24 | 43,977 | 0.9136 | 0.9999968 |
| 1M | 17 | 142,481 | 0.8056 | 0.9999990 |
| 1M | 18 | 166,943 | 0.8347 | 0.9999991 |
| 1M | 19 | 165,097 | 0.8391 | 0.9999991 |
| 1M | 20 | 212,443 | 0.8834 | 0.9999993 |
| 1M | 21 | 270,331 | 0.9235 | 0.9999995 |
| 1M | 22 | 329,865 | 0.9448 | 0.9999996 |
| 1M | 23 | 354,913 | 0.9543 | 0.9999996 |
| 1M | 24 | 437,990 | 0.9710 | 0.9999997 |

At 1M, `m24` buys the largest observed Flat-top-768 overlap, but it scans
about 43.8% of the corpus and retains only an approximate heuristic claim.
This is evidence against extending this fixed-radius frontier further, not a
selection of `m24`.

## Interpretation and next work

The fixed-`r56` branch is now closed for the current candidate-generator
architecture.  It has documented a budget/quality frontier, not an exact
Hamming-top-K algorithm.  Future work is intentionally split:

1. **Budgeted approximate MIH.** Calibration may choose `m`, schedules, and
   explicit probe/candidate budgets against native latency and quality.  It
   must make no exactness claim and must use a fresh untouched confirmation
   split after selection.
2. **True exact Hamming top-K MIH.** A separate implementation must return
   the identical ordered `(distance, document_position)` prefix as Flat for
   every predeclared K.  Its protocol is recorded in
   `2026-08-21-true-exact-mih-top-k-protocol.md`.

