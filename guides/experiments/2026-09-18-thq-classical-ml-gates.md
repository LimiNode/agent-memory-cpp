# THQ4 classical 152-query codec-function gate and ML sanity diagnostics

Date: 2026-09-18  
Context: `main` after merge #423 (`497f8f87a79651e3062ee2a14d635e5b32a65b4c`);
research branch `research/thq-classical-152`.

## Question

Does the frozen three-seed R4 candidate shell retain enough information for
classical residual codecs, and is the weak learned-decoder result caused by a
basic reconstruction/optimization failure rather than by the THQ4 feature
representation itself?

## Setup and evidence boundary

The replay uses the frozen 152-query R4 stream (5000--5099 unique candidate
IDs per query), bound by the external canonical
`semantic_r4_fused_candidate_materialization_v1` receipt. It reads the 1M-row
multilingual-E5 document vectors, detached 25k training vectors, and the
native-full-corpus THQ4/INT8 materialization. All model fitting is detached
from qrels and teacher IDs.

The reference runners are:

```text
tools/agent-memory-bench/run-thq-r4-classical-gate.py
tools/agent-memory-bench/run-thq-ml-sanity-gate.py
tools/agent-memory-bench/run-thq-r4-teacher-diagnostics.py
```

This is a **codec-function quality gate**: residual codes are formed from
FP32 candidate rows during replay. It is not a persistent side-code replay,
native SIMD benchmark, page-fault measurement, or production activation.
Raw outputs remain outside Git in `tmp/`; compact evidence and fail-closed
receipts are committed beside this note. The replay binds candidate raw/flat
files and receipt, all external input SHA-256 values, runner hashes, and model
hashes.

## Classical result

`candidate-fp32` is the quality ceiling when all frozen candidates may be
reranked. `thq4-fp32` is the ceiling after THQ4 top-128 membership. The table
reports both the teacher top-10 overlap and the primary qrels metric,
nDCG@10.

| arm | teacher mean | teacher p05 | teacher min | qrels mean | qrels p05 | qrels min |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| candidate FP32 ceiling | 0.9928 | 0.9000 | 0.9000 | 0.6542 | 0.0000 | 0.0000 |
| THQ4 -> FP32 | 0.9928 | 0.9000 | 0.9000 | 0.6542 | 0.0000 | 0.0000 |
| direct INT8 linear | 0.9895 | 0.9000 | 0.9000 | 0.6570 | 0.0000 | 0.0000 |
| RSLM-like 4-bit | 0.9697 | 0.9000 | 0.8000 | 0.6593 | 0.0000 | 0.0000 |
| RSLM-like 3-bit | 0.9658 | 0.9000 | 0.8000 | 0.6540 | 0.0000 | 0.0000 |
| RSLM-like 2-bit | 0.9329 | 0.8000 | 0.7000 | 0.6533 | 0.0000 | 0.0000 |
| hierarchical residual 3-bit | 0.9454 | 0.8000 | 0.7000 | 0.6550 | 0.0000 | 0.0000 |
| hierarchical residual 2-bit | 0.9375 | 0.8000 | 0.7000 | 0.6525 | 0.0000 | 0.0000 |
| hierarchical residual 1-bit | 0.9020 | 0.8000 | 0.6000 | 0.6514 | 0.0000 | 0.0000 |
| PQ32x8 | 0.8822 | 0.7000 | 0.6000 | 0.6472 | 0.0000 | 0.0000 |
| OPQ32x4 | 0.8553 | 0.7000 | 0.6000 | 0.6495 | 0.0000 | 0.0000 |
| PCA32x8 | 0.8467 | 0.7000 | 0.5000 | 0.6460 | 0.0000 | 0.0000 |
| THQ4 centroid | 0.8368 | 0.6000 | 0.5000 | 0.6484 | 0.0000 | 0.0000 |

The `candidate-fp32` and `thq4-fp32` top-10 IDs are identical for all 152
queries. Therefore the observed residual-codec loss is downstream of frozen
candidate membership, not a second THQ4 top-128 approximation. Paired qrels
bootstrap summaries are stored in the compact receipt; for example direct
INT8 versus candidate FP32 has mean delta `+0.00279`, 95% bootstrap interval
`[-0.00028, +0.00866]`, and worst-query delta `-0.0140`.

