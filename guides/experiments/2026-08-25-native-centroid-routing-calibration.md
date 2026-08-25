# Native semantic-centroid routing calibration

## 2026-08-25 predeclared protocol

Question: is routing to the frozen 1,024--16,384 semantic-IVF centroids
actually expensive in the native C++ read path, and which compact surrogate is
competitive at the same document-candidate mass?

The experiment uses only the frozen Spanish calibration roots and float
semantic-IVF centroids/assignments. It separately times warm in-memory native
routing; E5 query encoding, document cascade, index build, and process-wide
memory are excluded. The FP32 exhaustive centroid scan is the exact routing
oracle. Every approximate treatment is compared after selecting float-reranked
centroid lists until the same 5%, 10%, or 25% document mass is reached.

| treatment | storage/routing change | quality control |
| --- | --- | --- |
| FP32 scan | exact control | 100% centroid-order recall |
| FP16 | binary16 centroid payload, FP32 accumulation | matched-mass teacher overlap |
| int8 | symmetric per-centroid int8 payload and scale | matched-mass teacher overlap |
| symmetric binary | 512-bit Rademacher Hamming shortlist then exact float rerank | 2x/4x shortlist |
| asymmetric binary | same stored 512-bit codes, continuous query projection score then exact float rerank | 2x/4x shortlist |
| HNSW | external hnswlib FP32 graph then exact float rerank | `M={8,16}`, `efSearch={128,512,2048,4096}` |

For the binary arms, the exact FP32 rerank shortlist is
`ceil(multiplier * target_fraction * centroid_count)`. HNSW has no multiplier:
it returns `ceil(target_fraction * centroid_count)` centroids and reranks them
in exact FP32. This makes feasibility explicit instead of silently asking an
`efSearch` of 16--128 to produce thousands of centroids. The runner uses one
native routing thread and must mark a row infeasible when an HNSW search cannot
produce its declared shortlist.

The runner must preserve raw timing samples, centroid-payload bytes,
backend-specific index bytes, feasibility, selected-centroid recall, and
teacher candidate-document overlap. No row may select a runtime backend or
open confirmation data. A native result can only decide whether another learned
router experiment is worth its complexity.

Expected outcomes:

- if FP32 scanning 4k/16k centroids is already comfortably cheap, binary and
  HNSW routing have no presumed production justification;
- if FP16 or int8 is near-exact and materially cheaper, it is a simpler
  challenger than learned binary routing;
- if asymmetric binary improves symmetric Hamming at the same shortlist, it
  motivates a compact-routing line but not a learned encoder by itself;
- HNSW is only a graph-cost/latency control, not a default dependency.

Limitations: this is one hardware environment and calibration-only data.
Repeated warm single-thread routing measurements establish a local performance frontier, not
cross-platform latency or retrieval quality after the document cascade.

### Pre-measurement protocol clarification

The initial draft used `efSearch={16,32,64,128}` and treated every
`then_exact_fp32_rerank` treatment as if it had a binary shortlist multiplier.
That made the HNSW arm unable to provide the required centroid-list mass for
the larger configurations. Before any row of the matched-mass matrix was run,
the protocol was corrected to the explicit binary/HNSW rules above and to the
feasible `efSearch={128,512,2048,4096}` grid. The separately run C++ scan
preflight is diagnostic-only and is not a row of this matrix.
