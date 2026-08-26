# NeuRoute v3 external French confirmation

Date: 2026-08-27. This protocol is an external confirmation of the successful
German dynamic false-positive semantic-address result. It is not a retuning or
an ablation of that result.

## Question

Does the already frozen v3 mechanism transfer to a previously unused French
MIRACL 25k corpus at the same 12-bit / 512-probe / ten-percent-candidate
operating point?

## Frozen setup

The source is French MIRACL dev data from the immutable corpus revision
`d921ec7e349ce0d28daf30b2da9da5ee698bef0d` and judgments revision
`5be20db9509754dadad47689368639fcec739c00`. Deterministic stable-hash
materialization produces 25,000 evaluation documents and all 343 French dev
queries. The materialized manifest roots are bound in the committed contract
before any model training occurs.

The deterministic query partition is `172 / 85 / 86` for train,
configuration, and untouched internal evaluation. The internal partition may
not choose a seed, checkpoint, loss, width, probe count, or mining setting.

The only learned treatment is copied exactly from German v3:

- `384 -> 96 -> 64 -> 12`, three fixed seeds, 80 epochs;
- positive geometry, variance and covariance terms unchanged;
- 20-epoch warm-up and remine epochs `20 / 40 / 60`;
- take four E5-farthest documents from the current latent top-32;
- false-positive margin `0.05`;
- median single-address placement, 512 logit-guided probes, and a hard 10%
  candidate ceiling.

The causal positive-only shared encoder and symmetric PCA control are replayed
unchanged. Configuration emits the fixed `64 / 128 / 256 / 512` frontier only;
the headline remains 512 probes.

## Decision rule

The external confirmation passes only when all of the following hold on the
untouched 86-query internal split:

1. every learned row respects the per-query 10% candidate ceiling;
2. dynamic mining has its predeclared positive survival mechanism gain over
   the otherwise identical positive-only control;
3. dynamic v3 exceeds PCA in both ADC E5-top-10 survival and reranked
   nDCG@10, and both paired 95% bootstrap lower bounds are strictly positive.

A negative result is preserved as a valid external evidence receipt. It is not
an invitation to tune French hyperparameters. Scale transfer and a mining-rule
ablation are gated on a positive external confirmation.

## Evidence

The companion runner persists model byte hashes, the exact query split,
configuration rows, per-query internal contributions, input-root identities,
and hashes of the external runner plus all algorithmic dependencies. Its
evidence writer independently reloads every model, replays each result and
the PCA control, checks the candidate ceiling, and then recomputes both paired
bootstrap comparisons.
