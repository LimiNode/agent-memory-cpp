# Repaired MIH-aware ITQ r56 funnel diagnosis

## 2026-08-13 - pre-execution contract

This diagnostic consumes only the published v2 evidence archive from the
repaired held-out frontier. It compares the fixed `16x16-r56` ITQ and repaired
rows across the five already measured seeds. It cannot select another radius,
encoder treatment, or training setting.

The retained source artifact records per-query oracle fractions, candidate
work, and funnel survival. It does not retain the document identities of the
ten oracle neighbours. Consequently this is an aggregate per-query funnel
diagnosis, not a document-level trace of individual threshold crossers.

## Result

Across the 6,260 paired seed-query observations, the repaired code raises the
mean fraction of oracle neighbours within Hamming 56 by `+0.001885`, but the
observed gain attenuates through the fixed cascade:

| Measure | Repaired - ITQ |
| --- | ---: |
| Oracle fraction Hamming <= 56 | +0.001885 |
| Raw-union survival | +0.000927 |
| Hamming K1=768 survival | +0.000447 |
| ADC K2=256 survival | +0.000064 |
| Candidates/query | +93.81 |
| Posting visits/query | +130.43 |

Only 5.61% of paired query observations increase their `<=56` oracle fraction;
4.14% decrease and 90.26% are unchanged. More importantly, the corresponding
per-query correlations are effectively zero: threshold delta versus candidate
work is `0.0205`, versus raw-union survival `0.0072`, versus Hamming K1
survival `0.0084`, and versus ADC K2 survival `0.0087`.

### Interpretation

The small full-Hamming threshold shift is not aligned with useful per-query
frontier movement. It occurs for a small subset of queries, while its signal
does not predict survival through raw union, K1, or ADC K2; meanwhile index
work rises broadly. This reinforces the fixed-objective no-go without using
these results to tune a radius.

The next learning treatment should therefore be query-aware and optimise a
retrieval-aligned Hamming target on train query-to-passage pairs. A later
false-positive-mining treatment needs a new evaluator artifact that retains
oracle document identities and stage membership; this diagnostic deliberately
does not infer those unavailable identities.

## Evidence

The replay-validated archive is staged as
`mih-aware-itq-r56-funnel-evidence-v1.zip`. It embeds the exact published
source evidence archive and validates its archive SHA-256
`07a20a79bfaf1120244a8f2d719344fb3b213dcc03b6493113d3f0dd5357f71f`
and bundle root
`22317e8a8b3bf337a0c714e26fbea55e897f049ca003d2761b649ea7e83172fa`
before calculating any diagnostic value. The diagnostic archive SHA-256 is
`d8d8492d1335ecfabacbf6faab73c13b056e6b76d31e0a052d7e8027161ae0fb`
and its bundle root is
`243f61198c548ecc1c559b8460705455a62403b8b1b7aab84d57df9abdf1ec29`.