These are NumPy/Faiss reference quality results. They do not establish native
latency or a persistent storage layout.

## ML sanity result

Reconstruction MSE (train / 10k held-out document rows) was:

| control | train MSE | held-out MSE |
| --- | ---: | ---: |
| THQ4 centroid (zero residual) | 9.9909e-5 | 1.0038e-4 |
| PCA8 residual | 9.5652e-5 | 9.6179e-5 |
| PCA16 residual | 9.2532e-5 | 9.3240e-5 |
| PCA32 residual | 8.6941e-5 | 8.8062e-5 |
| linear AE32, random-init (64 epochs) | 8.7002e-5 | 8.8166e-5 |

The random-init linear bottleneck reaches the PCA32 reconstruction optimum
within `0.12%` on held-out vectors. This checks optimization of an exact-FP32
residual bottleneck only; it does **not** test whether THQ4 one-hot features
predict residuals. That question belongs to the teacher-shell diagnostic.

## Teacher-score diagnostics

Scores and top-10 overlap are measured inside each query's frozen candidate
shell against exact FP32 candidate scores. The decoder is a 1536->128->384
ReLU network trained with vector MSE plus teacher-score regression; ridge is a
detached linear one-hot control.

| split / arm | score-MSE mean | top-10 overlap mean |
| --- | ---: | ---: |
| train / centroid | 1.665e-4 | 0.831 |
| train / ridge | 1.928e-4 | 0.818 |
| train / decoder | 5.850e-5 | 0.814 |
| held-out / centroid | 1.670e-4 | 0.872 |
| held-out / ridge | 1.984e-4 | 0.866 |
| held-out / decoder | 6.409e-5 | 0.866 |

The zero-initialized residual decoder improves score calibration but not
held-out ranking over the centroid control. This is a bounded negative result
for the tested training configuration, not evidence that cross-coordinate
information is absent or that learned latent methods are impossible.

## Evidence status and limitations

* **confirmed:** the classical numbers and RSLM4 are reproducible on the
  frozen 152-query shell with canonical candidate-receipt, input, runner, and
  model SHA bindings;
* **confirmed:** the hardened audit was run against the authoritative external
  classical, ML-sanity, teacher, candidate, vector, query and qrels artifacts;
  it independently recomputed each per-query primary metric before aggregating,
  and the committed PASS receipt is bound to that audit artifact;
* **confirmed:** a random-initialized linear AE32 reaches the PCA32
  reconstruction optimum on train and held-out vectors;
* **bounded negative:** the tested hierarchical/PQ/OPQ/RSLM controls do not
  preserve candidate FP32 ranking at the same level as direct INT8;
* **bounded negative:** the tested teacher decoder improves score-MSE but not
  held-out top-10 overlap over centroid;
* **unknown:** persistent side-code replay, native SIMD latency, page faults,
  MDBX behavior, and held-out domain quality.

## Follow-up

1. Keep direct INT8 as the current quality/storage control. Carry RSLM4 into a
   persistent code-only replay because its lower payload and nominal qrels
   result remain plausible, while treating its worst-query loss as a guardrail.
2. Keep the teacher decoder as a bounded score-regression control; a next ML
   attempt must target top-10/order loss or hard negatives inside the candidate
   shell rather than only reducing score MSE.
3. Only after a learned decoder beats centroid/ridge on held-out top-10
   overlap should it receive a native-kernel or storage benchmark.

Committed evidence:

* `2026-09-18-thq-r4-classical-gate.compact.json` and `.receipt.json`;
* `2026-09-18-thq-ml-sanity-gate.compact.json` and `.receipt.json`;
* `2026-09-18-thq-r4-teacher-diagnostics.compact.json` and `.receipt.json`;
* `2026-09-18-thq-r4-classical-ml-gates.audit.json`;
* `2026-09-18-thq-r4-classical-ml-gates.audit.receipt.json`.

The former prose-only record is superseded by these SHA-bound artifacts.
