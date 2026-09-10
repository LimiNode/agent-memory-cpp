# Landmark-affinity THQ locality

Date: 2026-09-08.

## Question

Can a data-adaptive semantic coordinate system make exact E5 neighbours more
local in a compact binary code than raw-coordinate THQ or random-hyperplane
hashing?  The tested representation is:

```text
normalized E5 x
  -> spherical k-means landmarks C
  -> phi(x) = x C^T
  -> per-landmark quantile thermometer code
  -> exhaustive Hamming ranking
```

The experiment deliberately separates two questions.  The FP32 affinity
oracle tests the metric induced by the landmark profile.  THQ3/THQ4 then test
whether thermometer quantization produces useful Hamming locality.

## Frozen protocol

The runner uses the same frozen DE-1M vectors, 152 queries, exact E5 top-10
teacher IDs, and qrels as the flat THQ study.  It evaluates 32, 64, 128, and
256 directions from both spherical k-means and seeded Gaussian random
families.  K-means uses the first 100,000 normalized document vectors for 15
iterations.  For each direction count it materializes `phi(x)` in chunks,
evaluates naive/cosine/Gram-whitened affinity controls, and builds THQ3/4/5
codes using per-direction training-prefix quantiles.

The affinity score is exactly `phi(q) dot phi(x)`.  Because this equals
`q^T C^T C x`, it is a deliberately simple linear-affinity control, not the
original cosine metric and not a Gram-corrected or whitened metric.  The
Hamming implementation is a portable Python/NumPy reference; its timing is
not a native product claim.

Runner:
`tools/agent-memory-bench/evaluate-landmark-affinity-thq.py`.

Raw artifacts are not committed:

* corrected full matrix: `tmp/landmark-affinity-v2-full/result.json`, SHA-256
  `c018502c02b193f88046ed5d0d140483e7d6c365dd167d752fcb7874d0231ead`;
* Gaussian M=256 rank replay: `tmp/landmark-affinity-v2-ranks/result.json`,
  SHA-256 `8e5501e9fdfd64aea55503fd807c37ceed4f5277dbf9fb2452f84789940b5704`
  (raw output intentionally untracked).

## Results (corrected levels)

Mean exact-E5 top-10 survival under exhaustive ranking:

| representation | bytes/doc | @256 | @1k | @5k | @10k |
|---|---:|---:|---:|---:|---:|
| k-means naive-affinity dot M32 | 128 | .000 | .000 | .001 | .001 |
| k-means naive-affinity dot M64 | 256 | .000 | .000 | .001 | .002 |
| k-means naive-affinity dot M128 | 512 | .000 | .000 | .001 | .002 |
| k-means naive-affinity dot M256 | 1024 | .000 | .000 | .001 | .002 |
| affinity THQ3 M32 (2 thresholds) | 8 | .014 | .032 | .076 | .119 |
| affinity THQ4 M32 (3 thresholds) | 12 | .018 | .047 | .102 | .144 |
| affinity THQ5 M32 (4 thresholds) | 16 | .022 | .051 | .122 | .161 |
| affinity THQ3 M64 (2 thresholds) | 16 | .039 | .066 | .133 | .179 |
| affinity THQ4 M64 (3 thresholds) | 24 | .047 | .086 | .161 | .195 |
| affinity THQ5 M64 (4 thresholds) | 32 | .051 | .095 | .177 | .213 |
| affinity THQ3 M128 (2 thresholds) | 32 | .047 | .085 | .157 | .194 |
| affinity THQ4 M128 (3 thresholds) | 48 | .058 | .094 | .172 | .214 |
| affinity THQ5 M128 (4 thresholds) | 64 | .066 | .118 | .188 | .230 |
| affinity THQ3 M256 (2 thresholds) | 64 | .058 | .103 | .175 | .211 |
| affinity THQ4 M256 (3 thresholds) | 96 | .076 | .116 | .197 | .234 |
| affinity THQ5 M256 (4 thresholds) | 128 | .077 | .122 | .214 | .256 |

Increasing the number of landmarks helps the Hamming code, but the best row
retains only `.077` of exact neighbours at K=256 and `.256` at K=10k.  This is
far below raw THQ4 (`.9993` at K=256 in the frozen flat scan) and below both
256-bit and 512-bit random-hyperplane controls.

The direction-family control changes that conclusion.  Mean survival for
Gaussian random directions at M=256 was:

| representation | bytes/doc | @256 | @1k | @5k | @10k |
|---|---:|---:|---:|---:|---:|
| Gaussian affinity THQ3 | 64 | .910 | .964 | .991 | .993 |
| Gaussian affinity THQ4 | 96 | .939 | .976 | .997 | .997 |
| Gaussian affinity THQ5 | 128 | .949 | .978 | .997 | .999 |

The corresponding continuous controls at M=256 were `.545/.916` for naive
dot, `.973/1.000` for cosine-normalized affinity, and `.945/.999` for
Gram-whitened affinity at K=256/K=10k.  Direction placement is therefore a
decisive factor, not merely a quantizer detail.

Gaussian M=256 Hamming teacher-rank quantiles were:

| code | r50 | r95 | r99 | worst |
|---|---:|---:|---:|---:|
| THQ3 (2 thresholds) | 10 | 639 | 4,533 | 27,009 |
| THQ4 (3 thresholds) | 8 | 345 | 2,129 | 28,455 |
| THQ5 (4 thresholds) | 8 | 257 | 1,853 | 18,363 |

These ranks are a strong locality signal, but they were measured by the
portable Python scan and still require native throughput/bytes confirmation.

The near-zero k-means FP32 dot result shows that naive `phi(q) dot phi(x)` is
not an adequate surrogate for E5 cosine on this fixture.  It does not apply
to every direction family or metric: Gaussian directions reach `.973` cosine
survival at K=256, and Gram-whitened k-means reaches `.998` at M=256.  The
quantized result therefore has to be interpreted by both direction family and
metric rather than as a blanket rejection of affinity features.

## Decision

K-means landmark-affinity THQ is rejected as an indexing representation.  The
same experiment with Gaussian random directions is retained as an open line:
the corrected full matrix found substantially stronger locality for random
directions, so the earlier blanket rejection of affinity features was too
strong.

A follow-up must report direction family and metric separately.  The justified
controls are cosine-normalized affinity, Gram-whitened scoring, and a
nonlinear RBF/shell feature, each with a fresh FP32 ceiling gate.  The Gaussian
random-direction result must first be confirmed with rank/tail and native
cost measurements before any MIH/LSH index is built.

## Limitations

Landmarks use one deterministic training prefix and one corpus.  The runner
measures resident-array research behavior, not MDBX pages or cold storage.
The FP32 row is an affinity-dot oracle only; cosine-normalizing the affinity
vectors, whitening, or applying the pseudoinverse Gram metric may produce a
different result and were not silently conflated with this run.
