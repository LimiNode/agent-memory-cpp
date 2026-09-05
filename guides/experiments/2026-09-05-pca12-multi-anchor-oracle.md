# PCA12 multi-anchor routing oracle

Date: 2026-09-05
Status: exploratory routing-ceiling study; not a product benchmark
Runner: `tools/agent-memory-bench/run-pca12-multi-anchor-oracle.py`

## Question

Does the corrected frozen PCA12 document partition have enough capacity to retain the teacher top-10, or is the loss caused mainly by forcing every query through one routing anchor?  The experiment compares a single anchor with teacher-derived multi-anchor routing and with an oracle that selects useful teacher cells directly.

## Frozen protocol

- Input: the existing DE-1M ordinal-lattice cache (`manifest.json`), with 76 `config` and 76 `internal` evaluation queries (152 total).
- Documents are assigned once to a 12-dimensional PCA partition.  PCA is fitted on `documents[::4]` with the shared SVD initializer; each coordinate uses its frozen median threshold.  Documents have one posting (`replication = 1`).
- Candidate budgets are exact unique-document budgets of 32,000, 64,000, and 128,000.  Cell proposals stop when the budget is reached; raw postings and unique candidates are reported separately.
- `single`: one query anchor and its exact threshold-crossing cell order.
- `multi_anchor_equal`: M medoid anchors (M = 2, 4, 8), with round-robin cell proposals.
- `multi_anchor_adaptive`: the same anchors, merged by their exact crossing costs.
- Anchor points are an oracle: exact medoids of the query's teacher top-10 projected documents.  They are not predicted by a model.
- `optimal`: `optimal_teacher_cells_plus_single_fill`; all subsets of the positive teacher cells (at most ten) are enumerated, maximizing teacher-cell recovery under the budget, then the normal single-anchor scheduler fills the remaining budget.  This is an exact useful-cell oracle, not a global optimizer over every partition cell.
- Final ranking uses exact E5 FP32 scores over the routed candidate documents.  `overlap` is teacher top-10 survival; `qrels nDCG` uses the held-out qrels.  `p05/worst` are the 5th percentile and minimum query-level overlap.  `proposal dup` is duplicate cell proposals, not document duplication (R=1 gives zero document duplication).

The replay output contains 3,648 per-query rows and 48 aggregate summaries.  Output SHA-256:

`c42c164d39deae653352d9133fffd56414d3dbf9f6f1e456358a459e7188097e`

## Aggregate results: config queries

Values are mean overlap, mean qrels nDCG@10, overlap p05/min, mean opened cells, mean recovered teacher cells, duplicate proposal ratio, and Python p95 route time (ms).

### 32k unique candidates

| policy | M | overlap | qrels nDCG | p05 / worst | cells | teacher cells | proposal dup | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1 | 0.582 | 0.495 | 0.200 / 0.000 | 126.4 | 4.66 | 0.000 | 2.65 |
| equal | 2 | 0.755 | 0.581 | 0.475 / 0.300 | 108.1 | 6.37 | 0.249 | 4.97 |
| equal | 4 | 0.855 | 0.607 | 0.600 / 0.600 | 104.4 | 7.36 | 0.395 | 4.79 |
| equal | 8 | 0.967 | 0.638 | 0.900 / 0.800 | 101.9 | 8.45 | 0.564 | 7.87 |
| adaptive | 2 | 0.743 | 0.568 | 0.400 / 0.300 | 108.7 | 6.25 | 0.219 | 8.91 |
| adaptive | 4 | 0.849 | 0.604 | 0.600 / 0.500 | 106.6 | 7.29 | 0.388 | 18.09 |
| adaptive | 8 | 0.971 | 0.638 | 0.900 / 0.800 | 103.8 | 8.49 | 0.539 | 19.92 |
| optimal | — | 1.000 | 0.647 | 1.000 / 1.000 | 124.8 | 8.78 | 0.000 | 2.20 |

### 64k unique candidates

