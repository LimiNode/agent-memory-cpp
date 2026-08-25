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
| HNSW | external hnswlib FP32 graph then exact float rerank | `M={8,16}`, `efSearch={16,32,64,128}` |

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
Repeated warm routing measurements establish a local performance frontier, not
cross-platform latency or retrieval quality after the document cascade.
