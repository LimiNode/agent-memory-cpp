# Repaired MIH-aware ITQ held-out frontier

## 2026-08-13 — pre-execution contract

### Question

Does the calibration-only repaired encoder move enough E5-oracle neighbours
across useful Hamming thresholds to improve the held-out MIH retrieval/work
frontier? This is the first evaluation of the repaired encoder against the
held-out documents, queries, and qrels. No result may select or retune the
encoder, training hyperparameters, radius, or partition.

### Frozen matrix

For each of five fixed ITQ seeds, the matrix compares ordinary full-25k ITQ
with the fixed repaired-control trainer from #134. The repaired artifact uses
the same full-25k initialization, eight final epochs, bipolar target, anchor
weight 50, and full-calibration threshold recalibration. Training remains
document-only and does not read the evaluation root.

Each encoder is evaluated in all four fixed index regimes:

| Regime | Partition | Search contract |
| --- | --- | --- |
| `32x8-r1` | 32 bands × 8 bits | local radius one |
| `16x16-r48` | 16 bands × 16 bits | exact global Hamming radius 48 |
| `16x16-r56` | 16 bands × 16 bits | exact global Hamming radius 56 |
| `16x16-r64` | 16 bands × 16 bits | exact global Hamming radius 64 |

Every row fixes Hamming K1 to 768 candidates, binary-ADC K2 to 256, and the
E5 oracle to top 10. It records raw candidate union, posting visits, raw-union
survival, Hamming-K1 survival, ADC-K2 survival, final reranked nDCG@10, and
mean oracle Hamming. It additionally records the fraction of oracle neighbours
at Hamming distance at most 48, 56, and 64.

The report is descriptive: paired bootstraps compare repaired vs ordinary ITQ
within the same seed and regime, but they do not choose a winner. A weak or
negative held-out result closes this repaired objective without post-hoc changes
to its anchor, epoch count, or radius grid.

## 2026-08-13 — five-seed held-out result

The frozen 40-row matrix completed. The repaired encoder moved a small but
measurable fraction of E5 oracle neighbours through the useful thresholds:
the five-seed mean fractions at Hamming `<=48`, `<=56`, and `<=64` changed by
`+0.000431`, `+0.001885`, and `+0.005623`, respectively. The mean oracle
Hamming distance fell by `0.0387` bits. This is far smaller on held-out data
than the calibration-only change, but it has the expected direction.

| Regime | ITQ candidates | Repaired candidates | Raw-union delta | ADC-K2 delta | nDCG@10 delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| `32x8-r1` | 16,138.81 | 16,132.45 | -0.000288 | -0.004585 | -0.000381 |
| `16x16-r48` | 1,277.24 | 1,331.58 | -0.000927 | -0.001038 | -0.000422 |
| `16x16-r56` | 3,045.48 | 3,139.30 | +0.000927 | +0.000064 | -0.000350 |
| `16x16-r64` | 5,021.06 | 5,132.00 | -0.000974 | -0.002700 | -0.000486 |

Posting work follows the same unfavorable direction for the exact regimes:
the repaired representation adds 62.66, 130.43, and 197.48 mean visits at
`r48`, `r56`, and `r64`. The per-seed paired bootstrap confirms that the
threshold-mass shift can be real—for example, seed 52 at `r56` has a 95%
paired interval of `[+0.000080, +0.003914]` for the `<=56` fraction—but the
same seed's raw-union, ADC-K2, and nDCG intervals cross zero.

### Interpretation

The repaired objective solved the geometry-preservation problem but did not
shift enough probability mass across the held-out MIH frontier to compensate
for its additional exact-regime candidate and posting work. It is therefore a
useful no-go for this cosine/bipolar refinement at the fixed anchor and epoch
schedule, not evidence for retuning those values after observing the frontier.

The next calibration-only algorithm should optimize a threshold objective such
as `P(Hamming <= R)` for fixed oracle-neighbour targets and hard negatives,
then separately consider a MISH-style term that penalizes MIH substring false
positives. It should begin with a new pre-execution contract and not reuse this
held-out matrix for selection.
