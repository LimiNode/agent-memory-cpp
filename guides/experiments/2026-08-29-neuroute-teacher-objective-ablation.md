# NeuRoute teacher-objective ablation

## Context

- Date: 2026-08-29
- PR: stacked on the nonlinear/listwise capacity and training-density study
- Status: full DE-1M measurement and independent replay complete

## Question

With the DE-1M 16-bit partition, K8 prototypes, exact top-1024 address
shortlist, and the parent-selected nonlinear architecture/training count frozen
per seed, does a cascade-aware or conditional teacher recover the privileged
sparse ordering better than static exact-E5 gain density?

## Frozen protocol

The study compares three objectives at address budgets 128, 256, and 512:

1. discounted exact-E5 top-10 gain per posting entry;
2. independent actionable gain after Hamming768 and ADC64 per posting entry;
3. greedy conditional marginal actionable gain per added posting entry,
   distilled as a query-wise sequence-weighted ListNet distribution.

The conditional action pool is restricted to target-bearing addresses already
inside the frozen top-1024 shortlist. Addresses outside that shortlist and
non-target addresses cannot receive positive teacher reward. External ES/FR/RU
topics remain pseudo-supervision only; their qrels are not read.

The frozen cascade did not preserve its original ITQ transform. The 153 German
training queries use their authoritative frozen projections; external query
projection is derived once from 100,000 deterministic document
samples by regressing document E5 vectors onto their binary-ADC centroid
reconstructions. Before training, the derived transform must reproduce the 153
training plus 76 configuration query projections with at least 95% sign
agreement and correlation at least 0.98. Internal vectors and qrels remain
closed until all nine objective models are serialized.

## Decision rule

The direct gate requires every seed to reach actionable gain at least `.90`
with candidate fraction at most `.005` at budget 256. The alternative progress
gate requires at least half of the prototype-to-privileged-teacher gap to close
without exceeding `1.05x` prototype candidate mass. Passing does not license a
native or production activation in this PR.

## Results

Three-seed internal means are below.

| Treatment | Budget | Candidate fraction | Actionable gain | nDCG@10 |
|---|---:|---:|---:|---:|
| Prototype order | 128 | .002742 | .7621 | .5915 |
| Prototype order | 256 | .005415 | .8213 | .6219 |
| Prototype order | 512 | .010637 | .8566 | .6375 |
| Static gain density | 128 | .002327 | .7837 | .6071 |
| Static gain density | 256 | .005120 | .8387 | .6374 |
| Static gain density | 512 | .011036 | .8696 | .6402 |
| Independent cascade density | 128 | .002497 | .7916 | .5974 |
| Independent cascade density | 256 | .005429 | .8433 | .6361 |
| Independent cascade density | 512 | .011335 | .8696 | .6434 |
| Conditional sequence distillation | 128 | .002601 | .7935 | .6003 |
| Conditional sequence distillation | 256 | .005694 | .8421 | .6386 |
| Conditional sequence distillation | 512 | .011948 | .8723 | .6423 |
| Privileged teacher | 128 | .000993 | .9247 | .6481 |
| Privileged teacher | 256 | .002263 | .9197 | .6494 |
| Privileged teacher | 512 | .005780 | .9127 | .6498 |

The reconstructed external query transform passes its frozen validation gate:
German train-plus-configuration sign agreement is `.97288`, projection
correlation is `.99084`, and RMSE is `.01340`. German training queries use the
original authoritative projections.

Cascade-aware supervision helps only modestly. At budget 256, independent
cascade density improves mean actionable gain from `.8387` to `.8433` over
static gain density, while conditional sequence distillation reaches `.8421`.
The best mean final nDCG is the conditional treatment at `.6386`, only `.0012`
above static. Per-seed teacher-gap closure remains about `.16–.26`; no
treatment reaches the required `.50` on every seed. Candidate mass also exceeds
the `.005` direct threshold except for one individual static seed.

Thus neither the independent cascade teacher nor greedy conditional ordering
is the missing mechanism for the frozen static scorer. The privileged order
still demonstrates a real `.9197` actionable frontier at `.00226` mass, but
these teacher changes do not make that ordering learnable from the current
query/address representation.

Result SHA-256 is
`6fb06a01bc3dbcf2ec5db4fb408246be9af17e21d2cd03c039d574e8c467513c`.
Independent replay reproduced all nine model archives and the complete result
byte for byte; evidence SHA-256 is
`8d3464899c14fcb5cac332bca660eb25e63d213fbf6709d2e4e0a5e121c11a9b`.

## Limitations

- The externally applied Hamming/ADC transform is a validated deterministic
  reconstruction, not the missing original ITQ matrix.
- Sequence-weighted ListNet distills a greedy teacher order into the frozen
  static scorer; it is not reinforcement learning or a stateful policy.
- Exact prototype retrieval remains intentionally unoptimized.
