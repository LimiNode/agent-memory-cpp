# Native MDBX serving layout bakeoff (2026-09-30)

This gate measures physical MDBX payload serving for the frozen one-million-row
THQ4/INT8 corpus. It is deliberately separate from the earlier allocation-free
kernel control: the THQ candidate stream is read from the production-control
fixture, while MDBX is responsible only for persisted payload materialization
and candidate payload reads.

The runner is `tools/agent-memory-bench/native-thq-mdbx-serving.cpp`. It compares
two physical layouts under the same default MDBX commit mode:

* `row_kv`: one key/value per document (`uint32 document_id -> 96-byte THQ4 +
  384-byte INT8 + float32 scale);
* `segment_blob`: 4,096 contiguous fixed-width rows per value.

The fixture contains 152 queries, 128 candidate IDs per query, and the expected
ordered top-10 from the frozen production-control receipt. Both layouts pass
ordered parity 152/152.

| layout | build | MDBX file | reopen first read | warm p50 / p95 / p99 |
| --- | ---: | ---: | ---: | ---: |
| row KV | 308,344.921 ms | 553,648,128 B | 1.834 ms | 0.329 / 0.886 / 1.125 ms |
| segment/blob (4,096 rows) | 1,794.665 ms | 486,539,264 B | 220.567 ms | 136.977 / 163.316 / 185.366 ms |

These numbers answer only the physical payload-read question. They do not
include a full THQ scan, index routing, OS-cache eviction, restart durability
recovery, or update/rebuild concurrency. The row layout is attractive for the
current sparse 128-candidate read but expensive to build; the segment layout is
more compact and amortizes writes, while paying for larger segment reads. No
codec winner or production latency threshold is inferred.

The bounded lifecycle companion (`2026-09-30-native-mdbx-lifecycle.result.json`)
passes update visibility, tombstone preservation, and committed generation
publication. It is a smoke gate, not a crash-recovery or concurrent-writer
stress result.
