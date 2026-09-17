# Native full-corpus candidate gate: reference replay (2026-09-16)

Status: `EXECUTED`; evidence status: reference quality and logical-cost result.
The native serving gate remains `PENDING`.  The earlier Python reference
replay is retained as historical evidence only: the runner now uses the exact
native byte-LUT accumulation order and records quality-input hashes, so a new
authoritative replay must be generated from the frozen candidate payload.

The corrective native runner now emits per-query timings for direct linear,
direct power-.625, THQ prefilter, each top-128 rerank, and both cascade totals,
with mean/p50/p95/p99 summaries.  This removes the previous ambiguity where a
single `direct_mean_ms` or `cascade_mean_ms` combined unrelated arms.  The
full-corpus control also reports all `23,438` THQ scan pages separately from
shortlisted INT8 payload pages; the candidate-stream page counts below remain
candidate-local and must not be read as full-scan accounting.

The frozen three-seed fused candidate stream was replayed for all 152 queries
with four matched arms:

```text
direct INT8 linear
THQ4 interval² -> top128 -> INT8 linear
direct INT8 power-.625
THQ4 interval² -> top128 -> INT8 power-.625
```

The runner is fail-closed on the native materialization receipt, candidate
flat/raw SHA values, candidate record size, candidate ID range and per-query
uniqueness, query/qrels/teacher shapes, and payload sizes/SHA values.  The
authoritative aggregate is in
`2026-09-16-native-full-corpus-candidate-gate-reference.json`; the complete
row-level result is the local `gate1-reference-result.json` and is intentionally
not committed with the large candidate payload.

Observed reference result:

| arm | qrels nDCG@10 mean | teacher overlap mean | reference p50 ms | logical bytes/query mean | unique 4-KiB pages mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct INT8 linear | 0.65618 | 0.98684 | 14.29 | 1,945,315 | 6034.6 |
| THQ4 → linear | 0.65618 | 0.98684 | 30.44 | 530,979 | 4681.2 |
| direct power-.625 | 0.65852 | 0.98289 | 42.83 | 1,945,315 | 6034.6 |
| THQ4 → power-.625 | 0.65852 | 0.98289 | 30.88 | 530,979 | 4681.2 |

For both codecs, the ordered top-10 from the THQ4 cascade matched the direct
top-10 for all 152 queries.  Thus this replay confirms the logical prefilter
property on this frozen candidate stream and shows roughly a 3.66× logical
payload reduction.  It does not establish native latency: the recorded timing
scope is `python_reference_candidate_scoring_and_selection`, with no AVX2,
OS-page, cold/warm, MDBX, or storage-server measurement.  It also does not
measure route recall outside the supplied candidate stream; that remains owned
by the upstream R4 evidence.
