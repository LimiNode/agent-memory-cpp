# Native packed PLSQ cascade (2026-10-02)

This gate closes the candidate-local packed decode-and-score path for the
persisted Faiss PLSQ payloads. The timed scorer accumulates THQ and PLSQ
codebook dot products directly and uses the persisted final norm; it does not
materialize a 384-D document vector.

| Arm | Payload side bytes | p50 (ms) | p95 (ms) | p99 (ms) | ordered parity |
| --- | ---: | ---: | ---: | ---: | ---: |
| PLSQ8x4x8 | 36 | 0.3801 | 0.4538 | 0.5166 | 152/152 |
| PLSQ8x6x8 | 52 | 0.5377 | 0.6136 | 0.7252 | 152/152 |

The fixture is the frozen 152-query × 128-candidate R4 shell. The result is
candidate-local packed direct-dot evidence, not a full 1M serving benchmark or an
MDBX layout result. Full-corpus PLSQ payload materialization and cold/restart
serving remain separate gates.

The executable is `tools/agent-memory-bench/native-plsq-benchmark.cpp`; its
self-test is built in the R4 CI harness. The compact machine-readable receipt
is `2026-10-02-native-plsq-packed-cascade.result.json`.
