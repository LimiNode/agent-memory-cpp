# NeuRoute training-sufficient fixed Top-M router frontier

Date: 2026-09-02

## Question

Was the negative learned-router result in PR #268 caused primarily by its
few-shot regime, and can K1-conditioned residual distillation produce a
high-fidelity address shortlist before exact local K8?

## Preregistered extension

PR #268 remains the 76-query/four-fold data-efficiency control. This follow-up
trains independently on nested pools of 153 German training queries and 8,141
query-like vectors. The larger pool consists of those same 153 German queries
plus 7,988 MIRACL ES/FR/RU pseudoqueries. The additional topics have no German
qrels and are used only for K8 and exact-document teacher distillation.

The frontier compares centroid K1, a direct rank-64 global-address scorer, and
K1 plus rank-64 predictions of signed exact `K8Score-K1Score`, actionable
pseudo-gain, or their hybrid. Negative K8 corrections are retained rather than
clipped away. Configuration requests select model hyperparameters and the
Top-M budget from `1024/2048/4096/8192`; the locked internal partition is not
inspected until the selection is frozen. Hyperparameters are selected at the
maximum product-eligible budget, 4,096; the 8,192 sensitivity row cannot tune
the product model.

Diagnostics include unweighted and rank-discounted global-K8 coverage,
K8-margin-weighted missed utility, actionable-address coverage, missed teacher
rank, lost addresses containing FP32 final-top10 documents, worst-query loss,
and the unchanged candidate/Hamming/ADC/final R4 metrics. Ordinary teacher-set
recall is not used as the sole success proxy.

Global FP32 K8 over all occupied addresses is an offline teacher/reference,
not a production candidate. The 8,192-address row is a sensitivity control;
only a treatment at or below 4,096 addresses can pass the product-budget gate.
The diagnostic rank-64 output table still scores every occupied address, so it
tests whether the learned signal exists; it is not itself the target execution
format. A surviving signal must subsequently be represented by the cheap
12/14/16-bit selector frontier before native product integration.

Directional generator timing is partitioned before selection and covers the
complete requested-budget path: learned address scoring, K1 scoring for every
residual treatment, score combination/standardization, and Top-M selection.
Rejected model orders and training-cache mappings are released before native
R4 replay so the Python frontier does not impose artificial memory pressure on
the downstream timing.

The historical listwise caches store 16-bit address codes, whereas the local-K8
hook consumes compact occupied-row IDs. The runner validates and applies the
current topology's address-to-row mapping before fitting any output head; raw
cache integers are never interpreted directly as compact rows.

## Result

The compact result has SHA-256
`2c238bd31a4105d404328c57479991e872e5f59919a1b7454945a0201e2f9cbe`;
the validated evidence has SHA-256
`8bc7700f8f22ce5bcd5d1a59c9bf46a6371365f9eb7beda3585500e66b8689fd`.

At the product-eligible boundary, M=4,096, configuration produced:

| Generator | Train rows | Weighted / margin-weighted teacher coverage | Final overlap | Candidate / Hamming / ADC overlap | Mean / worst-stratum nDCG loss | Worst-query loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Centroid K1 | n/a | n/a | 0.9654 | 0.9922 / 0.9927 / 0.9849 | 0.00886 / 0.02695 | 1.0000 |
| Direct rank-64 | 153 | 0.6904 / 0.7562 | 0.6399 | 0.7355 / 0.7467 / 0.7075 | 0.15149 / 0.20574 | 1.0000 |
| Direct rank-64 | 8,141 | 0.7408 / 0.8048 | 0.6820 | 0.7946 / 0.8060 / 0.7560 | 0.12458 / 0.17424 | 1.0000 |
| K1 + signed exact K8 delta | 153 | 0.9982 / 0.9960 | 0.9662 | 0.9926 / 0.9930 / 0.9858 | 0.00789 / 0.02695 | 1.0000 |
| K1 + signed exact K8 delta | 8,141 | 0.9981 / 0.9958 | 0.9662 | 0.9923 / 0.9928 / 0.9852 | 0.00895 / 0.02695 | 1.0000 |
| K1 + actionable gain | 153 | 0.9980 / 0.9955 | 0.9632 | 0.9921 / 0.9926 / 0.9844 | 0.00812 / 0.02695 | 1.0000 |
| K1 + actionable gain | 8,141 | 0.9976 / 0.9945 | 0.9531 | 0.9911 / 0.9914 / 0.9814 | 0.01329 / 0.02830 | 0.6309 |

Increasing the pool materially helps the direct output head but leaves it far
from usable. It does not improve the exact residual, and the multilingual
pseudoqueries make actionable and hybrid supervision worse. The selected
diagnostic model sizes are about 32.5 MiB for direct rank-64 and 127.9 MiB for
K1-conditioned treatments; these are signal probes rather than the intended
compact execution format.

No treatment passed configuration. The registered fallback therefore opened
the closest M=8,192 exact-delta row and a distinct actionable row. Both failed
on reused confirmation:

| Opened row | Final overlap | Candidate / Hamming / ADC overlap | Mean / worst-stratum nDCG loss | Worst-query loss |
| --- | ---: | ---: | ---: | ---: |
| Exact delta, 8,141, M=8192 | 0.9811 | 0.9962 / 0.9965 / 0.9916 | 0.01396 / 0.02648 | 1.0000 |
| Actionable, 153, M=8192 | 0.9794 | 0.9962 / 0.9966 / 0.9912 | 0.01616 / 0.02301 | 1.0000 |

The zero p95 query loss on several configuration residual rows coexists with a
worst-query loss of 1.0. This confirms that sparse catastrophic misses, not
average teacher coverage, dominate the remaining failure.

## Decision

The few-shot caveat in PR #268 is now resolved, but the learned fixed Top-M
branch remains negative. Training on 8,141 multilingual teacher rows does not
produce a usable direct generator, and neither signed K8 correction nor
actionable pseudo-gain repairs K1 at M<=4,096. Even the non-product M=8,192
fallback fails reused confirmation.

This result does not license native integration or production use. It also
does not claim that a compact bitwise model has been trained: the 65K-output
rank-64 table was deliberately a signal-existence diagnostic. Under the
registered sequence, jointly trained prefix-aware 12/14/16 compression is not
activated by this result. The next experiment still replays the retained
12/14/16 heads to measure representational and execution behavior before the
common comparison with prototype IVF.

## Limitations

- The 8,141-row treatment is multilingual pseudo-supervision, not 8,141 judged
  German requests.
- Requests 76--151 are mechanically excluded from fitting and selection in
  this runner, but their outcomes have appeared in preceding PRs. They are a
  reused confirmation partition, not a pristine never-observed holdout.
- The learned address table and Python scoring latency are diagnostic; native
  execution is considered only after a common learned-versus-ANN bake-off.
- A positive or negative ANN result does not activate or suppress this learned
  experiment; the two generator families are evaluated independently before
  the common bake-off.
- Current 12-bit and 16-bit partitions are independently trained, so no
  hierarchical prefix claim is made.

## Reproduction

The ignored raw result is
`tmp/neuroute-training-sufficient-router/result.json`. The runner binds the
query-pool hashes, all three training-cache manifests, the frozen layout/K8
manifests, the native executable, and the authoritative qrels receipt.
