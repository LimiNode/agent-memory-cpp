# R4 fused-storage layout bake-off (2026-09-15)

Using the real 760,000-record three-seed AoSoA-32 candidate stream at `A=8192`
and 5k/query, this control compares current 144-byte THQ thermometer records
with packed 96-byte ordinal records. It evaluates contiguous flat, 27/40-record
page-blocked, and logical MDBX blob/chunk page models. The MDBX arms are page
and key-overhead models, not a physical MDBX database benchmark.

The packed ordinal representation reduces the logical fused entry from 148 B
to 100 B. For the actual candidate stream, flat storage is linear in logical
payload, while page-blocked storage adds only the expected 2--3% page padding.
This is the relevant replacement for the earlier page-per-record upper bound.
The external receipt binds the candidate-stream file SHA and passes
`semantic_r4_storage_layout_bakeoff_audit_v1`.
