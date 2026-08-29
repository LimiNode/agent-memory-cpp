# NeuRoute R0 representation-ambiguity diagnostic

## Context

- Date: 2026-08-29
- PR: stacked on the teacher-objective ablation
- Status: full DE-1M diagnostic and independent byte replay complete

## Question

Does the frozen 22-dimensional R0 query-address representation contain direct
collisions or strong local teacher ambiguity that can explain why the nonlinear
and teacher-objective studies remain far below the privileged sparse order?

## Frozen protocol

The document partition, K8 prototypes, exact top-1024 shortlist, static
exact-E5 discounted gain-density teacher, and three DE-1M seeds are inherited
unchanged. The diagnostic scans all `8,141 * 1,024` cached rows per seed for:

1. exact raw-float32 collisions;
2. collisions after independently standardizing each R0 feature, clipping to
   four standard deviations, and quantizing it to 8 or 12 bits;
3. positive-versus-zero-target and privileged-top256 membership disagreement
   within those collision groups.

The local-neighbour diagnostic uses 256 query shortlists selected per seed by a
teacher-blind frozen permutation. It computes exact within-query distances in
normalized R0 space, teacher disagreement by distance, positive-to-negative
nearest-neighbour rates, and a five-fold selection over `k = 1/4/8/16`.
Neighbour labels are privileged and therefore define a local recoverability
ceiling, not a deployable scorer. A within-query target shuffle and the frozen
prototype order are reported as controls.

## Results

No raw or quantized exact collision was observed in any of the 25,009,152 rows.
This means the experiment does **not** provide a formal collision proof of R0
insufficiency.

| Seed | Raw positive/negative collisions | 8-bit | 12-bit | Positive whose nearest R0 neighbour is negative | CV local-kNN gain | Shuffled | Prototype order |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026082701 | 0 | 0 | 0 | .8627 | .5251 | .2543 | .6555 |
| 2026082702 | 0 | 0 | 0 | .8720 | .4619 | .2022 | .6733 |
| 2026082703 | 0 | 0 | 0 | .8635 | .4986 | .2499 | .6765 |

The privileged order has gain coverage `1.0` in this teacher-space diagnostic:
there are at most ten target-bearing addresses, so a 256-address privileged
budget contains all of them. Local kNN is substantially above the shuffled
control but remains below the original prototype order on every seed. Roughly
86%--87% of positive rows have a zero-target row as their nearest R0 neighbour.
Teacher disagreement and local target variance also rise with normalized R0
distance.

The result therefore supports empirical local ambiguity, while carefully
stopping short of an impossibility claim. The absence of exact collisions is
not surprising: R0 includes normalized shortlist rank and 21 other float
features, making byte-identical rows unlikely. Conversely, the poor local
recoverability shows that merely adding another smooth function over the same
compressed statistics has little observed headroom.

This licenses the already predeclared matched representation ladder:

```text
R0: scalar K8 statistics
R1: projected raw K8 prototype geometry with invariant pooling
R2: query-conditioned gating over the same projected K8 geometry
```

R3 document summaries and a stateful scheduler remain outside this PR.

Result SHA-256 is
`4a960128d88693962a0a2805f8dea31559d9a227cddfb71efb0b3c17ea48b2db`.
Independent replay reproduced the complete result byte for byte; evidence
SHA-256 is
`c309e6c4a43b36a03792079cb2d582d9f26a43b1ea869e17330d30651fc44f9a`.

## Limitations

- Quantized collision absence applies to the frozen per-feature clipping and
  scalar encoding, not to every possible deployment codec.
- The local-kNN ceiling consumes privileged labels from the evaluated query;
  it measures representation smoothness and is not an inference recipe.
- The pseudo-supervised training teacher is exact E5 top-10 gain density. This
  diagnostic does not replace the authoritative internal cascade evaluation in
  the next representation study.
- Approximate neighbour ambiguity is empirical evidence, not a mathematical
  proof that every possible nonlinear R0 model must fail.
