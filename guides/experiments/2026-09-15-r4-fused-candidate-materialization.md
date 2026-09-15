# Actual fused R4 candidate materialization (2026-09-15)

This is the first fused-payload experiment driven by the real production
candidate stream rather than `doc_id = 0..N`. It consumes the frozen AoSoA-32
K1→K16 order files at `A=8192`, fuses all three R4 seeds under the 5k unique
candidate budget for all 152 queries, and writes `[int32 doc_id + 144-byte
THQ4]` records in candidate-stream order.

The materialized stream contains 760,000 records and 112,480,000 logical
bytes. The packed flat file occupies 27,461 logical 4-KiB pages. A page-blocked
layout with 27 records per page occupies 28,272 pages / 115,802,112 bytes, only
about 3.0% padding overhead. This replaces the earlier synthetic 27.68x
page-per-record number with a realistic page-blocked upper bound.

Candidate order is highly non-document-local: mean absolute document-ID delta
across the stream is about 294,704. Therefore a fused contiguous stream is
useful only when the query reads the stream sequentially or when it is
materialized as candidate-specific immutable slabs; it does not make random
canonical document fetches local by itself.

Independent audit: `semantic_r4_fused_candidate_materialization_audit_v1`,
760,000 records, PASS. This remains a logical file-layout experiment; MDBX
page splits, transactions, and OS/media latency are not measured.
