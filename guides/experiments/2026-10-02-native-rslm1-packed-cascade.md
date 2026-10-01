# Native packed RSLM1 candidate gate (2026-10-02)

This closes the candidate-local packed decode-and-score gate for the
paper-faithful RSLM1 arm on the frozen R4 shell. The scorer reads packed 4-bit
C4D symbols, UE7M9 scales, THQ4 codes and official C4D/FWHT constants. It
accumulates the score in the transform domain and does not materialize a dense
384-D document vector or perform a candidate-row lookup in the timed region.

## Evidence

- 152 queries, 128 candidates per query;
- exact ordered top-10 parity: 152/152 against the frozen RSLM1 reference;
- transform-domain packed timing: p50 0.8042 ms, p95 1.1402 ms, p99
  1.2950 ms on the recorded Windows host;
- independent fail-closed audit and C++ self-test pass.

This is candidate-local packed evidence. It is not a full 1M-row serving or
MDBX layout benchmark, and its timing must not be merged with the end-to-end
THQ routing columns until the same topology is rerun for every finalist.
The source artifact directory is
`E:\\_repoz\\agent-memory-workspaces\\rslm1-faithful-closure-v1`; its
materialization receipt binds the official Google Research RSLM revision and
all packed-file hashes.

See [`2026-10-02-native-rslm1-packed-cascade.result.json`](2026-10-02-native-rslm1-packed-cascade.result.json).
