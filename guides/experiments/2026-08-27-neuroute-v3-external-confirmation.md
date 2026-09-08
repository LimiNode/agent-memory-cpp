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
bootstrap comparisons. Its receipt separates `integrity_replay_passed` from
`quality_gates_passed`, so a sound negative result remains auditable rather
than being misreported as a replay failure.

## 2026-08-27 French result

The full six-model matrix completed on the frozen 25,000-document French
root. The prepared, E5, and ITQ/ADC input manifests have SHA-256 values
`0cc309906d934d529e7d89620c46694a65cd5d18be47122f50c4812bffabb8f3`,
`5aff7bfb7088006adce88f213a1ded381f7efc212bd2b5b7ffca6f105399948f`, and
`09fcbfd584cad9b57e46f7fdf8d0fa6be553e8bcf89351cd352a38a1f6b55569`.
The compact result has SHA-256
`4da270f9b48f100763850a618c3de052f572aa788126ea1683b2803cdbe307f0`.
Independent replay of all six model bytes, all 24 configuration rows, every
internal contribution, the recomputed median thresholds and internal means,
and the PCA control succeeded. The schema-v2 integrity receipt has SHA-256
`4ad145a94ad0f3491cf38492fe06c6747af8daa295d40578da04b900b37615b9`;
its integrity replay passes while its quality-gate field correctly remains
false for this inconclusive architecture result.

At the fixed 512-probe / hard-10% headline on the untouched 86-query French
partition, the three-seed means were:

| Treatment | Candidate fraction | ADC E5 top-10 survival | nDCG@10 |
| --- | ---: | ---: | ---: |
| Positive-only shared encoder | 10.00% | 37.64% | .3847 |
| Dynamic false-positive encoder | 10.00% | 74.26% | .6064 |
| Symmetric PCA control | 9.77% | 60.12% | .5639 |

The causal mechanism transfers cleanly. Dynamic mining gains `+36.63`
survival points over the otherwise identical positive-only control, with
paired 95% bootstrap interval `[+32.60, +40.66]`; nDCG gains `+0.2217`, with
interval `[+0.1699, +0.2754]`. The predeclared mechanism gate passes.

Dynamic v3 also has a positive point estimate against PCA: `+14.15` survival
points (`[+8.72, +19.61]`) and `+0.0426` nDCG. However, the paired nDCG
interval is `[-0.0202, +0.1084]`. Therefore the strict external architecture
gate, which required positive lower bounds for *both* survival and nDCG,
does **not** pass. This is neither a failure of the dynamic-mining mechanism
nor evidence that it beats PCA across languages; the correct result is an
inconclusive architecture comparison at this French sample size.

No French retuning, width selection, scale transfer, or binary-aware loss is
licensed by this result. The next legitimate decision is whether to register a
second independent external confirmation with the same frozen recipe, not to
search French hyperparameters until the gate passes.
