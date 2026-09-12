# THQ index wave 2: IMI and SPANN-like posting controls

Date: 2026-09-12  
Fixture: frozen `thq-full-scan-v2` (1,000,000 documents, THQ4-384).  This is
an in-memory coarse-posting oracle; no R4 mapping, MDBX backend, or production
activation is claimed.

The runner uses two deterministic controls over the first six decoded THQ
coordinates.  The SPANN-like control has 64 exact three-level signature
postings.  The IMI control splits the signature into two independent
three-coordinate spaces, producing a 64×64 Cartesian cell grid.  For each
query, cells/postings are ordered by additive ordinal distance and the top
`L ∈ {1,2,4,8,16,32}` are unioned.  Metrics are posting/cell count, candidate
documents, and teacher recall; payload reranking is intentionally not run.

Eight-query smoke means were:

| L | SPANN-like candidates | SPANN-like teacher recall | IMI candidates | IMI teacher recall |
|---:|---:|---:|---:|---:|
| 1 | 15,705 | 0.1500 | 416 | 0.0000 |
| 2 | 30,382 | 0.1875 | 1,302 | 0.0000 |
| 4 | 62,249 | 0.2625 | 2,145 | 0.0125 |
| 8 | 121,662 | 0.3250 | 3,939 | 0.0125 |
| 16 | 245,678 | 0.5375 | 11,191 | 0.0125 |
| 32 | 493,298 | 0.7500 | 18,484 | 0.0375 |

The SPANN-like surrogate shows a recall/work trade-off but misses the target
of ≥0.995 recall at ≤50k candidates.  The IMI Cartesian partition is highly
sparse/misaligned for this multi-modal teacher set and is negative in this
configuration.  Neither result rules out a learned K8/R4 coarse partition,
balanced replication, or a different subspace design; they only characterize
these fixed signature controls.  A real SPANN/R4 follow-up must assign by
verified semantic prototypes, support optional 2–4-way replication, and then
rerank candidates with exact THQ-ADC before any storage materialization.

Receipt: `2026-09-12-thq-index-wave2-result.json`.  It records frozen fixture
and runner hashes, protocol, smoke status, and `production_activation: false`.
