# THQ4 ADC explicit pairwise teacher-loss control

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Protocol

This gate forms THQ4 top128 for each fitting query, ranks those documents by
exact FP32 score, and trains 128×3D×3-bit codebooks on ranks 0–31. It is an
ADC48 side code (48 B) plus the 96 B THQ4 code, or 144 B total. The objective is

`softplus(-(score(rank 0..9) - score(rank 10..31))/0.02)`

plus a reconstruction regularizer calibrated to 10% of the initial pairwise
loss. Hard assignments are refreshed every step, groups are shuffled per epoch,
and evaluation is four-fold seeded-shuffled OOF on all 152 queries with three
training seeds. The runner records pairwise/reconstruction losses, weighted
regularizer contribution, assignment occupancy, center diagnostics, and a
pre-training parity check against the direct ADC scorer.

## Result

The corrected multi-seed replay uses 10% reconstruction contribution and
completes with pre-training direct-ADC parity below `7.2e-7` in every fold.
The prior receipt (`e5ca7566…`) was mislabeled as 32B/2bit and used an
effectively disabled `1e-4` reconstruction term. Its `.560589` result is
retained only as historical evidence and is not pooled with this replay.

| arm | OOF nDCG@10 | candidate-FP32 overlap | teacher overlap |
| --- | ---: | ---: | ---: |
| ordinary ADC48/3bit shuffled control | .649128 | .875658 | .871711 |
| corrected pairwise ADC48/3bit (three seeds pooled) | .639432 | .818860 | .816667 |

Per-seed nDCG@10 was `.640683`, `.642764` and `.634850` (seeds 11, 22 and
33). The corrected pairwise objective is below the ordinary ADC48/3bit OOF
control (`.649128`). After correcting block-local symbol accounting, the
representative diagnostic leaves 429 of 1024 global centers unused (the
per-block mean is 3.35 unused centers out of 8). This is non-uniform
occupancy, but not evidence of total codebook collapse by itself.

All four folds reproduced the direct scorer's top-10 on the four parity groups
checked in each fold; maximum absolute score error was below `7.2e-7`.

## Interpretation

This is a bounded negative for this stabilized hard-assignment pairwise
implementation: shuffling, three seeds and a reconstruction term calibrated to
10% of the initial pairwise loss did not recover the ordinary ADC quality. It
does not disprove AVQ, Distill-VQ, QINCo, or a
carefully regularized listwise method, but it removes this naive objective as a
credible next production candidate.

The independent audit recomputes qrels nDCG, teacher overlap,
candidate-FP32 overlap, fold coverage, top128 containment, and runner/result
hash bindings. Native latency, persistent storage, and held-out-domain evidence
remain outside this gate.

Raw output (corrected replay): `tmp/thq-adc-pairwise-corrected.json`, SHA-256
`6a9b41b3c9a9ebcb50d3ca458652e2b016f0ab8daad60fa4bad3b09006812946`.
Runner SHA-256: `7d92b4394a3bafcc2fc6469303182f4bcba2fa441c39d5a93914a20287bfe05c`.
