# R4 fused-storage layout bake-off (2026-09-15)

This control uses the real whole-posting three-seed AoSoA-32 candidate stream
at `A=8192`: 762,082 records, with 5,000–5,099 candidates per query. It
compares 144-byte THQ thermometer records with packed 96-byte ordinal records.
The MDBX arms are logical page/key-overhead models, not a physical MDBX
database benchmark.

| Representation | Layout | Logical bytes | Physical bytes | Pages |
|---|---|---:|---:|---:|
| 144-byte thermometer | flat | 112.79 MB | 112.79 MB | 27,537 |
| 144-byte thermometer | page-blocked | 112.79 MB | 115.94 MB | 28,305 |
| 96-byte ordinal | flat | 76.21 MB | 76.21 MB | 18,606 |
| 96-byte ordinal | page-blocked | 76.21 MB | 76.64 MB | 18,712 |

The logical MDBX blob model is 116.04 MB for thermometer and 78.48 MB for
ordinal; the chunk model charges a key per roughly 4-KiB chunk and is slightly
larger. The overshoot-aware counts are used throughout; no candidate stream is
truncated to 5,000.

The external receipt binds the candidate-stream receipt/raw/file SHA values and
passes `semantic_r4_storage_layout_bakeoff_audit_v2`. These remain arithmetic
models, not measured MDBX pages, transactions, or latency.
