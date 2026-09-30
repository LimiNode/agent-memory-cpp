# Native MDBX segment-row sweep (2026-09-30)

This gate is the completed 1M-document persistence/serving sweep for the
THQ4/INT8 production-control fixture. It uses the native MDBX executable with
the same 152 queries, 128 candidates per query, five timed repeats and ordered
top-10 parity contract for every profile. The runner is
`tools/agent-memory-bench/run-native-mdbx-layout-sweep.py`; the receipt is
audited independently by `audit-native-mdbx-layout-sweep.py`.

| rows per segment | materialize (ms) | file bytes | warm p50 (ms) | p95 (ms) | p99 (ms) | reopen first query (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 19,516.215 | 402,653,184 | 0.9155 | 1.5765 | 1.8933 | 2.6817 |
| 32 | 10,473.956 | 402,653,184 | 1.2660 | 2.4578 | 3.0936 | 4.1821 |
| 64 | 5,981.152 | 402,653,184 | 1.5618 | 4.4750 | 5.3903 | 5.7457 |
| 128 | 3,768.009 | 402,653,184 | 4.2209 | 7.4977 | 9.0137 | 9.6967 |
| 256 | 2,559.099 | 402,653,184 | 8.6213 | 12.4385 | 15.3197 | 16.8557 |
| 512 | 1,890.503 | 402,653,184 | 16.2234 | 20.6380 | 25.8707 | 30.6953 |
| 1024 | 1,655.578 | 402,653,184 | 27.9224 | 34.5709 | 41.0736 | 57.0882 |
| 4096 | 1,453.676 | 402,653,184 | 113.6379 | 148.1682 | 167.0260 | 214.6662 |

Every row passed ordered top-10 parity `152/152`, with the same checksum
`16797446933002326132`. The receipt binds SHA-256 hashes for THQ codes,
INT8 codes/scales, queries, candidates and expected results, and records the
single-threaded unpinned Windows host configuration.
The external raw receipt is SHA-256
`7df6185d2cbfabc00a6c0bd254a23fa9293fc74cfdb7bc6cfa41033119f5deaf9`;
its 760 timing samples per profile are replayed by the independent auditor,
including percentile and expected-ID checksum recomputation.

The sweep answers the physical segment-size trade-off only. Keys use the
native `uint32_t` `MDBX_INTEGERKEY` contract; the re-keyed replay preserved
the previous 152/152 ordered result checksum. Both row and
segment projections now store only the final INT8 code plus scale (388 bytes
per logical row); THQ remains a separate routing/index input rather than being
duplicated in the final-code payload. Decode/training, full THQ routing, cold-cache eviction,
crash recovery, concurrent update/rebuild and fresh qrels remain separate
gates. The results therefore do not select a codec or establish a production
latency threshold. The compact receipt is committed as
`2026-09-30-native-mdbx-segment-sweep.result.json`; large MDBX files remain
outside Git.

The materializer now supports bounded durable batches through the optional
`--batch-documents` argument and reports `durable_commits`. A 1,000-document
segment smoke with 128-row segments and 256-document batches produced four
commits and passed a locally regenerated ordered top-10 parity check. This is
only a plumbing smoke: it is not added to the 1M sweep receipt, and full
concurrent generation publication remains a separate lifecycle gate.

Future receipts from the executable also expose p50/p95/p99 stage timings for
read/decode, scalar score and bounded top-k selection. The independent auditor
accepts these fields only as a complete non-negative group; the historical 1M
receipt predates this optional stage breakdown and is not retrofitted.