| policy | M | overlap | qrels nDCG | p05 / worst | cells | teacher cells | proposal dup | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1 | 0.730 | 0.548 | 0.300 / 0.000 | 258.2 | 6.11 | 0.000 | 2.64 |
| equal | 2 | 0.858 | 0.604 | 0.600 / 0.400 | 229.6 | 7.36 | 0.241 | 5.13 |
| equal | 4 | 0.916 | 0.615 | 0.800 / 0.700 | 222.7 | 7.93 | 0.441 | 8.45 |
| equal | 8 | 0.987 | 0.648 | 0.900 / 0.900 | 216.4 | 8.64 | 0.584 | 13.84 |
| adaptive | 2 | 0.850 | 0.604 | 0.600 / 0.500 | 231.1 | 7.29 | 0.215 | 6.26 |
| adaptive | 4 | 0.917 | 0.624 | 0.800 / 0.600 | 225.4 | 7.95 | 0.406 | 18.18 |
| adaptive | 8 | 0.989 | 0.648 | 0.900 / 0.900 | 219.1 | 8.67 | 0.531 | 11.74 |
| optimal | — | 1.000 | 0.647 | 1.000 / 1.000 | 257.0 | 8.78 | 0.000 | 2.61 |

### 128k unique candidates

| policy | M | overlap | qrels nDCG | p05 / worst | cells | teacher cells | proposal dup | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1 | 0.863 | 0.607 | 0.500 / 0.400 | 529.6 | 7.41 | 0.000 | 2.70 |
| equal | 2 | 0.938 | 0.626 | 0.775 / 0.600 | 501.2 | 8.16 | 0.279 | 5.19 |
| equal | 4 | 0.966 | 0.628 | 0.800 / 0.800 | 484.2 | 8.43 | 0.467 | 8.16 |
| equal | 8 | 0.996 | 0.648 | 1.000 / 0.900 | 476.7 | 8.74 | 0.658 | 14.95 |
| adaptive | 2 | 0.930 | 0.624 | 0.775 / 0.600 | 501.1 | 8.08 | 0.271 | 9.48 |
| adaptive | 4 | 0.964 | 0.630 | 0.800 / 0.700 | 487.3 | 8.42 | 0.419 | 18.48 |
| adaptive | 8 | 0.997 | 0.648 | 1.000 / 0.900 | 479.9 | 8.75 | 0.584 | 36.96 |
| optimal | — | 1.000 | 0.647 | 1.000 / 1.000 | 528.7 | 8.78 | 0.000 | 2.66 |

## Aggregate results: internal queries

### 32k unique candidates

| policy | M | overlap | qrels nDCG | p05 / worst | cells | teacher cells | proposal dup | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1 | 0.570 | 0.551 | 0.200 / 0.000 | 130.9 | 4.82 | 0.000 | 2.66 |
| equal | 2 | 0.763 | 0.565 | 0.475 / 0.300 | 109.3 | 6.61 | 0.242 | 4.71 |
| equal | 4 | 0.828 | 0.577 | 0.500 / 0.500 | 105.4 | 7.22 | 0.422 | 7.97 |
| equal | 8 | 0.963 | 0.652 | 0.875 / 0.800 | 100.0 | 8.57 | 0.570 | 13.72 |
| adaptive | 2 | 0.759 | 0.572 | 0.400 / 0.300 | 109.9 | 6.57 | 0.204 | 5.95 |
| adaptive | 4 | 0.838 | 0.571 | 0.600 / 0.500 | 108.1 | 7.33 | 0.365 | 12.12 |
| adaptive | 8 | 0.967 | 0.652 | 0.900 / 0.800 | 101.2 | 8.61 | 0.522 | 11.68 |
| optimal | — | 1.000 | 0.661 | 1.000 / 1.000 | 127.7 | 8.93 | 0.000 | 2.52 |

### 64k unique candidates

