# Native packed RSLM1 candidate gate (2026-10-02)

This closes the native scorer gate for the paper-faithful RSLM1 arm on the
frozen candidate-local R4 shell. The scorer reads packed 4-bit C4D symbols,
UE7M9 inner/outer scales, THQ4 codes and official C4D/FWHT constants; it does
not read the persisted decoded FP32 finalist vectors.

## Evidence

- 152 queries, 128 candidates per query;
- exact ordered top-10 parity: 152/152 against the frozen RSLM1 reference;
- codec rerank timing from the native packed path: p50 0.7206 ms, p95
  1.2166 ms, p99 1.2561 ms on the recorded Windows host;
- independent fail-closed audit and C++ self-test pass.

This is candidate-local packed evidence. It is not a full 1M-row serving or
MDBX layout benchmark, and its timing must not be merged with the end-to-end
THQ routing columns until the same topology is rerun for every finalist.
The source artifact directory is
`E:\\_repoz\\agent-memory-workspaces\\rslm1-faithful-closure-v1`; its
materialization receipt binds the official Google Research RSLM revision and
all packed-file hashes.

See [`2026-10-02-native-rslm1-packed-cascade.result.json`](2026-10-02-native-rslm1-packed-cascade.result.json).
