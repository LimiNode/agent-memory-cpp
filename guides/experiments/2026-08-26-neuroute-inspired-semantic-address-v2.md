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
