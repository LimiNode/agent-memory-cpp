# PCA12 bucket-centroid bake-off

Date: 2026-09-05
Status: exploratory centroid-routing study; not a product benchmark
Runner: `tools/agent-memory-bench/run-pca12-bucket-centroid-bakeoff.py`

## Question

The multi-anchor oracle showed that useful documents can occupy several frozen
PCA12 buckets for one query.  Before relying on a learned multimodal router,
this study tests whether the same effect can be obtained with offline bucket
representatives:

```text
query E5/PCA -> compare against 4096 precomputed bucket centroids -> cells
```

Both PCA-space and original E5-space centroids are measured.  Each bucket is
also split into deterministic `K=2/4/8` PCA12 Lloyd subclusters; the query
scores the best subcentroid per bucket.  The existing threshold scheduler is a
direct control.

## Protocol

The replay uses the same DE-1M ordinal cache and frozen partition as the
multi-anchor oracle: 76 config and 76 internal queries, 12D SVD/PCA fitted on
`documents[::4]`, median cuts, and one posting per document.  Candidate budgets
are exact unique-document budgets of 32k, 64k, and 128k.  Final ranking is exact
E5 FP32; overlap is survival of the exact E5 teacher top-10 and qrels nDCG is
reported separately.

For `K>1`, five Lloyd iterations are run independently inside every bucket in
PCA12 coordinates.  The resulting assignments are used to average both PCA
and E5 vectors.  Empty clusters fall back to their nearest bucket point, so no
NaN representatives are emitted.

Cache manifest SHA-256:
`25f10151c60461edbfd0ac52e66caf5777834c80ba4637a8986e03de14f352ae`

Raw result SHA-256:
`47abb09973413a02c0281354255995b768b8405587eab0bc110adc1eadfc4c6f`

The result contains 4,104 per-query rows and 54 aggregate summaries.

## Results at 64k unique candidates

Values are mean teacher top-10 overlap / qrels nDCG@10 for config and internal
queries.  Storage is centroid payload only; it excludes postings.

| Policy | Config overlap / nDCG | Internal overlap / nDCG | Storage | Query dot products |
|---|---:|---:|---:|---:|
| PCA threshold scheduler | .730 / .548 | .713 / .615 | 19.5 KiB | 49,152 |
| PCA centroid K=1 | .779 / .586 | .733 / .625 | 192 KiB | 49,152 |
| PCA centroid K=2 | .772 / .598 | .757 / .623 | 384 KiB | 98,304 |
| PCA centroid K=4 | .771 / .584 | .742 / .614 | 768 KiB | 196,608 |
| PCA centroid K=8 | .765 / .576 | .757 / .620 | 1.5 MiB | 393,216 |
| E5 centroid K=1 | .779 / .585 | .730 / .601 | 6.0 MiB | 1,572,864 |
| E5 centroid K=2 | .793 / .590 | .763 / .576 | 12.0 MiB | 3,145,728 |
| E5 centroid K=4 | .804 / .595 | .770 / .610 | 24.0 MiB | 6,291,456 |
| E5 centroid K=8 | .815 / .611 | .796 / .612 | 48.0 MiB | 12,582,912 |

At the same budget, the teacher-derived M=8 oracle was approximately
`.987/.648` config and `.983/.662` internal.  The learned direct multi-label
router's best row (top-32) was `.712/.551` and `.701/.616` respectively.

## Candidate-budget frontier

| Policy | 32k config / internal overlap | 128k config / internal overlap |
|---|---:|---:|
| PCA threshold scheduler | .582 / .570 | .863 / .855 |
| PCA centroid K=1 | .620 / .592 | .886 / .862 |
| PCA centroid K=4 | .633 / .626 | .892 / .874 |
| PCA centroid K=8 | .638 / .609 | .895 / .874 |
| E5 centroid K=1 | .657 / .611 | .882 / .845 |
| E5 centroid K=4 | .696 / .654 | .901 / .862 |
| E5 centroid K=8 | .713 / .690 | .924 / .879 |

The E5 K=8 representative gives the best centroid overlap on config at all
three budgets and reaches `.924` at 128k.  PCA centroids are much smaller and
have lower query work; their K=8 variant reaches `.895/.874` at 128k.

## Interpretation

Precomputed centroids are a real, useful baseline: even K=1 improves over the
single-anchor threshold scheduler at 64k, and E5 subcentroids improve further
to `.815/.796` at K=8.  This confirms that direct similarity to bucket
representatives can recover some multimodal routing without a neural network.

However, centroids do not approach the teacher-derived M=8 ceiling.  The gap is
not merely a choice of one versus several representatives: E5 K=8 stores 48 MiB
of representatives and performs 12.6M dot products per query, yet remains well
below `.98` overlap at 64k.  The centroid ranking is therefore a useful
lightweight control and possible fallback, not evidence that learned set
prediction is unnecessary.

PCA-centroid K=1 is especially attractive on the quality/weight axis: 192 KiB
of representatives and almost the same query arithmetic as the threshold
control, with a clear overlap gain.  E5-centroid K=4/8 trades tens of MiB and
millions of dot products for higher recall.  The reported end-to-end Python p95
(roughly 19--21 ms at 64k and 41--46 ms at 128k) includes candidate formation
and exact E5 scoring, so it is not a centroid-kernel benchmark.

## Limitations and follow-up

- One DE-1M split; no cross-corpus validation or native SIMD benchmark.
- K-means is a deterministic small Lloyd fit, not an optimized production
  codebook trainer.
- Centroid storage excludes postings and metadata; E5 K=1 is 6.0 MiB and K=8
  is 48.0 MiB for 4096 buckets.
- This is still a routing-ceiling replay: no local K8, codec reranking, MDBX
  I/O, or full R4 cascade.

The next comparison should put PCA-centroid K=1, E5-centroid K=4/8, direct
multi-label top-32, and a learned set-prediction router under the same native
candidate-access harness.  Only then should a winner be promoted into the
full cascade.
