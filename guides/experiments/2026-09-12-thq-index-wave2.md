# THQ index wave 2: IMI and SPANN-like posting controls

Date: 2026-09-12. Fixture: frozen `thq-full-scan-v2` (1,000,000 documents,
THQ4-384). This is an in-memory coarse-posting oracle; no R4 mapping, MDBX
backend, or production activation is claimed.

The runner uses the first six decoded THQ coordinates. The SPANN-like control
has 64 exact three-coordinate, four-level THQ signature postings. The IMI
control splits them into two disjoint three-coordinate, four-level THQ
subspaces, producing a 64x64 Cartesian grid. Cell IDs are composed in int32
with fail-closed range assertions; an earlier uint8-overflow receipt is
superseded by the corrected replay. Payload reranking is not run.

Corrected eight-query smoke means:

| L | SPANN-like candidates | SPANN-like teacher recall | IMI candidates | IMI teacher recall |
|---:|---:|---:|---:|---:|
| 1 | 15,705 | 0.1500 | 216 | 0.0125 |
| 2 | 30,382 | 0.1875 | 407 | 0.0125 |
| 4 | 62,249 | 0.2625 | 861 | 0.0250 |
| 8 | 121,662 | 0.3250 | 1,703 | 0.0375 |
| 16 | 245,678 | 0.5375 | 3,322 | 0.0375 |
| 32 | 493,298 | 0.7500 | 6,845 | 0.1000 |
| 64 | 1,000,000 | 1.0000 | 13,730 | 0.1250 |
| 128 | 1,000,000 | 1.0000 | 28,056 | 0.1500 |
| 256 | 1,000,000 | 1.0000 | 56,892 | 0.2125 |

Occupancy confirms that all 4,096 IMI cells are populated (p50 232 and p95
395 documents; effective cells 3,877.9).  The SPANN postings are comparatively
balanced (min 8,856, p50 15,458, p95 21,024, max 25,631; effective cells
62.68).  Thus the previous sparse/overflow interpretation was invalid, but the
corrected raw-coordinate IMI still has very poor teacher recall.  Among tested
SPANN points below 50k mean candidates, the best recall is 0.1875 at L=2; the
next point is already ~62k candidates with recall 0.2625. The IMI control remains
below 0.25 recall through 56.9k candidates (L=256). These findings are
specific to the first six raw THQ coordinates and do not establish that IMI is
intrinsically sparse or that SPANN fails with semantic prototypes.

Receipt: `2026-09-12-thq-index-wave2-result.json`. It records the corrected
fixture/runner hashes, occupancy diagnostics, smoke status, and
`production_activation: false`.
