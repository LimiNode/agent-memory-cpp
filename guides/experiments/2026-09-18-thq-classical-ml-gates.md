# THQ4 classical 152-query gate and ML sanity diagnostics

Date: 2026-09-18  
Context: `main` after merge #423 (`497f8f87a79651e3062ee2a14d635e5b32a65b4c`);
research branch `research/thq-classical-152`.

## Question

Does the frozen three-seed R4 candidate shell retain enough information for
classical residual codecs, and is the weak learned-decoder result caused by a
basic reconstruction/optimization failure rather than by the THQ4 feature
representation itself?

## Setup

The classical replay uses the frozen 152-query R4 stream (5000--5099 unique
candidate IDs per query), the 1M-row multilingual-E5 document vectors, the
detached 25k training vectors, and the production-shaped THQ4 materialization.
All model fitting is detached from qrels and teacher IDs.  The reference
runner is:

```text
tools/agent-memory-bench/run-thq-r4-classical-gate.py
```

The ML reconstruction control is:

```text
tools/agent-memory-bench/run-thq-ml-sanity-gate.py
```

Teacher-score train/held-out diagnostics use the first 120 query rows for
training and the remaining 32 rows for evaluation inside the same frozen
candidate shell:

```text
tools/agent-memory-bench/run-thq-r4-teacher-diagnostics.py
```

Raw outputs are retained outside Git in the worktree `tmp/` directory.  Their
input SHA-256 values and model hashes are recorded in each JSON receipt.

## Classical result

Teacher overlap is the overlap with the full FP32 teacher top-10.  The
`candidate-fp32` row is the ceiling when all frozen candidates may be reranked;
`thq4-fp32` is the stricter ceiling after THQ4 top-128 membership.

| arm | teacher overlap mean | p05 | min | candidate-FP32 top-10 overlap mean |
| --- | ---: | ---: | ---: | ---: |
| candidate FP32 ceiling | 0.9928 | 0.9000 | 0.9000 | 1.0000 |
| THQ4 → FP32 | 0.9928 | 0.9000 | 0.9000 | 1.0000 |
| direct INT8 linear | 0.9895 | 0.9000 | 0.9000 | 0.9967 |
| RSLM-like 3-bit | 0.9658 | 0.9000 | 0.8000 | 0.9711 |
| hierarchical residual 3-bit | 0.9454 | 0.8000 | 0.7000 | 0.9513 |
| RSLM-like 2-bit | 0.9329 | 0.8000 | 0.7000 | 0.9382 |
| hierarchical residual 2-bit | 0.9375 | 0.8000 | 0.7000 | 0.9414 |
| hierarchical residual 1-bit | 0.9020 | 0.8000 | 0.6000 | 0.9053 |
| PQ32×8 | 0.8822 | 0.7000 | 0.6000 | 0.8855 |
| OPQ32×4 | 0.8553 | 0.7000 | 0.6000 | 0.8566 |
| PCA32×8 | 0.8467 | 0.7000 | 0.5000 | 0.8493 |
| THQ4 centroid | 0.8368 | 0.6000 | 0.5000 | 0.8395 |

The THQ4→FP32 ceiling is equal to the all-candidate FP32 ceiling on this
stream.  Therefore the observed residual-codec loss is not explained by a
second top-128 rerank approximation; it is caused by the codec reconstruction
after the frozen candidate membership.  The result is a reference quality
gate, not a native latency benchmark.

## ML sanity result

Reconstruction MSE (train / 10k held-out document rows) was:

| control | train MSE | held-out MSE |
| --- | ---: | ---: |
| THQ4 centroid (zero residual) | 9.9909e-5 | 1.0038e-4 |
| PCA8 residual | 9.5652e-5 | 9.6179e-5 |
| PCA16 residual | 9.2532e-5 | 9.3240e-5 |
| PCA32 residual | 8.6941e-5 | 8.8062e-5 |
| linear AE32, random-init (64 epochs) | 8.7002e-5 | 8.8166e-5 |

The random-init linear bottleneck reaches the PCA32 optimum within `0.12%` on
held-out vectors.  The one-hot/THQ4 residual representation therefore passes
this basic learnability check without relying on PCA initialization.

## Teacher-score diagnostics

Scores and top-10 overlap are measured inside each query's frozen candidate
shell against exact FP32 candidate scores.  The decoder is a 1536→128→384
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

The zero-initialized residual decoder learns teacher-score structure: its
score-MSE is lower than both centroid and ridge on train and held-out shells.
However, its held-out top-10 overlap (`.866`) is not better than the centroid
control (`.872`), and the train ranking is also slightly lower.  Thus the
decoder objective captures score calibration without a demonstrated retrieval
gain.  This is evidence against the current decoder as a quality improvement,
not evidence that cross-coordinate information is absent or that learned
latent methods are impossible.

## Evidence status and limitations

* **confirmed:** the classical numbers above are reproducible on the frozen
  152-query candidate shell with input and model SHA bindings;
* **confirmed:** a PCA-initialized linear AE32 reaches the PCA reconstruction
  optimum on train and held-out vectors;
* **bounded negative:** the tested hierarchical/PQ/OPQ/RSLM controls do not
  preserve the candidate FP32 ranking at the same level as direct INT8;
* **investigation target:** the tested teacher decoder remains a failed
  training configuration, not a universal ML impossibility result;
* **not checked:** native SIMD latency, page faults, MDBX behaviour, and
  held-out domain quality.

## Follow-up

1. Keep direct INT8 as the current quality/storage control and do not promote
   residual codecs from this reference gate to production.
2. Keep the teacher decoder as a bounded score-regression control; a next ML
   attempt must target top-10/order loss or hard negatives inside the candidate
   shell rather than only reducing score MSE.
3. Only after a learned decoder beats the centroid/ridge controls on held-out
   top-10 overlap should it receive a native-kernel or storage benchmark.
