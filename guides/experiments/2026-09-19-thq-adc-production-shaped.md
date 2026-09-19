# THQ4 production-shaped ADC48 comparison

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Protocol

The earlier cross-fit table compared ADC against an INT8 arm that could rank
the full R4 shell. This control makes the stages identical:

`R4 ~5k → THQ4 interval² top128 → scorer → top10`.

It uses a seeded shuffled four-fold split (38 queries per fold), fitting the
ADC48/3-bit Mahalanobis codebook on the other 114 queries. Controls are
candidate FP32, direct INT8, and RSLM4-like rotated residual reconstruction,
all restricted to the same THQ4 top128. The RSLM4 codebook is query
independent and is fitted once on the detached document-training sample.

## OOF result

| arm | payload | nDCG@10 | teacher overlap |
| --- | ---: | ---: | ---: |
| THQ4 → FP32 | 1536 B | .654201 | .992764 |
| THQ4 → INT8 | 388 B | .656991 | .989474 |
| THQ4 → RSLM4 | 288 B | .659320 | .969737 |
| THQ4 → ADC48/3bit | 144 B | .649128 | .871711 |

Paired ADC48 deltas across all 152 OOF queries:

| baseline | mean | median | p05 | min | CI95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| THQ4 → FP32 | -.005072 | .000000 | -.122352 | -.369070 | [-.019093, +.009095] |
| THQ4 → INT8 | -.007863 | .000000 | -.122950 | -.369070 | [-.021383, +.005518] |
| THQ4 → RSLM4 | -.010191 | .000000 | -.144315 | -.369070 | [-.023868, +.003263] |

The seeded contiguous-fold replay had reported `.656748` for ADC48/3bit;
the shuffled-fold result is `.649128`. This fold-assignment sensitivity is
material evidence against treating the earlier `.656748` as a codec win.

## Interpretation

The production-shaped control removes the apples-to-oranges INT8 comparison.
On this replay ADC48/3bit is below all three controls, although the paired
bootstrap intervals include zero. The 144-byte arm therefore remains a
bounded exploratory candidate, not a production selection. The disagreement
between contiguous and shuffled folds also motivates repeated shuffled folds
before any architecture decision.

This experiment does not test a true pairwise/listwise loss. It only tests the
current Mahalanobis ADC parameterization under a fairer stage boundary. Native
latency, persistent storage, and held-out-domain evidence remain open.

Raw output (kept locally): `tmp/thq-adc-production-shaped.json`, SHA-256
`abda5bdc604cda8e21485f43542ce274f2adc7d69d3f4d7dc69de36962106ee2`.
Runner SHA-256:
`ec3650b15c607c5a57f91e25e82a16314ca3172ab2cb91597b25570696123000`.
