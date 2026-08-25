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
| HNSW | external hnswlib FP32 graph then exact float rerank | `M={8,16}`, `efSearch={256,512,2048,8192}` |

For the binary arms, the exact FP32 rerank shortlist is
`ceil(multiplier * target_fraction * centroid_count)`. HNSW uses a fixed 2x
mass shortlist before exact FP32 rerank. This accounts for differing inverted
list sizes: a 1x centroid count can fall just short of the requested document
mass despite returning exactly the requested number of centroids. The runner
uses one native routing thread and must mark a row infeasible when an HNSW
search cannot produce its declared shortlist.

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

The initial draft used `efSearch={16,32,64,128}` and left the HNSW shortlist
mass ambiguous. That made the HNSW arm unable to provide the required
centroid-list mass for the larger configurations. The diagnostic smoke run also
showed that an exactly 1x centroid shortlist occasionally falls short because
the frozen inverted lists are not equal-sized. Before any row of the
matched-mass matrix was run, the protocol was corrected to the explicit 2x
HNSW rule above and to the feasible `efSearch={256,512,2048,8192}` grid. The
separately run C++ scan preflight and smoke run are diagnostic-only and are not
rows of this matrix.

## 2026-08-25 calibration result

The completed matrix contains 180 predeclared rows: four frozen
`(scale, centroid_count)` materializations, three document-mass targets, and
the declared FP32/FP16/int8/binary/HNSW controls. Each feasible row has two
warmups and seven retained warm in-memory timing samples. The fail-closed
evidence archive was reproduced twice byte-for-byte locally:

```text
native-centroid-routing-v1-evidence.zip
SHA-256 e71d0be96597f120ef3b3c75c82f4fa008837a3a2c1cebe12181921cc2d69f76
```

The compact frontier below uses the 5% document-candidate target, where
sublinear routing is most useful. `overlap` is candidate-document overlap with
the exact FP32 centroid-order teacher after the declared list-mass selection.

| frozen materialization | treatment | p50 ms/query | overlap |
| --- | --- | ---: | ---: |
| es-100k / K=1024 | exact FP32 scan | 0.482 | 1.0000 |
| es-100k / K=1024 | HNSW M16, ef256 | 0.247 | 1.0000 |
| es-100k / K=4096 | exact FP32 scan | 1.975 | 1.0000 |
| es-100k / K=4096 | HNSW M16, ef512 | 0.814 | 0.9992 |
| es-1m / K=4096 | exact FP32 scan | 1.972 | 1.0000 |
| es-1m / K=4096 | HNSW M16, ef512 | 0.807 | 0.9994 |
| es-1m / K=16384 | exact FP32 scan | 8.236 | 1.0000 |
| es-1m / K=16384 | HNSW M8, ef2048 | 3.401 | 0.9992 |

The simple payload controls do not provide a better native frontier. Per-
centroid int8 preserved roughly 98.3--99.3% teacher document overlap, but was
slightly slower than the compiler-vectorized FP32 scan. The scalar software
FP16 conversion in this diagnostic is much slower; it is not evidence against
a future platform-specific hardware-FP16 implementation. The static 512-bit
Rademacher routes were also slower than FP32: at K=16384 their 5% p50 values
were 11.3--12.7 ms for symmetric Hamming and 47.2--48.2 ms for asymmetric
sign-dot, even before a production document cascade.

The HNSW feasibility flags are evidence, not failures of the runner. At K1024,
`efSearch=256` cannot return the declared 512-centroid 2x shortlist for the
25% row. At K4096 and K16384, lower-ef configurations are likewise marked
infeasible whenever their required shortlist is larger than `efSearch`. They
are retained so the archive records the boundary rather than silently
substituting a different candidate mass.

Interpretation: for these frozen semantic centroids, native exhaustive FP32
routing remains a strong and simple baseline through K4096. External FP32
HNSW is a viable latency control at larger K, but remains research-only and is
not a library dependency or a production selection. This experiment rejects
the specific static 512-bit Rademacher surrogate as a useful replacement for
the centroid scan. It does not reject a supervised or hierarchical semantic
router: those must be trained/evaluated on a separate calibration partition
against the same matched-mass contract.

Limitations: all timing is one-machine, single-native-thread, warm-routing
diagnostic evidence; it excludes E5 query encoding, HNSW build time, document
cascade work, memory high-water marks, and cross-platform measurements. The
matrix is calibration-only and cannot select a production configuration or
claim end-to-end retrieval quality.

Next check: predeclare a held-out protocol for a hierarchical semantic-centroid
router (or a small supervised classifier) that reproduces the frozen centroid
partition. Compare it with FP32 scan and the external HNSW control at identical
candidate-document mass; do not continue the static Rademacher binary line.
