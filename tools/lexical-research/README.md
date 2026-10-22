# Lexical research runner

This directory is an offline research harness. It is intentionally outside the
C++ library target and has no third-party Python dependency.

Run the committed smoke fixture:

```powershell
py -3 tools/lexical-research/run_lexical_protocol.py `
  --dataset tools/lexical-research/fixtures/lexical_protocol_smoke.json `
  --self-test
```

Write a compact JSON receipt for a local run:

```powershell
py -3 tools/lexical-research/run_lexical_protocol.py `
  --dataset tools/lexical-research/fixtures/lexical_protocol_smoke.json `
  --repeats 5 `
  --output .artifacts/lexical-protocol-smoke.json
```

The runner currently implements only the protocol BM25 control. Its tokenizer
and scorer are a portable fixture implementation, not evidence that the Python
path is byte-for-byte equivalent to `ExactLexicalIndex`. PR3 must add separate
tokenizer, scorer, and lifecycle parity checks against the C++ baseline before
using it for algorithmic claims. Model inference, WAND/BMW traversal,
compressed postings, and production MDBX are intentionally out of scope.

Every query must declare an `answer_mode`. `judged` requires at least one
positive qrel and is the only mode included in aggregate Recall, nDCG, and MRR.
`judged_zero_relevant` requires explicit zero-grade qrels and records a fully
judged query with no relevant document; its `zero_relevant_accuracy` is the
fraction returning no hits, so false-positive retrieval is visible. `no_answer`
has its own `no_answer_accuracy`. When a receipt has no eligible `judged`
queries, Recall, nDCG, and MRR are `null` and the quality status cannot be
`complete`. `no_answer`, `unjudged`, and `ignore`
must not carry qrels: they respectively test expected empty retrieval, identify
absent judgments, and omit a query from this protocol. `incomplete_qrels`
requires at least one judgment, is reported separately, and prevents the
receipt from claiming a complete quality evaluation. Duplicate `(query_id,
doc_id)` qrels are rejected, including duplicate grades, so a fixture cannot
silently choose between conflicting judgments.

The `--limit` value is the candidate depth and defaults to 50 so that the
reported `Recall@50` is not silently truncated. The receipt separates one-time
index build from query preprocessing, scoring, and end-to-end query timing.
Tail percentiles use every query-repeat sample; the per-query receipt retains
the median for compatibility. Fewer than 100 raw query-repeat observations are
marked `directional`, 100–999 are `descriptive`, and only 1000 or more are
`measured`. Here `sample_count = query_count * repeat_count`: repeated runs of
the same queries are observations, not independent queries. `measured` names
the observed sample distribution only; it does not claim that p99 is
statistically stable. Python timings must not be compared directly with
production C++ latency.
