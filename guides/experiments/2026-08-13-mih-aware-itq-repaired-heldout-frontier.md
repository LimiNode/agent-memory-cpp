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
