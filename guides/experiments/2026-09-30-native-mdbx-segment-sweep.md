# Native MDBX segment-row sweep (2026-09-30)

This gate is the completed 1M-document persistence/serving sweep for the
THQ4/INT8 production-control fixture. It uses the native MDBX executable with
the same 152 queries, 128 candidates per query, five timed repeats and ordered
top-10 parity contract for every profile. The runner is
`tools/agent-memory-bench/run-native-mdbx-layout-sweep.py`; the receipt is
audited independently by `audit-native-mdbx-layout-sweep.py`.

| rows per segment | materialize (ms) | file bytes | warm p50 (ms) | p95 (ms) | p99 (ms) | reopen first query (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 19,877.888 | 503,316,480 | 0.9980 | 1.7662 | 2.3164 | 3.1817 |
| 32 | 10,914.127 | 503,316,480 | 1.5890 | 2.6414 | 3.0623 | 3.7075 |
| 64 | 6,396.197 | 503,316,480 | 1.8230 | 5.4103 | 6.0745 | 6.2953 |
| 128 | 4,132.988 | 486,539,264 | 3.0401 | 9.5181 | 11.1006 | 11.9634 |
| 256 | 2,965.206 | 486,539,264 | 4.2032 | 14.7332 | 18.8471 | 21.8002 |
| 512 | 2,300.693 | 486,539,264 | 9.0948 | 25.6068 | 32.5447 | 37.4706 |
| 1024 | 1,962.705 | 486,539,264 | 16.6265 | 40.0918 | 51.8804 | 71.6912 |
| 4096 | 1,787.445 | 486,539,264 | 135.2274 | 154.7272 | 163.2206 | 224.4743 |

Every row passed ordered top-10 parity `152/152`, with the same checksum
`16797446933002326132`. The receipt binds SHA-256 hashes for THQ codes,
INT8 codes/scales, queries, candidates and expected results, and records the
single-threaded unpinned Windows host configuration.

The sweep answers the physical segment-size trade-off only. Payloads are the
reference mixed format; decode/training, full THQ routing, cold-cache eviction,
crash recovery, concurrent update/rebuild and fresh qrels remain separate
gates. The results therefore do not select a codec or establish a production
latency threshold. The compact receipt is committed as
`2026-09-30-native-mdbx-segment-sweep.result.json`; large MDBX files remain
outside Git.
