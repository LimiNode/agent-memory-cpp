# 2026-09-11 packed two-bit THQ flat scan

THQ4 levels fit in two bits per coordinate: 384×2 = 768 bits / 96 B per
document, versus 1,152 thermometer bits / 144 B.  The benchmark packed the
canonical codes, decoded them through a fixed lookup table, and exhaustively
computed ordinal L1 for eight semantic queries.  The first two scans were
bit-for-bit equal to thermometer Hamming; all eight retained `1.0 @256`
teacher survival.  Python decode timing was p50 3,096.8 ms/query and p95
3,208.8 ms/query, a diagnostic implementation cost rather than native
latency.  The 33% footprint reduction is real; native SIMD/tiled layouts are
required before a throughput claim.

`production_activation: false`; no codec replacement is licensed by this
microbenchmark.
