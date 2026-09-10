# Runtime-anchor and spherical THQ geometry oracle

Date: 2026-09-07. This follow-up removes the privileged teacher anchor from
the ray/segment experiment and checks the cosine-geometry variant.

## Protocol

All vectors are L2-normalized. For each query, prototypes are ranked by
distance to a shared-alpha segment. `linear` uses the Euclidean chord;
`spherical` uses the minor great-circle arc (normalized chord and slerp have
the same locus on the unit sphere). Anchor sources are:

* `teacher`: exact teacher top-1 prototype, an upper bound;
* `nearest-prototype`: nearest prototype by query cosine, an exhaustive but
  runtime-available control;
* `ivf`: nearest prototype from the top-1 4096-cell IVF centroid, a cheap
  routing proxy.

The reference is teacher top-10 prototype survival. This remains an oracle:
all prototype scores are exhaustive and no index is built.

## Result

Mean survival over 152 queries, with a 10,000-candidate budget:

| anchor source | linear segment | spherical segment |
|---|---:|---:|
| privileged teacher top-1 | .9816 | .9803 |
| nearest E5 prototype | .9816 | not run separately (same anchor control) |
| prototype-IVF top-1 | .9197 | not run separately |

The corrected privileged linear budget curve is `.9020 / .9289 / .9526 /
.9678 / .9776 / .9816` at K `256 / 512 / 1024 / 2048 / 5000 / 10000`.
Intermediate-K values in the earlier note were invalid because an unsorted
`argpartition` prefix had been used; the runner now sorts the selected max-K
set before taking prefixes. The spherical curve is `.8868 / .9197 / .9382 /
.9612 / .9750 / .9803`.

## Interpretation

The strong result is not an artifact of the teacher target alone: nearest
prototype selection reproduces the same curve on this fixture. However, the
cheap prototype-IVF top-1 anchor drops to .9197 at 10k, so the open problem is
anchor routing rather than the segment distance itself. Four-anchor min-distance
is not a safe default; it previously fell to .9605 because the union admitted
too many distractors.

Linear and spherical geometry are effectively tied here. The choice between
them is therefore not the current bottleneck. The next meaningful experiment
is a calibrated anchor schedule (confidence-weighted top-1/top-2/top-4) and a
discrete shared-alpha transition approximation, both evaluated against the
same candidate and tail-quality gates.

## Limitations

The nearest-prototype row is still an exhaustive scan and the IVF row uses a
single simplified centroid assignment, not the complete production
prototype-IVF generator. No latency, bytes, qrels nDCG or physical-probe
quality is implied by these routing-ceiling numbers.
