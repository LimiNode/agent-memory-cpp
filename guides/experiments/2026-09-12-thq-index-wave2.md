# THQ index wave 2: IMI and SPANN-like posting controls

Date: 2026-09-12. Fixture: frozen `thq-full-scan-v2` (1,000,000 documents,
THQ4-384). This is an in-memory coarse-posting oracle; no R4 mapping, MDBX
backend, or production activation is claimed.

The runner uses the first six decoded THQ coordinates. The SPANN-like control
has 64 exact three-coordinate, four-level THQ signature postings. The IMI
control splits the signature into two disjoint three-coordinate, four-level
THQ subspaces, producing a 64x64 Cartesian cell grid. Cells/postings are
ordered by additive ordinal distance and the top `L` values are unioned.
Payload reranking is intentionally not run.

Eight-query smoke means:

| L | SPANN-like candidates | SPANN-like teacher recall | IMI candidates | IMI teacher recall |
|---:|---:|---:|---:|---:|
| 1 | 15,705 | 0.1500 | 416 | 0.0000 |
| 2 | 30,382 | 0.1875 | 1,302 | 0.0000 |
| 4 | 62,249 | 0.2625 | 2,145 | 0.0125 |
| 8 | 121,662 | 0.3250 | 3,939 | 0.0125 |
| 16 | 245,678 | 0.5375 | 11,191 | 0.0125 |
| 32 | 493,298 | 0.7500 | 18,484 | 0.0375 |

Among tested points below 50k mean candidates, the best SPANN-like recall is
`0.1875` at `L=2`; the next point is already about 62k candidates with recall
`0.2625`, far below the >=0.995 target. The IMI Cartesian partition is
negative in this configuration. These findings are specific to the first six
raw THQ coordinates and do not establish that IMI is intrinsically sparse or
that SPANN fails with semantic prototypes. A real follow-up must use verified
full-dimensional prototypes, optional boundary replication, and exact THQ-ADC
reranking before storage materialization.

The receipt records occupancy diagnostics (posting/cell size distribution,
occupied cells, empty top-L fraction), frozen fixture and runner hashes, smoke
status, and `production_activation: false`.
