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
teacher IDs, and qrels as the flat THQ study.  It trains 32, 64, 128, and 256
spherical k-means landmarks on the first 100,000 normalized document vectors
for 15 iterations.  For each landmark count it materializes `phi(x)` in
chunks, evaluates the raw affinity dot product, and builds THQ3/THQ4 codes
using per-landmark training-prefix quantiles.

The affinity score is exactly `phi(q) dot phi(x)`.  Because this equals
`q^T C^T C x`, it is a deliberately simple linear-affinity control, not the
original cosine metric and not a Gram-corrected or whitened metric.  The
Hamming implementation is a portable Python/NumPy reference; its timing is
not a native product claim.

Runner:
`tools/agent-memory-bench/evaluate-landmark-affinity-thq.py`.

Raw artifacts are not committed:

* `tmp/landmark-affinity-full/m32.json`, SHA-256
  `d961f5c5ca83dbb11957c2a03f34bc6514141b9cf4fd561aa4784fb534b2cc8e`;
* `tmp/landmark-affinity-full/m64-256.json`, SHA-256
  `d703ccc361253ea6b091bf5736da31c79c79114c79234ac2b90a0f0b5fb5d099`.
* `tmp/landmark-affinity-full/m256-ranks.json` was rerun with teacher-rank
  accounting (the raw file is intentionally untracked).

## Results

Mean exact-E5 top-10 survival under exhaustive ranking:

| representation | bytes/doc | @256 | @1k | @5k | @10k |
|---|---:|---:|---:|---:|---:|
| FP32 affinity M32 | 128 | .000 | .000 | .001 | .001 |
| FP32 affinity M64 | 256 | .000 | .000 | .001 | .002 |
| FP32 affinity M128 | 512 | .000 | .000 | .001 | .002 |
| FP32 affinity M256 | 1024 | .000 | .000 | .001 | .002 |
| affinity THQ3 M32 | 12 | .018 | .047 | .102 | .144 |
| affinity THQ4 M32 | 16 | .022 | .051 | .122 | .161 |
| affinity THQ3 M64 | 24 | .047 | .086 | .161 | .195 |
| affinity THQ4 M64 | 32 | .051 | .095 | .177 | .213 |
| affinity THQ3 M128 | 48 | .058 | .094 | .172 | .214 |
| affinity THQ4 M128 | 64 | .066 | .118 | .188 | .230 |
| affinity THQ3 M256 | 96 | .076 | .116 | .197 | .234 |
| affinity THQ4 M256 | 128 | .077 | .122 | .214 | .256 |

Increasing the number of landmarks helps the Hamming code, but the best row
retains only `.077` of exact neighbours at K=256 and `.256` at K=10k.  This is
far below raw THQ4 (`.9993` at K=256 in the frozen flat scan) and below both
256-bit and 512-bit random-hyperplane controls.

For M=256 the Hamming teacher-rank quantiles were `123,010/839,779/964,880`
(`r50/r95/r99`) for THQ3 and `106,550/830,101/966,711` for THQ4.  The worst
teacher rank was approximately 997k in both cases, confirming a broad
non-local tail rather than a small number of isolated misses.

The near-zero FP32 affinity result is the more important failure.  It shows
that naive `phi(q) dot phi(x)` is not an adequate surrogate for E5 cosine on
this fixture.  The non-zero THQ rows do not rescue that scorer; quantile
binarization merely creates a different coarse rank with weak semantic
locality.

## Decision

Naive linear landmark-affinity THQ is rejected as an indexing representation.
It does not license affinity-MIH, affinity-LSH tables, or a physical persisted
index.  This closes only the exact formulation above; it does not claim that
all landmark features are impossible.

A follow-up would need to change the metric before changing the index.  The
only justified candidates are a centered/whitened affinity profile,
Gram-corrected scoring, or a nonlinear RBF/shell feature with a fresh FP32
ceiling gate.  Such a variant must first exceed the random-hyperplane and raw
THQ locality controls at the same candidate budget.  Until then, research
effort stays on flat THQ and the improved prototype-IVF/K8/K32/R0 cascade.

## Limitations

Landmarks use one deterministic training prefix and one corpus.  The runner
measures resident-array research behavior, not MDBX pages or cold storage.
The FP32 row is an affinity-dot oracle only; cosine-normalizing the affinity
vectors, whitening, or applying the pseudoinverse Gram metric may produce a
different result and were not silently conflated with this run.
