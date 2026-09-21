# THQ4 native finalist gate (2026-09-21)

Lifecycle: `active`

## Decision boundary

This gate decides whether a persistable final document code can replace the
current FP32-side reranker on the frozen R4 candidate stream. It is a native
latency/quality gate, not another codec taxonomy study.

The production serving metric is fixed to **cosine**. The oracle is cosine
between the original unit-normalized FP32 query and document vectors (equal to
their inner product only because both source vectors are unit norm). Every
approximate final arm is scored with cosine semantics. Paper-faithful RSLM IP
scoring remains a separate reproduction/control line and is not mixed into the
production finalist decision.

The candidate protocol is three parallel alternatives, never a sequential
`THQ4 -> RQ -> RSLM` cascade:

```text
frozen R4 candidate stream (152 queries, 5,000--5,099 IDs/query)
  ├─ fixed THQ4 exact byte-LUT filter -> top-128
  ├─ final arm: THQ-joint2
  ├─ final arm: RQ32
  ├─ final arm: faithful RSLM3
  ├─ final arm: faithful RSLM4
  └─ final arm: direct INT8 control
```

Every arm receives the same THQ4 top-128 IDs and uses the same final FP32
oracle rerank for the quality reference. A separate native scorer measurement
may replace that oracle only after exact top-10 parity is established.

## Frozen inputs

The gate must bind the existing candidate flat/raw/receipt trio, canonical
1M document vectors, queries, qrels, teacher IDs, THQ4 ordinal codes, and
thresholds by SHA-256. Candidate offsets are a derived little-endian sidecar
whose receipt binds the candidate raw SHA and the 152 row counts.

The RSLM arms use the official relative codec for the paper-faithful IP
control: RSLM3 stores 144 symbol bytes plus two-byte inner and outer UE7M9
scales (148 B side payload); RSLM4 stores 192 symbol bytes plus the same two
scales (196 B). For the production cosine scorer the positive outer scale is
mathematically cancelled, so the persistable layouts are RSLM3 = 144 symbol
bytes + 2-byte inner scale (146 B) and RSLM4 = 192 symbol bytes + 2-byte inner
scale (194 B). The current 148/196 B materialization is therefore retained as
paper-faithful IP/control evidence, not as the minimal production cosine
layout. `RSLM4Lite` is excluded: the official notebook does not support it in
residual mode.

`FastScan` names the THQ filter kernel and is not a sixth final-codec arm.

## Required measurements

For every arm and every query record:

* exact top-10 list and approximate top-10 list;
* candidate-FP32 overlap, teacher overlap, qrels nDCG@10, p05 per-query
  nDCG@10, and worst-query loss;
* filter-only latency, final-rerank latency, total cascade latency, and
  p50/p95/p99 over per-query samples;
* logical bytes touched, unique 4 KiB pages, and candidate IDs refined.

The native scorer's checksum is computed after the timed region. Correctness
checksums must remain non-zero and finite, but must not contaminate reported
latency. Scalar and any SIMD implementation must read the same record layout.

## Acceptance and evidence status

The gate is `EXECUTED` only when all five arms have native/reference top-10
parity, the THQ4 top-128 set matches the independent interval² reference, and
all source/producers are SHA-bound. Until then this note remains `planned` and
the current RSLM result remains NumPy quality evidence only.

The first implementation step is complete. Candidate-union RSLM3/4 records
were materialized for 463,258 unique documents. The external raw artifact is
bound by SHA-256
`ee6df345ce968407c647ba62f868f7100534a41c583ac093a0c976bde96a0b4c`; its
source-bound candidate-stream and sample audit are committed in
`2026-09-21-thq-rslm-faithful-candidates.audit.json` with `PASS`,
`candidate_stream_replay: true`, and `sample_replay: true`. The materializer
stores official symbols and both UE7M9 scales, with 148 B/document for RSLM3
and 196 B/document for RSLM4. This is a correctness/parity artifact over the
candidate union, not a page-locality or serving-latency measurement.

The next discriminating check is a portable C++ decode/score control that
must reproduce the Python sample bytes and top-10 lists before timing is
reported. AVX2, page locality, and held-out-domain replay are separate steps
and cannot be inferred from this materialization result.
