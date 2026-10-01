# Native packed PLSQ cascade (2026-10-02)

This gate closes the candidate-local native scorer for the persisted Faiss
PLSQ payloads. It reconstructs each residual directly from packed PLSQ symbols
and shared codebooks, adds the packed THQ base, computes cosine score, and
selects ordered top-10 results. No predecoded document vectors are read by the
timed scorer.

| Arm | Payload side bytes | p50 (ms) | p95 (ms) | p99 (ms) | ordered parity |
| --- | ---: | ---: | ---: | ---: | ---: |
| PLSQ8x4x8 | 36 | 0.3647 | 0.4304 | 0.4860 | 152/152 |
| PLSQ8x6x8 | 52 | 0.4988 | 0.6122 | 0.6985 | 152/152 |

The fixture is the frozen 152-query × 128-candidate R4 shell. The result is
candidate-local native packed evidence, not a full 1M serving benchmark or an
MDBX layout result. Full-corpus PLSQ payload materialization and cold/restart
serving remain separate gates.

The executable is `tools/agent-memory-bench/native-plsq-benchmark.cpp`; its
self-test is built in the R4 CI harness. The compact machine-readable receipt
is `2026-10-02-native-plsq-packed-cascade.result.json`.
