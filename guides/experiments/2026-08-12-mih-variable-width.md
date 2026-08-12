# True variable-width MIH under matched local probing

## 2026-08-12 — fixed 32-band comparison

### Question

Can true variable-width MIH reduce the candidate union of the current ITQ-256
32x8, local-radius-1 cascade without sacrificing the E5-oracle funnel?

### Contract

The study uses the frozen 25,000-vector MIRACL Russian E5 materialization,
1,252 evaluation queries, ITQ seeds 52--56, 32 bands, local radius one,
Hamming K1=768, binary ADC K2=256, and E5 oracle K=10.  The fixed 15-row
matrix is three layouts by five ITQ seeds:

| Layout | Band widths | Assignment rule |
| --- | --- | --- |
| `contiguous` | 32 x 8 | Existing equal-width control. |
| `fixed-random` | 8 x 6, 8 x 7, 8 x 9, 8 x 10 | Fixed full-bit permutation, seed `20260812`. |
| `calibration-collision-balanced-variable` | 8 x 6, 8 x 7, 8 x 9, 8 x 10 | Calibration-only collision-information balancing into the fixed variable-width keys. |

All forms make exactly `32 + 32 * 8 = 288` bucket probes per query.  Thus
the intervention changes key widths and assignment, rather than granting the
variable layouts additional probes.  The calibration-only procedure does not
read held-out query relevance, E5-oracle outcomes, candidate counts, or
retrieval metrics while creating the layout.

Paired-query bootstrap uses 10,000 replicates for both comparisons per seed:
equal-width control versus calibrated variable, and variable-width fixed-random
control versus calibrated variable. It preserves raw-union,
Hamming K1, ADC K2, final coverage/NDCG, candidate count, posting visits, and
bucket-probe work arrays.

### Result

Five-seed means were:

| Layout | Candidates/query | Posting visits/query | Oracle raw union | Oracle Hamming K1 | Oracle ADC K2 | Final NDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Equal 32x8 contiguous | 16,124.23 | 29,133.11 | 0.998466 | 0.992716 | 0.991917 | 0.801148 |
| Variable fixed-random | 18,826.53 | 40,528.25 | 0.999521 | 0.993403 | 0.992604 | 0.801246 |
| Variable calibrated | 18,765.08 | 40,058.35 | 0.999361 | 0.993339 | 0.992540 | 0.801293 |

Relative to 32x8, the calibrated true-variable form increases the candidate
union by 16.38% and posting visits by 37.50%.  It raises raw-union oracle
survival by 0.000895 and ADC survival by 0.000623, but those gains come with
substantially more work.  Against the width-matched fixed-random baseline,
calibration balancing reduces candidates only by 61.45/query and posting visits
by 469.90/query, while its mean raw-union and ADC survival are slightly lower.

### Interpretation

This particular true-variable-width allocation is **not** a candidate-reduction
improvement for the current radius-one cascade.  The quality movement is small
and is explained by a larger union, not a better selectivity/work frontier.
The calibration collision-information approximation also does not beat the
fixed-random variable-width control at the same widths. This is not merely an
unsuccessful greedy assignment: the candidate expansion is closely predicted
by the radius-one bucket geometry itself.

For independent balanced bits, a width-`w` band hits a random document with
probability `(w + 1) / 2^w` under exact plus one-bit probing. The corresponding
union prediction and observed mean union fraction for the 22,607-document
corpus are:

| Form | Independent balanced-bit prediction | Observed candidate fraction |
| --- | ---: | ---: |
| 32 x 8 | 0.6819 | 0.7132 |
| 8 x 6, 8 x 7, 8 x 9, 8 x 10 | 0.8150 | 0.8301 |

The eight 6-bit bands alone have an independent-bit union prediction of
0.6041. Because MIH forms a union, later 9/10-bit bands cannot remove the
false-positive candidates that shorter bands already admitted. A 6-bit key also
cannot contain more than about six bits of collision information when the ITQ
bits are already near balanced, so calibration assignment cannot make it as
selective as a good 8-bit key.

This does not say that every variable-width MIH algorithm is ineffective.  It
rejects this fixed static width shape and greedy calibration-only assignment
under equal probe count. It does not reject every variable-width MIH method.
A future learned layout would need a separate objective and must show an
improved quality-versus-union frontier, not merely higher recall after admitting
more candidates.

### Scope and limitations

The evaluator is a deterministic Python quality/reference harness; these rows
are not native latency claims.  The archive records the 15 reports, 15
per-query contribution NPZ files, ten paired bootstrap reports, matrix,
runtime/source provenance, and source snapshots.  The release is draft staging
until its validator and PR checks are green; its target commit and digests are
recorded in the PR rather than treated as a public release here.

The measured static variable widths are only one shape: eight bands each of 6,
7, 9, and 10 bits. This is a fixed-matrix study; its repository chronology does
not claim an independently auditable predeclaration before execution. It does
not test query-adaptive radii, weighted Hamming, learned code training, or
Hamming-weight-tree alternatives.

### Follow-up

The immediate implementation priority remains the native hot path and reducing
the union before Hamming/top-K. If static variable width is revisited, it should
directly optimize calibration-estimated unique union, posting visits, and a
tail-work statistic while choosing both widths and bit assignment. It should
compare the returned design with equal-width and width-matched random controls
at matching probe budgets. Returning 32 x 8 is an informative outcome: it
would support closing static radius-one variable-width MIH for this ITQ-256
distribution rather than hand-enumerating more width shapes.
