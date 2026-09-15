# Fused `[doc_id + THQ]` payload layout control

This follow-up materializes the immutable payload proposed by the corrected
R4/AoSoA cascade. Each record is a 4-byte document id followed by the frozen
144-byte THQ4 code. The control compares a contiguous flat file with a
page-aligned immutable representation. It reports logical payload bytes,
physical file bytes, a deterministic SHA, and a warm sequential scan timing.

This is a storage-layout control, not an MDBX benchmark and not a production
latency claim. Both arms are materialized in document-ID order; therefore the
flat `1.0x` result proves only zero padding overhead, not candidate-friendly
locality or random-gather latency. The next experiment must feed the actual
AoSoA-32 candidate stream and compare fused flat, page-ranged, and MDBX records.

The external receipt is independently checked by
`audit-r4-fused-payload-layout.py` (PASS, two layouts). On the 200k-document
control, flat storage is 29.6 MB (1.0x logical amplification), while
per-record 4-KiB alignment is 819.2 MB (27.68x) and a one-byte-per-record
resident touch scans about 4x slower. This timing is a resident-touch control,
not a full payload scan or a production latency measurement.
