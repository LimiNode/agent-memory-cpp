# PCA12 routing follow-up synthesis

## Scope

This note closes the five requested follow-ups: diversity-aware centroids,
data scaling, weighted kNN, centroid-prior/Hungarian set prediction, and the
native full-cascade comparison.

## Routing-ceiling comparison

All Python studies use the frozen DE-1M PCA12 partition, `R=1`, exact E5 final
scoring, and therefore isolate routing rather than codec or storage effects.

| Method | 64k config overlap | 64k internal overlap | Finding |
| --- | ---: | ---: | --- |
| PCA threshold scheduler | ~.730 | ~.713 | historical control |
| Direct4096 top-32 | ~.712 | ~.701 | more data helps config only |
| PCA centroid K=1 | ~.779 | ~.733 | strong simple control |
| E5 centroid K=8 | ~.815 | ~.796 | best simple multimodal control |
| diversity/MMR centroid | ~.658 best listed run | ~.612 | seed-only MMR is weak |
| weighted kNN vote | ~.361 | ~.293 | negative |
| Hungarian centroid hybrid | ~.794 | ~.769 | useful diagnostic, not clean win |
| teacher M=8 oracle | ~.987 | ~.983 | routing ceiling only |

The apparent difference between E5-centroid K=8 and the Hungarian hybrid is
not a contradiction: the former is a direct document-side multimodal control,
whereas the latter predicts eight continuous anchors and then still relies on a
single centroid-prior budget fill.

## Native full-cascade evidence

The route-integrated variants above were not materialized into the native R4
benchmark in this batch.  The correct native reference remains the frozen
2026-08-31 end-to-end study:

`guides/experiments/2026-08-31-neuroute-r4-native-end-to-end.md`

It executes the complete `5000 -> 768 -> 64 -> 10` cascade and reports, on the
frozen DE-1M matrix, p95 latency of 63.008 ms for seek/decode/scalar,
17.452 ms for strict mmap/fused/batched, and 10.423 ms for the AVX2 treatment,
with identical mean nDCG@10 (`.650652`) across the three paths.  Those are
native implementation results for the historical frozen router; they are not
quality numbers for the new centroid/Hungarian routes.

Consequently no new route is promoted to production from these Python ceilings.
The correct next native experiment is to materialize the E5-centroid K=1/K=4/8
postings (and, separately, Direct4096) into the same R4 harness, then compare
candidate count, bytes read, p95/p99, and final qrels nDCG.  Until that replay,
the practical choices are:

* **quality/latency control:** E5 centroid K=4 or K=8, if the extra centroid
  metadata and candidate budget are acceptable;
* **small/simple baseline:** PCA centroid K=1;
* **research only:** Direct4096, weighted-kNN, MMR seed prepending, and the
  current Hungarian hybrid.

## Final conclusion

The fixed PCA-prefix hierarchy is closed as the main research path.  A cheap
centroid lookup already dominates the learned and kNN alternatives tested here,
while the teacher oracle shows that the remaining gap is multimodal routing,
not merely threshold calibration.  The next decision must be made on the
native full cascade, with route quality and physical cost measured together.
