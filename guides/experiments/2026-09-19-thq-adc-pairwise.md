# THQ4 ADC explicit pairwise teacher-loss control

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Protocol

The preceding cutoff-aware replay only changed the k-means sample. This gate
implements an actual differentiable pairwise objective over the production
boundary: for each fitting query, form THQ4 top128, rank those documents by
exact FP32 score, and train 128×3D×2-bit codebooks on ranks 0–31. The loss is

`softplus(-(score(rank 0..9) - score(rank 10..31))/0.02)`

plus a small reconstruction regularizer. Hard assignments are recomputed each
step; evaluation is four-fold seeded-shuffled OOF on all 152 queries. The
reported run uses one Adam run per fold in the conservative control (learning
rate `.003`, four epochs). An earlier `.03`/eight-epoch run was
also tried and was similarly poor; it is not pooled into the final receipt.

## Result

| arm | OOF nDCG@10 | candidate-FP32 overlap | teacher overlap |
| --- | ---: | ---: | ---: |
| ordinary ADC48/3bit shuffled control | .649128 | .875658 | .871711 |
| explicit pairwise 32B/2bit | .560589 | .650000 | .650000 |

The paired delta to the independent candidate-FP32 reference is `-.093612`,
with p05 `-.384558`, worst-query loss `-.684535`, and bootstrap CI95
`[-.122109,-.065761]`. The loss decreased during each fold, but held-out
retrieval collapsed. This is a genuine objective/optimization failure, not a
decoder parity issue: the scorer was first checked against the existing ADC
direct scorer with max score error below `6e-7` before training.

## Interpretation

This is a bounded negative for the straightforward hard-assignment pairwise
implementation. It does not disprove AVQ, Distill-VQ, QINCo, or a carefully
regularized listwise method. It does show that replacing reconstruction with a
naive pairwise teacher loss is not a drop-in improvement for this tiny
per-block codebook: assignments and centers co-adapt, and the held-out shell
quality collapses despite lower training loss.

Together with the production-shaped ADC48 control, the current evidence does
not justify another blind ADC capacity sweep. A future learned codec would
need an explicitly stabilized objective (assignment refresh policy, stronger
reconstruction/score calibration, and repeated shuffled folds) before any
storage or native implementation work.

Raw output (kept locally): `tmp/thq-adc-pairwise.json`, SHA-256
`e5ca75666c785a260bdc1e5f0690ed4942cdbc98471c0fe4f99f378f3c8865dc`.
Runner SHA-256:
`30951732ab249efd7f48d19097f546447000e7aef474337c7878e965bd8b8b77`.
