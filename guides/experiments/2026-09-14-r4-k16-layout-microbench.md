# K16 intra-address layout microbenchmark

The corrected R4 route already shows K16 quality is sufficient; this control
tests whether a simple dimension-major representation is automatically faster
than row-major K16 scoring. A frozen INT8 representative sample (4,096 groups
of 16) is scored against all 152 queries with identical checksums.

On the current NumPy control, row-major takes 375.8 ms and dimension-major
7812.3 ms. This is not a SIMD implementation and must not be read as a native
AVX2 result; it is a warning that a physical transpose alone is not an
optimization. The next implementation should use an explicit native 16-lane
kernel and report parity plus p50/p95 timing before claiming a layout win.