| policy | M | overlap | qrels nDCG | p05 / worst | cells | teacher cells | proposal dup | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1 | 0.713 | 0.615 | 0.300 / 0.100 | 259.1 | 6.16 | 0.000 | 2.57 |
| equal | 2 | 0.862 | 0.621 | 0.500 / 0.500 | 233.1 | 7.58 | 0.235 | 5.09 |
| equal | 4 | 0.932 | 0.621 | 0.800 / 0.600 | 224.5 | 8.25 | 0.447 | 8.19 |
| equal | 8 | 0.983 | 0.662 | 0.900 / 0.800 | 216.4 | 8.76 | 0.601 | 13.63 |
| adaptive | 2 | 0.847 | 0.610 | 0.600 / 0.500 | 233.2 | 7.43 | 0.229 | 9.09 |
| adaptive | 4 | 0.921 | 0.629 | 0.700 / 0.600 | 226.6 | 8.16 | 0.388 | 18.08 |
| adaptive | 8 | 0.988 | 0.661 | 0.900 / 0.900 | 220.6 | 8.82 | 0.568 | 37.51 |
| optimal | — | 1.000 | 0.661 | 1.000 / 1.000 | 257.3 | 8.93 | 0.000 | 2.63 |

### 128k unique candidates

| policy | M | overlap | qrels nDCG | p05 / worst | cells | teacher cells | proposal dup | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1 | 0.855 | 0.652 | 0.500 / 0.400 | 523.0 | 7.54 | 0.000 | 2.88 |
| equal | 2 | 0.937 | 0.647 | 0.775 / 0.600 | 493.4 | 8.30 | 0.278 | 5.30 |
| equal | 4 | 0.974 | 0.651 | 0.900 / 0.800 | 486.3 | 8.67 | 0.458 | 8.23 |
| equal | 8 | 0.995 | 0.661 | 0.975 / 0.900 | 476.6 | 8.88 | 0.606 | 13.81 |
| adaptive | 2 | 0.928 | 0.651 | 0.700 / 0.600 | 497.2 | 8.21 | 0.267 | 9.34 |
| adaptive | 4 | 0.974 | 0.654 | 0.900 / 0.800 | 488.0 | 8.67 | 0.432 | 18.50 |
| adaptive | 8 | 0.997 | 0.661 | 1.000 / 0.900 | 478.7 | 8.91 | 0.585 | 37.17 |
| optimal | — | 1.000 | 0.661 | 1.000 / 1.000 | 522.6 | 8.93 | 0.000 | 2.71 |

## Interpretation

The partition is not the limiting factor in this routing-ceiling test.  The optimal teacher-cell oracle recovers every teacher top-10 at all three budgets, while the one-anchor policy is substantially worse (config overlap 0.582/0.730/0.863 at 32k/64k/128k; internal 0.570/0.713/0.855).  A small set of teacher-derived anchors closes most of that gap: M=8 reaches 0.967/0.987/0.996 on config and 0.963/0.983/0.995 on internal for the equal scheduler.  Adaptive merging is similar in quality, but its Python p95 is noisier and often higher.

The relevant documents are therefore multimodal across PCA12 cells.  A single query point and its nearest threshold-crossing order cannot express those modes; the observed loss is primarily a single-anchor routing limitation, not evidence that 12 bits cannot partition the corpus.  The near-perfect oracle is an upper bound: its anchors and selected cells use the exact teacher top-10 and cannot be deployed as-is.

The qrels nDCG ceiling is lower than overlap 1.0 because this is a routing-ceiling replay with exact final E5 scoring, not a complete production cascade.  `worst_qrels_ndcg` is zero in the aggregate summaries for queries with no matching held-out qrels and should not be read as a codec result.  Timings are Python directional measurements and exclude native index, I/O, SIMD, and model inference costs.  The study does not measure index bytes or add/delete behavior.

## Consequence for the next experiment

Do not spend the next iteration only widening or retuning the single-anchor loss.  Train and evaluate a learned multi-anchor query router (for example, predict 2/4/8 anchor points or cell logits), using the same frozen PCA12 partition and the same exact candidate budgets.  Compare it against the single-anchor baseline and this oracle, then replay the surviving addresses through the downstream local-K8/R4 cascade.  Keep the oracle explicitly diagnostic; no product-quality claim follows from this experiment.
