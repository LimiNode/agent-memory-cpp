# Lexical Retrieval Qualification R2

Status: matched C++ correctness/performance smoke. This is not a quality
conclusion and does not activate a new production backend.

## Scope

R2 exercises the implementation paths that are present today:

* `bm25`: the production in-memory `ExactLexicalIndex` baseline;
* `flat-v2`: `MdbxPersistentLexicalIndex` with its flat-v2 layout;
* `segmented-v2`: `MdbxSegmentedLexicalIndex` with the incremental layout.

The three rows use the same frozen corpus, tokenizer (`StandardTokenizer`),
query terms, BM25 parameters, candidate depth, and qrels. Flat-v2 and
segmented-v2 are physical index backends, not independent ranking algorithms.
Learned sparse and BGE-M3 are not part of this C++ run.

## Inputs and command

The small committed smoke inputs are:

* `tools/lexical-research/fixtures/lexical_retrieval_qualification_r2_corpus.tsv`;
* `tools/lexical-research/fixtures/lexical_retrieval_qualification_r2_queries.tsv`.

The C++ harness is `tools/lexical-retrieval-qualification-r2.cpp`. It builds
all three paths, scores the same queries, reopens the MDBX backends, and emits
a receipt. The CTest smoke, validator mutation self-test, and bounded parallel
run are registered only when the existing MDBX benchmark option is enabled.
Ranking parity is exact; score parity uses the receipt's fixed absolute
tolerance of `1e-5` to account for the reference float implementation versus
the double-valued diagnostic scorer.

The fixture's `Token` metadata uses ordinal synthetic ranges solely to satisfy
the in-memory index record contract; those values are not source byte offsets
and are not evidence about document positions.

The query TSV uses `-` as the explicit empty-qrels sentinel for the
no-answer control, avoiding an ambiguous trailing empty field.

Each run reserves a fresh, sentinel-marked MDBX directory under the requested
parent and removes only that owned directory through RAII. The parallel CTest
regression starts two processes, checks that both receipts validate, and checks
that an external sentinel in the parent remains unchanged.

The receipt contains ordered per-query IDs and scores for every backend, the
candidate count, frozen fixture digests, tokenizer/scorer/backend revisions,
and explicit measurement-path labels. The Python validator recomputes parity
from that evidence; it does not trust the harness's boolean flags alone.

The receipt reports exact logical ranking parity and score parity within the
fixed tolerance against the BM25 baseline, Recall@1/5, nDCG@10, MRR,
no-answer accuracy, build/update/reopen times, index bytes, and p50/p95/p99
query samples. `sample_count` is the number of query observations
(`one_query`), not independent queries; the receipt is `directional` and makes
no p99 stability claim.

`bm25` latency measures the production `ExactLexicalIndex::search()` path.
The durable backend rows measure a diagnostic BM25 scorer over a materialized
snapshot, not an equivalent production retrieval API. Therefore these latency
values are not a production backend performance comparison.

## Evidence boundaries

The harness checks lexical index state and reopen parity. It has no canonical
MDBX read path for resource/chunk hydration or authoritative source-frontier
eligibility. Consequently every receipt marks hydration as
`NOT_MEASURED_NO_CANONICAL_READ_PATH`, and quality as
`QUALITY_INCONCLUSIVE` because the qrels are a correctness smoke rather than
independent judgments. No result should be used to claim BM25 quality, learned
sparse irrelevance, or segmented-v2 production readiness.

## Diagnostics and next steps

If parity fails, classify the failure as scoring, tokenizer/normalization,
index representation, candidate enumeration, or lifecycle/reopen behavior.
Token IDs may differ between backends; logical postings and rankings are the
required comparison. The next experiment should use a larger permitted corpus
and independent qrels only after this matched smoke is reviewed. Hydration and
provenance may be measured separately once a real canonical read path is
available.
