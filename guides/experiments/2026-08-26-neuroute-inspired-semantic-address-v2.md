# NeuRoute-inspired semantic address v2

Date: 2026-08-26. This is the final, deliberately bounded centroid-free
challenger after the constrained shared encoder in PR #179 collapsed its
addresses and did not beat the symmetric PCA control. It is not a reproduction
claim for NeuRoute, whose data scale and full system differ materially.

## Question and success criterion

Can a jointly shared document/query representation with selected near-pair
training, explicit anti-collapse regularization, and logit-guided address
probing materially beat the existing symmetric PCA confidence pool at the same
hard 10% candidate ceiling, without a runtime centroid reorder?

The primary treatment must exceed the control's ADC top-10 survival by at least
three absolute percentage points with a positive paired-query 95% bootstrap
lower bound. Its nDCG@10 may fall by no more than one absolute percentage point.
The selected result is not a production claim: it requires later external
confirmation before any scaling or product decision.

The paired bootstrap uses exactly 10,000 resamples and seed `2026082699`; the
reported confidence intervals are therefore replayable rather than an informal
stability label.

## Frozen protocol

The machine-readable contract is
`tools/agent-memory-bench/neuroute-inspired-semantic-address-v2.example.json`.
It uses the established deterministic `324 / 162 / 162` train/configuration/
internal query split. Internal evaluation cannot select a model, width, seed or
probe budget.

The shared MLP is `384 -> 96 -> 64 -> L` with `L = 12, 16, 20, 24`. Three
predeclared seeds are measured. It learns from all frozen documents plus only
the permitted training queries: source-neighbour pairs are mined from frozen
E5 cosine geometry and the loss compares their source cosine with normalized
latent cosine. The loss also has an explicit minimum-variance term and an
off-diagonal covariance penalty. A no-covariance ablation is limited to 16 bits
and exists solely to test whether the diversity term, rather than median
thresholding alone, prevents the v1 collapse.

Each epoch deterministically uses the next ranked neighbour for every document
and training query (`epoch modulo 16` and `epoch modulo 10` respectively); a
128-query batch is paired with each 512-document batch. Thus the pair-mining
description is executable rather than an unspecified sampling choice.

Documents receive one median-thresholded address. Queries are evaluated with
two fixed orders over the same address space:

- independent-logit best-first enumeration;
- a hard-code Hamming-order control.

The configuration partition selects a single full-loss bit width and probe
budget at the 10% hard candidate ceiling. All three seeds for that selected
configuration are then evaluated exactly once on the internal partition. The
centroid-free result remains the headline. A local-centroid-reordered arm may
be reported only as a practical diagnostic and cannot make the centroid-free
gate pass.

## Required evidence

Every execution must preserve frozen manifests and split identity, source L2
audit receipt, model SHA-256, selected source pairs, code entropy, per-bit
marginals, off-diagonal covariance, occupied-address count, bucket-size
distribution/Gini, query-to-nearest-document code distance, probes, candidate
mass, per-query cascade contributions and paired bootstrap inputs. The v2
report must distinguish training, configuration selection and internal
evaluation throughout.

The predeclared matrix is intentionally small: twelve full models and three
16-bit no-covariance ablations. It does not add further depths, replications,
loss weights, or post-hoc probing variants. If 24 bits shows a credible,
monotonic benefit in this protocol, a wider code is a future calibration-only
question; otherwise this line ends here rather than becoming an unbounded
hyperparameter search.

## 2026-08-26 es-25k result

The runner completed all fifteen planned training runs on the frozen roots.
Its compact `result.json` has SHA-256
`9c85f49baf79d56d3431909e45a92d5813269a10e4e024731c77f92679a80671`.
Before any training, it validates the pinned schema-v2 normalization receipt:
the receipt contract, source-result, manifests, and audit-writer hashes must
all agree with the local frozen roots. It then binds that receipt, deterministic
query split, mined-pair digests, every saved model hash, all configuration rows,
and per-query internal cascade contributions.

The configuration partition selected the full-loss 12-bit encoder with
independent-logit best-first order and the predeclared maximum 256 probes. The
three fixed seeds were evaluated once on internal evaluation:

| Internal treatment | Candidate fraction | ADC E5 top-10 survival | nDCG@10 |
| --- | ---: | ---: | ---: |
| Full v2, seed 2026082601 | 6.14% | 29.44% | 0.3422 |
| Full v2, seed 2026082602 | 6.11% | 28.27% | 0.3774 |
| Full v2, seed 2026082603 | 6.11% | 28.64% | 0.3556 |
| Full v2 mean | 6.12% | 28.79% | 0.3584 |
| Symmetric PCA control | 9.76% | 66.36% | 0.6523 |
| 16-bit no-covariance ablation mean | 0.44% | 5.37% | 0.0869 |

The paired three-seed-versus-control bootstrap gives an ADC-survival delta of
`-37.57` percentage points (95% interval `[-41.48, -33.72]`) and nDCG@10 delta
of `-29.39` points (`[-34.22, -24.50]`). The predeclared success gate therefore
fails decisively.

The diversity mechanism itself worked. Full 12-bit models occupied
`4064--4067` of 4096 addresses with about `11.83` bits of code entropy and mean
off-diagonal latent correlation near `0.02`. The same-width 16-bit
no-covariance ablation had larger correlation (`0.032--0.039` versus
`0.021--0.022` with the full loss), but broadly similar occupancy and entropy.
It therefore supports the narrow claim that covariance reduces measured latent
correlation, not a stronger causal claim that it alone created useful code
diversity or routing quality. The failure is nevertheless not the old
median-threshold address collapse: these objectives produced diverse codes that
were still poor semantic routers.

There is one precise limitation: 256 probes reached only 6.12% candidate mass
for the selected 12-bit model, below the permitted 10% ceiling and below the
9.76% symmetric-control point. We must not represent this as a final
matched-10%-budget architectural closure. It is a clean negative result for
the *predeclared 256-probe v2 protocol*. Any later wider-probe test must use a
new calibration split and a new untouched confirmation partition; it cannot
retune this already observed internal evaluation. Until such a protocol exists,
the practical baseline remains the symmetric confidence pool followed by local
float centroid refinement.
