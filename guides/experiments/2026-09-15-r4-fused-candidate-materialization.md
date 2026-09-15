# Actual fused R4 candidate materialization (2026-09-15)

This is the first fused-payload experiment driven by the real production
candidate stream rather than `doc_id = 0..N`. It consumes the frozen AoSoA-32
K1→K16 order files at `A=8192`, fuses all three R4 seeds under the 5k unique
candidate budget for all 152 queries, and writes `[int32 doc_id + 144-byte
THQ4]` records in candidate-stream order.

The whole-posting replay contains 762,082 records (mean 5,013.70/query,
min 5,000, max 5,099) and 112,788,136 logical bytes. The packed flat file
occupies 27,537 logical 4-KiB pages. A page-blocked layout with 27 records per
page occupies 28,305 pages / 115,937,280 bytes, about 2.8% padding overhead.
The overshoot is retained; the stream is not truncated to exactly 5,000.

Candidate order is highly non-document-local: mean absolute document-ID delta
across the stream is about 294,704. Therefore a fused contiguous stream is
useful only when the query reads the stream sequentially or when it is
materialized as candidate-specific immutable slabs; it does not make random
canonical document fetches local by itself.

Independent audit: `semantic_r4_fused_candidate_materialization_audit_v2`,
762,082 records, PASS. This remains a logical file-layout experiment; MDBX
page splits, transactions, and OS/media latency are not measured.
