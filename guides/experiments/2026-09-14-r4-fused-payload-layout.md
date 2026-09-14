# Fused `[doc_id + THQ]` payload layout control

This follow-up materializes the immutable payload proposed by the corrected
R4/AoSoA cascade. Each record is a 4-byte document id followed by the frozen
144-byte THQ4 code. The control compares a contiguous flat file with a
page-aligned immutable representation. It reports logical payload bytes,
physical file bytes, a deterministic SHA, and a warm sequential scan timing.

This is a storage-layout control, not an MDBX benchmark and not a production
latency claim. The page-aligned arm intentionally exposes the upper bound on
padding amplification; the next experiment will feed the actual AoSoA-32
candidate stream and compare fused flat, page-ranged, and MDBX records.
