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

The route-integrated document cascade has now been replayed natively (in-memory
postings, not MDBX) with the same Hamming@768 → ADC@64 → exact@10 downstream
contract.  Full details are in [the native bake-off note](2026-09-06-native-document-routing-bakeoff.md).
At 64k, final overlap/nDCG were `.668/.565` for PCA threshold, `.660/.551` for
E5 K=4, `.738/.596` for E5 K=8, and `.730/.638` for Direct4096.  The latter
numbers are the composed-cascade result and replace any interpretation based
only on the earlier routing ceiling.

The correct MDBX/R4 reference remains the frozen 2026-08-31 end-to-end study:

`guides/experiments/2026-08-31-neuroute-r4-native-end-to-end.md`

It executes the complete `5000 -> 768 -> 64 -> 10` cascade and reports, on the
frozen DE-1M matrix, p95 latency of 63.008 ms for seek/decode/scalar,
17.452 ms for strict mmap/fused/batched, and 10.423 ms for the AVX2 treatment,
with identical mean nDCG@10 (`.650652`) across the three paths.  Those are
native implementation results for the historical frozen router; they are not
quality numbers for the new centroid/Hungarian routes.

The remaining gap is storage integration: materialize the same cell postings in
MDBX/R4 and compare candidate count, bytes read, p95/p99, and final qrels nDCG.
Until that replay, the practical choices are:

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

## Review update and decision protocol

The negative MMR result is specific to seed prepending: it does not show that
query-conditioned multimodality is impossible.  It shows that a few diverse
seeds cannot overcome the subsequent ranked-cell stream under a fixed budget.
Likewise, the weighted-kNN result closes the weighted-vote formulation, not the
historical single-neighbour cell-set transfer mechanism.  Neither distinction
changes the implementation priority.

The large teacher-oracle gap must also remain correctly scoped.  The oracle
proves that the PCA12 partition contains useful regions; it does not prove that
there is a cheap, generalising function from a query to those regions.  The
current four learned or vote-based attempts did not find such a function.

The next experiment is therefore a narrow native bake-off with one identical
downstream cascade and two operating budgets:

```text
A  corrected PCA threshold scheduler
B  PCA centroid K=1
C  E5 centroid K=1
D  E5 centroid K=2
E  E5 centroid K=4
F  E5 centroid K=8
G  Direct4096 top-32 (latency control)
```

Run every route at 32k and 64k candidates; retain 128k only as a diagnostic.
Record router p50/p95, cell scoring, postings/MDBX access, raw and unique
candidates, bytes read, THQ/Hamming, ADC/INT4, exact rerank, total p50/p95/p99,
final qrels nDCG@10, and index/model memory.  The Direct4096 row requires a
small native MLP inference adapter (or an explicitly documented precomputed
control); MMR, weighted-kNN, and the current Hungarian hybrid should not be
ported unless this matrix reveals a Pareto point they can plausibly beat.

Decision rule:

* choose the cheapest route whose full-cascade qrels nDCG is within the agreed
  quality tolerance of the best row;
* if E5 K4/K8 adds quality but loses its latency budget, retain PCA K1 as the
  production control and E5 K4/K8 as optional quality profiles;
* if all centroid routes lose after downstream reranking, stop the PCA12 branch
  rather than trying to close the oracle gap with more Python router variants.
