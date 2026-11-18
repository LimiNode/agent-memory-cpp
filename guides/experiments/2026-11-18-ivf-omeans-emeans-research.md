# O-means and E-means for IVF training

Status: `draft` / research-only / not a production recommendation

This note defines a bounded comparison of ordinary Faiss Lloyd k-means,
E-means, and O-means as alternative IVF centroid-training procedures. It does
not add an ANN runtime, a C++ API, a persisted schema, or a production
retrieval mode to `agent-memory-cpp`.

## Decision boundary

O-means is relevant to approximate nearest-centroid assignment during
training. It does not automatically reduce query-time IVF scanning. E-means
is a training procedure with a different update schedule; reported author
numbers are not transferable evidence for this repository. The reference for
O-means is Faiss PR #5917 at pinned commit `9ecbf0c2`; that reference must be
recorded in every measured receipt. The current exploratory case of 16,384
centroids and roughly 25,000 training vectors is intentionally treated as a
negative prior: it provides too few samples per centroid for a first claim.

The current `ExactVectorIndex`, MIH, binary routing, and Raw Markdown preview
are not direct O-means targets. The experiment must not alter any of them.

## Runner and fail-closed behaviour

`tools/agent-memory-bench/compare-ivf-training.py` is an offline receipt
runner. With no `.npy` inputs, or without a required dependency, it emits a
`NOT_EXECUTED` receipt. When NumPy and Faiss are available it measures the
ordinary Lloyd control and records E-means/O-means as `NOT_EXECUTED` until
separately pinned adapters and corpora are supplied. It never labels Lloyd
output as an E-means or O-means result.

The Lloyd control trains centroids exactly once. It installs those caller-owned
centroids into a fresh IVF quantizer, verifies a digest after installation, and
adds vectors without calling `IndexIVFFlat.train()` again. Input `.npy` files
and the centroid byte representation are SHA-256 bound in the receipt. The
minimum training-vectors-per-centroid guard is explicit and a failed guard is
`NOT_EXECUTED`, not a partial quality claim. Held-out nearest-centroid work is
performed in bounded query/centroid blocks; no `Q x K x D` distance tensor is
materialised.

Example contract check:

```text
python tools/agent-memory-bench/compare-ivf-training.py \
  --output .artifacts/ivf-training-not-run.json
```

The resulting JSON is evidence of execution state and provenance, not a
benchmark result. A `PARTIAL` receipt means only the explicitly listed methods
ran; it is not evidence that E-means or O-means were approximated by Lloyd.

## Required comparison matrix

For each method, corpus, seed, and centroid count, retain raw observations
and the exact software/hardware provenance:

- training time and build time;
- held-out MSE;
- cluster-size distribution and empty-cluster count;
- identical IVF retrieval after training;
- Recall@10 and Recall@100 at an equal scanned-document budget;
- p50/p95 query latency;
- resident/model memory and serialized index bytes;
- corpus identity, vector dimension, train/query split, seed, thread count,
  GPU model, driver, CUDA, Faiss commit, and method adapter commit.

The retrieval comparison must use the same IVF layout, metric, query set,
candidate budget, reranking policy, and exact ground truth. `nprobe` alone is
not an equal-work guarantee because list lengths differ; the receipt must
record the measured scanned-document count or reject the comparison.

## Acceptance and stop conditions

No method is promoted by training speed alone. A measured method is eligible
for a follow-up only if it has a complete receipt, no fabricated or inherited
metrics, and a matched retrieval comparison. A result on an RTX 4090 must not
be reported as an RTX 5090 result. If the pinned O-means or E-means adapter is
unavailable, the correct result is `NOT_EXECUTED`; production work continues
independently.
