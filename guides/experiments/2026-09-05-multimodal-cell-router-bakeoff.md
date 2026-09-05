# Multimodal PCA12 cell-router bake-off

Date: 2026-09-05
Status: exploratory learned-routing study; not a product benchmark
Runner: `tools/agent-memory-bench/run-multimodal-cell-router-bakeoff.py`

## Question

The preceding teacher-derived multi-anchor oracle showed that a frozen PCA12
partition can retain nearly all useful documents when a query has several
routing modes.  This study tests whether those modes can be predicted from one
query embedding.  It compares three hypotheses:

1. a direct 4096-way multi-label cell head;
2. a hierarchical head over fixed 64/128 PCA-bit-prefix regions, followed by
   local lattice ordering;
3. a diagnostic kNN baseline that reuses cell sets from nearest training
   queries.

All policies use exact E5 FP32 scoring after routing and are compared at the
same 32k/64k/128k unique-document budgets.  The earlier teacher-medoid oracle
is an upper bound, not an input to these learned policies.

## Protocol

The DE-1M ordinal cache contains 153 train and 152 held-out queries (76
`config`, 76 `internal`).  Documents are assigned once to the same frozen
12-dimensional PCA/SVD partition with median cuts and `R=1` postings.

The direct model is `384 -> 128 -> 4096`, trained with multi-label BCE plus a
hard-negative ranking term.  The hierarchical models predict 64 or 128 groups
formed by PCA-bit prefixes; the exact local threshold-crossing cost orders
cells inside selected groups.  The kNN controls use cosine-nearest train
queries with `k=1/4/8` and transfer their teacher-cell sets.  Each learned
model is trained with seeds 13, 37, and 101 for 180 deterministic epochs.

Exact E5 scores for all held-out queries are computed as one document/query
matrix product.  Candidate budgets are enforced on unique documents; raw
postings and opened cells are retained as diagnostics.  Python p95 values are
directional only.

Cache manifest SHA-256:
`25f10151c60461edbfd0ac52e66caf5777834c80ba4637a8986e03de14f352ae`

Raw result SHA-256:
`91baa008847a2fc0c5ab24f1b19320d3c80937977a5beb7419fc6666d83bfae1`

The result contains 13,680 per-query rows and 72 aggregate summaries.

## Results at 64k unique candidates

Values are mean teacher top-10 overlap / held-out-qrels nDCG@10.  `p05/worst`
are query-level overlap tails.  Values combine the three model seeds where
applicable.

| Policy | Config overlap / nDCG | Internal overlap / nDCG | Config p05 / worst | Model bytes |
|---|---:|---:|---:|---:|
| kNN-1 | .732 / .560 | .712 / .619 | .300 / .000 | train-query payload |
| kNN-4 | .715 / .555 | .699 / .605 | .300 / .100 | train-query payload |
| kNN-8 | .670 / .547 | .676 / .602 | .300 / .100 | train-query payload |
| Direct 4096 top-32 | .712 / .551 | .701 / .616 | .300 / .100 | 2,310,656 |
| Direct 4096 top-64 | .684 / .529 | .668 / .602 | .300 / .100 | 2,310,656 |
| Direct 4096 top-128 | .501 / .451 | .486 / .477 | .100 / .000 | 2,310,656 |
| Hier-128 top-4 | .681 / .541 | .661 / .586 | .300 / .100 | 263,168 |
| Hier-128 top-8 | .453 / .364 | .422 / .379 | .100 / .000 | 263,168 |
| Hier-64 top-4 | .436 / .355 | .390 / .404 | .100 / .000 | 230,144 |

The directional Python p95 at 64k was approximately 19--23 ms for the rows in
this table (direct top-32: 20.6/21.8 ms config/internal; kNN-1: 20.4/22.6 ms;
hier-128 top-4: 19.8/22.7 ms).  At 128k it was approximately 45--53 ms.  These
numbers are useful only for relative harness sanity: they exclude native SIMD,
MDBX posting reads, and process-level warm-up controls.

For context, the teacher-derived M=8 oracle at the same budget was `.987/.648`
on config and `.983/.662` on internal.  Thus the learned routers recover only
part of the available multimodal routing capacity.

## Candidate-budget frontier

| Policy | 32k config / internal overlap | 128k config / internal overlap |
|---|---:|---:|
| kNN-1 | .580 / .572 | .861 / .854 |
| Direct 4096 top-32 | .512 / .525 | .860 / .845 |
| Direct 4096 top-64 | .381 / .330 | .849 / .830 |
| Direct 4096 top-128 | .292 / .229 | .815 / .810 |
| Hier-128 top-4 | .341 / .285 | .856 / .830 |
| Hier-64 top-4 | .270 / .223 | .818 / .805 |

At 32k, all learned variants retain substantial tail failures (worst overlap
zero).  At 128k, direct top-32 and kNN-1 approach the old single-anchor
ceiling, but remain well below the M=8 oracle.

## Interpretation

The oracle result does not translate automatically into a useful learned
router.  The direct multi-label head is best with a small top-32 seed set;
adding more predicted cells hurts because the logits are poorly calibrated and
the fixed budget then displaces useful local cells.  The hierarchical
bit-prefix grouping is weaker than direct prediction except for the 128-group,
top-4 setting, indicating that these fixed prefixes are not good semantic
meta-regions.  kNN-1 is the strongest simple control, suggesting that cell-set
modes have some local smoothness in E5 space, but it still does not close the
oracle gap.

The main bottleneck has therefore moved from partition capacity to
generalization and set prediction.  A 4096-way head with only 153 training
queries is data-starved; a continuous MLP with independently ordered anchors
would additionally suffer permutation ambiguity.  The result supports trying
set-aware objectives (Hungarian/set matching or gain-weighted cell ranking),
more training queries, and learned semantic meta-regions rather than widening
the frozen PCA partition.

The qrels nDCG values are the resulting exact-E5 retrieval quality, while
overlap measures survival of the exact-E5 teacher top-10.  They are not a full
R4 cascade measurement.  No local K8, codec rerank, MDBX I/O, or native SIMD
timing is included here.

## Limitations and follow-up

- One DE-1M split and only 153 training queries; no cross-corpus validation.
- kNN `model_bytes=0` in the report excludes the stored train-query vectors and
  their cell sets; those must be counted for a deployment comparison.
- Hierarchical regions are hand-defined PCA-bit prefixes, not learned regions.
- Exact teacher cells are used as labels, never as inference-time information.
- Python p95 excludes native posting access, model execution overhead, and I/O.

Next, train a genuine set-prediction router with a larger query sample and
compare against the single-anchor and M=8 oracle controls at fixed budgets.
Only a learned router that passes the routing gate should be replayed through
prototype/address dedup, local K8, and the full R4 cascade.
