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

The same frozen measurements also give the operational context below.  The
first three rows at each scale are historical #155 controls, retained only as
context; the remaining sixteen are this predeclared #157 grid.  `ADC LB95`
and `nDCG LB95` are the fixed exploratory reporting quantities, not a new
selection rule.

| Scale | `m` | Candidates/query | Generator p50 ms | Cascade p50 ms | ADC LB95 | nDCG LB95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 25k | 21 (control) | 6,988 | 0.3105 | 0.4952 | 0.955093 | 0.983327 |
| 25k | 22 | 8,437 | 0.3823 | 0.5907 | 0.966049 | 0.986094 |
| 25k | 23 | 9,135 | 0.3895 | 0.5940 | 0.965278 | 0.985870 |
| 25k | 24 | 11,156 | 0.4412 | 0.6457 | 0.977623 | 0.989714 |
| 100k | 19 (control) | 16,649 | 0.6961 | 0.9107 | 0.906790 | 0.971010 |
| 100k | 20 | 21,398 | 0.8628 | 1.0940 | 0.931944 | 0.973471 |
| 100k | 21 | 27,219 | 1.0266 | 1.2740 | 0.952469 | 0.981912 |
| 100k | 22 | 33,151 | 1.2184 | 1.4608 | 0.959414 | 0.984849 |
| 100k | 23 | 35,712 | 1.2882 | 1.5236 | 0.959105 | 0.984823 |
| 100k | 24 | 43,977 | 1.6079 | 1.8517 | 0.968673 | 0.987996 |
| 1M | 16 (control) | 112,911 | 6.4279 | 6.6748 | 0.881790 | 0.945121 |
| 1M | 17 | 142,481 | 8.5367 | 8.8298 | 0.896296 | 0.956149 |
| 1M | 18 | 166,943 | 9.5661 | 9.8611 | 0.900309 | 0.962742 |
| 1M | 19 | 165,097 | 9.2185 | 9.5121 | 0.900617 | 0.968119 |
| 1M | 20 | 212,443 | 11.8151 | 12.1149 | 0.917130 | 0.967681 |
| 1M | 21 | 270,331 | 14.9148 | 15.2130 | 0.925926 | 0.977623 |
| 1M | 22 | 329,865 | 20.1927 | 20.5179 | 0.933179 | 0.979054 |
| 1M | 23 | 354,913 | 19.7531 | 20.0741 | 0.931173 | 0.978648 |
| 1M | 24 | 437,990 | 23.7744 | 24.0853 | 0.937191 | 0.979086 |

## Interpretation and next work

We stop extending the fixed-`r56` full-code frontier beyond `m24` for the
current architecture.  This is a practical stop decision, not a proof that
every `m>24` is dominated.  The branch has documented a budget/quality
frontier, not an exact Hamming-top-K algorithm.  Future work is intentionally
split:

1. **Budgeted approximate MIH.** Calibration may choose `m`, schedules, and
   explicit probe/candidate budgets against native latency and quality.  It
   must make no exactness claim and must use a fresh untouched confirmation
   split after selection.
2. **True exact Hamming top-K MIH.** A separate implementation must return
   the identical ordered `(distance, document_position)` prefix as Flat for
   every predeclared K.  Its protocol is recorded in
   `2026-08-21-true-exact-mih-top-k-protocol.md`.
