# K16 intra-address layout microbenchmark

The corrected R4 route already shows K16 quality is sufficient; this control
tests whether a simple dimension-major representation is automatically faster
than row-major K16 scoring. A frozen INT8 representative sample (4,096 groups
of 16) is decoded with the native codec contract: 384 unsigned bytes, `code -
127`, followed by a little-endian FP32 amplitude at record offset 384. It is
then scored against all 152 queries with identical checksums.

On the corrected NumPy control, row-major takes 395.4 ms and dimension-major
7846.1 ms. This is not a SIMD implementation and must not be read as a native
AVX2 result; it is a warning that a physical transpose alone is not an
optimization. The next implementation should use an explicit native 16-lane
kernel and report parity plus p50/p95 timing before claiming a layout win.
