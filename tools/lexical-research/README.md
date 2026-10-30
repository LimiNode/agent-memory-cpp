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
path is byte-for-byte equivalent to `ExactLexicalIndex`. PR3 adds separate
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

## Persistent lexical IndexStore oracle

The requirements handoff is recorded in
`guides/experiments/2026-10-24-persistent-lexical-index-requirements.md`.
Validate its implementation-independent fixture with:

```powershell
py -3 tools/lexical-research/validate_persistent_lexical_oracle.py `
  tools/lexical-research/fixtures/persistent_lexical_oracle_v1.json --self-test
```

The oracle separates canonical storage from derived search data:
`Canonical Storage -> SearchProjection -> Persistent Lexical IndexStore ->
Retrieval -> Canonical Hydration`. It records scorer-shared TF/DF inputs,
snapshot identity, stale-generation overlays, and fail-closed outcomes. It
does not implement an MDBX IndexStore or select a production scorer.

The validator independently recomputes BM25, BM25+, and BM25L scores from
explicit scorer parameters, including delta and tolerance, and checks score,
ranking, deterministic tie, collection-statistics, and reopen parity for
independent resource and chunk arms. The mixed arm is diagnostic only. It also
excludes zero-score non-matches, derives lifecycle completion from versioned
overlay membership and posting freshness,
round-trips the published snapshot identity, and checks numeric block upper
bounds. Its self-test must fail for a changed score, lifecycle result, freshness
field, reopen identity, bound, or required case ID.

## PR3 C++ parity

Build the opt-in C++ exporter with `AGENT_MEMORY_BUILD_TESTS=ON`, then run the
comparator from the repository root:

```powershell
py -3 tools/lexical-research/run_cpp_lexical_parity.py `
  --cpp-exporter .build/tests/Release/agent_memory_lexical_parity_export.exe `
  --golden tools/lexical-research/fixtures/cpp_tokenizer_golden.json
```

The receipt keeps four gates independent: `cpp_tokenizer_contract` compares
text, `TokenKind`, byte range, and position with hand-authored golden data;
`python_scorer_parity` consumes the actual C++-tokenized document and query
streams, recomputes BM25 document statistics from those streams, and compares
the resulting ordering and scores, including repeated terms, ties, near-ties,
and explicit multi-field filters; `lifecycle_parity`
checks scores and visibility after the in-memory update/erase fixture;
`native_tokenizer_delta` records expected differences between the portable
fixture tokenizer and the C++ tokenizer. Python `kind` values in that delta are
`unknown` placeholders, and Python offsets/lengths come from a
`casefold().find()` heuristic. They are diagnostic records, not a Python token
kind contract or exact byte ranges; Unicode case folding may change string
length.

Every comparator run also executes three mutation controls. The positive
control must pass, changing one hand-authored golden token must make
`cpp_tokenizer_contract` fail, and changing one exported C++ BM25 score must
make `python_scorer_parity` fail. Each mandatory gate failure must produce a
non-zero comparator exit code. The `mutation_self_test` result is included in
the receipt and is also registered as a separate CTest.

A top-level `SEPARATE_GATES` status is intentionally not an unconditional
`PARITY_PASS`. The exporter is a research fixture and does not establish MDBX
publication lifecycle authority; existing importer/manifest tests remain the
lifecycle evidence.

## Lexical Retrieval Qualification R1

R1 is a design-only qualification contract that compares the existing BM25
baseline, flat-v2/seg-v2 physical backends, learned-sparse weighted-postings
smoke, and BGE-M3 pilot using one frozen mini-corpus. It does not add a
production algorithm or a second benchmark framework. The manifest and fixture
are validated, and the BM25 row delegates to this directory's existing
`run_lexical_protocol.py` runner:

```powershell
py -3 tools/lexical-research/validate_lexical_retrieval_qualification.py --self-test
```

See [`guides/experiments/2026-10-30-lexical-retrieval-qualification-r1.md`](../../guides/experiments/2026-10-30-lexical-retrieval-qualification-r1.md)
for the baseline statuses, frozen provenance, candidate-versus-hydration
metrics, lifecycle cost fields, and timing limitations. In particular,
`sample_count` counts query-repeat observations rather than independent
queries, and p99 remains a directional distribution statistic until a separate
stability analysis is supplied.
