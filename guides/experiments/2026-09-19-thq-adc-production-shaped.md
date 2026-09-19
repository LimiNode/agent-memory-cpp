# THQ4 production-shaped ADC48 comparison

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Protocol

All arms use the same stage boundary:

`R4 ~5k → THQ4 interval² top128 → scorer → top10`.

The seeded shuffled four-fold split has 38 queries per fold. ADC48/3-bit
Mahalanobis codebooks are fit on the other 114 queries using a 25,000-vector
sample, 20 Lloyd iterations and four restarts. Controls are candidate FP32,
direct INT8 and RSLM4-like rotated residual reconstruction, all restricted to
the same THQ4 top128.

## Payload accounting

The table separates scorer-side bytes from the complete cascade:

| arm | scorer/side bytes | cascade total | nDCG@10 | teacher overlap |
| --- | ---: | ---: | ---: | ---: |
| THQ4 → FP32 | 1536 | 1632 | .654201 | .992763 |
| THQ4 → INT8 | 388 | 484 | .656991 | .989474 |
| THQ4 → RSLM4 | 192 | 288 | .659320 | .969737 |
| THQ4 → ADC48/3bit | 48 | 144 | .647401 | .878947 |

The earlier single-init replay (`abda5bdc…`) remains a bounded historical
control. It must not be presented as stable best-fit evidence because it used
8192 samples, five iterations and one restart.

## Interpretation

The corrected replay does not improve ADC48: its nDCG is below all three
controls. Paired deltas versus FP32, INT8 and RSLM4 are respectively
`-.006800`, `-.009590` and `-.011919`; all bootstrap intervals include zero,
so this is a bounded negative rather than a statistically decisive separation.
The stronger fit therefore does not license production selection. The replay
does not provide native latency, persistent-storage or held-out-domain evidence.
An independent audit recomputes the per-row qrels metrics, teacher/candidate
overlaps, fold coverage, and top128 containment before the result is cited.

Raw output (corrected replay): `tmp/thq-adc-production-shaped-stable.json`,
SHA-256 `68729d931c8edb5ec0f24d5700203d9eae4b15281ee2223d9b56399a7b8df671`.
Runner SHA-256: `1259e14bf0107ae510666330b5cf6ec4f55cf3418270994edea68c2cf5a3a516`.
