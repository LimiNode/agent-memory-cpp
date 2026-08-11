# Static weighted-Hamming MIH approximation

Date: 2026-08-12. Context: PR #123, calibrated-weighted-Hamming matrix.

## Question

Can a calibration-only, static per-bit Hamming weighting improve the existing
256-bit MIH cascade without changing its band probing policy?

## Frozen setup

The matrix fixes ITQ seeds 42--46, 32 equal 8-bit bands, radius-one
budgeted-confidence probing, a 12,288 soft-candidate target, Hamming-768,
and binary-ADC-256. It compares uniform Hamming with
`calibrated-centroid-separation`. The primary result is E5-oracle survival
through the binary-ADC stage; weighted `hamming_top_k_recall` is scorer-self
recall and is not used as a cross-policy quality comparison.

The final archive contains all 10 reports, per-query contributions, five
paired 10,000-replicate bootstraps, the frozen matrix, and exact source
snapshots. It is retained as a GitHub evidence release rather than in Git.

```text
archive SHA-256: ea741ed9d55025eee19c46bda45eac3c1bfaa48bcd3b08885d5d1573b28c26ce
bundle root:     c91de42485ec1f2265bb66c3abcf9a94df2d4748b7f9b783e36a95ee64da180f
```

## Result

Across the five seeds, uniform Hamming had mean ADC survival `0.987252`, while
the static calibrated weighting had `0.987157` (delta `-0.000096`). Raw-union
survival, candidate count, and posting visits were effectively unchanged.

## Interpretation and limitation

This is a no-go for this one cheap, static weighting approximation. It does
not reject weighted Hamming generally: it does not test query-adaptive weights,
best-first multiprobe ordering, multi-bit flips, candidate/posting budgets, or
MIH-aware representation learning. Those are separate experiments and must
not inherit this conclusion.
