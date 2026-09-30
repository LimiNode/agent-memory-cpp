# Native MDBX bounded-batch materialization sweep (2026-10-01)

This gate measures durable materialization batching for the split 388-byte
INT8-plus-scale segment projection at one million logical documents and 128
rows per segment. The fixture is synthetic zero-code payload data; it is used
only to measure commit overhead and storage footprint, not retrieval quality or
codec performance.

| batch documents | durable commits | materialize ms | MDBX bytes |
| ---: | ---: | ---: | ---: |
| 1,024 | 977 | 4,077.589 | 402,653,184 |
| 8,192 | 123 | 3,945.140 | 402,653,184 |
| 65,536 | 16 | 3,797.726 | 402,653,184 |
| 131,072 | 8 | 3,870.797 | 402,653,184 |
| 262,144 | 4 | 3,904.565 | 402,653,184 |
| 524,288 | 2 | 4,033.973 | 402,653,184 |
| 1,000,000 | 1 | 3,981.496 | 402,653,184 |

The runner is `tools/agent-memory-bench/run-native-mdbx-batch-sweep.py`; the
compact receipt is `2026-10-01-native-mdbx-batch-sweep.result.json`. The result
uses native `uint32_t` `MDBX_INTEGERKEY` ordering, shows the expected
commit-count relationship and a stable file footprint, but
does not establish publication visibility, concurrent-reader safety, crash
recovery, or any codec-quality claim. Those remain lifecycle gates.
