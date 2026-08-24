# Float semantic IVF routing control

This external calibration-only control separates semantic coarse-clustering
quality from attempts to compress or accelerate centroid routing. It trains
spherical k-means only on frozen 25k train vectors and assigns evaluation
documents to those float centroids.

For each query, the first treatment scans every centroid by exact float inner
product. Selected lists feed the unchanged ITQ-256 Hamming@768, binary-ADC@256,
and E5 cascade. The result is a routing-quality control for later binary
centroid encoders, not a production Faiss dependency or native latency claim.

| scale | centroids | candidate targets |
| --- | ---: | --- |
| Spanish 100k | 1024, 4096 | 5%, 10%, 25% |
| Spanish 1M | 4096, 16384 | 5%, 10%, 25% |

Evidence must retain centroid bytes, assignments, per-query selected centroid
IDs, candidate and cascade shortlists, E5 oracle identity, per-query
contributions, and a deterministic replay archive. A binary-centroid surrogate
must use these frozen centroids and assignments, then report centroid-list
recall against this control and the same end-to-end cascade metrics.
