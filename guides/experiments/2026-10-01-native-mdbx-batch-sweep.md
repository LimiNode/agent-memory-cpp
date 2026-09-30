# Native MDBX bounded-batch materialization sweep (2026-10-01)

This gate measures durable materialization batching for the split 388-byte
INT8-plus-scale segment projection at one million logical documents and 128
rows per segment. The fixture is synthetic zero-code payload data; it is used
only to measure commit overhead and storage footprint, not retrieval quality or
codec performance.

| batch documents | durable commits | materialize ms | MDBX bytes |
| ---: | ---: | ---: | ---: |
| 1,024 | 977 | 4,224.735 | 402,653,184 |
| 8,192 | 123 | 3,865.627 | 402,653,184 |
| 65,536 | 16 | 3,835.639 | 402,653,184 |
| 131,072 | 8 | 3,870.436 | 402,653,184 |
| 262,144 | 4 | 3,846.078 | 402,653,184 |
| 524,288 | 2 | 3,833.542 | 402,653,184 |
| 1,000,000 | 1 | 3,777.766 | 402,653,184 |

The runner is `tools/agent-memory-bench/run-native-mdbx-batch-sweep.py`; the
compact receipt is `2026-10-01-native-mdbx-batch-sweep.result.json`. The result
shows the expected commit-count relationship and a stable file footprint, but
does not establish publication visibility, concurrent-reader safety, crash
recovery, or any codec-quality claim. Those remain lifecycle gates.
