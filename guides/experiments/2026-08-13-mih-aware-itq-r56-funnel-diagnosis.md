# Repaired MIH-aware ITQ r56 funnel diagnosis

## 2026-08-13 - pre-execution contract

This diagnostic consumes only the published v2 evidence archive from the
repaired held-out frontier. It compares the fixed `16x16-r56` ITQ and repaired
rows across the five already measured seeds. It cannot select another radius,
encoder treatment, or training setting.

Although its transformation contract was committed before calculating this
report, it is a post-hoc descriptive analysis of the already observed #135
held-out observations, not an independent confirmation experiment.

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
4.14% decrease and 90.26% are unchanged. The pooled Pearson associations are
near zero: threshold delta versus candidate work is `0.0205`, versus raw-union
survival `0.0072`, versus Hamming K1 survival `0.0084`, and versus ADC K2
survival `0.0087`. These pooled linear statistics do not rule out nonlinear or
conditional relationships among the sparse non-zero observations.

The conditional view reaches the same practical conclusion without making that
stronger claim. It reports the means and positive-survival fractions for the
increased, unchanged, and decreased threshold groups separately:

| Threshold group | Δ raw | Δ K1 | Δ ADC | Δ candidates | Δ postings |
| --- | ---: | ---: | ---: | ---: | ---: |
| Increased | +0.004274 | +0.004274 | +0.004274 | +106.83 | +158.20 |
| Unchanged | +0.000708 | +0.000212 | -0.000212 | +93.09 | +128.94 |
| Decreased | +0.001158 | +0.000386 | +0.000386 | +92.05 | +125.28 |

The increased group does carry a positive conditional signal, but it is small,
rare, and accompanied by the largest extra work. This is descriptive evidence,
not a treatment-selection gate.

### Interpretation

The threshold gain is attenuated through all fixed cascade stages. Relative to
the `+0.001885` guaranteed `<=56` mass shift, raw-union survival rises by only
`+0.000927`, K1 by `+0.000447`, and ADC K2 by `+0.000064`: only about 3.4% of
the original threshold gain reaches ADC K2. The difference between threshold
and raw-union deltas (`-0.000958`) is consistent with a changed contribution
from useful above-radius substring collisions; it is not a document-level
attribution because those identities were not retained. Index work rises
broadly. This reinforces the fixed-objective no-go without using these results
to tune a radius.

The next learning treatment should therefore be query-aware and optimise a
retrieval-aligned Hamming target on train query-to-passage pairs. A later
false-positive-mining treatment needs a new evaluator artifact that retains
oracle document identities and stage membership; this diagnostic deliberately
does not infer those unavailable identities.

## Evidence

The replay-validated archive is staged as
`mih-aware-itq-r56-funnel-evidence-v3.zip`. It embeds the exact published
source evidence archive and validates its archive SHA-256
`07a20a79bfaf1120244a8f2d719344fb3b213dcc03b6493113d3f0dd5357f71f`
and bundle root
`22317e8a8b3bf337a0c714e26fbea55e897f049ca003d2761b649ea7e83172fa`
before calculating any diagnostic value. The diagnostic archive SHA-256 is
`befbb3b2096fbfc5743b9d222416d94a77fa8348e5d7af1dbf26ea9c502f9c51`
and its bundle root is
`1503baeaed0cd3496f1554a86127fd5bb1a8e2bfec5bc3298786a0c301f7608d`.
