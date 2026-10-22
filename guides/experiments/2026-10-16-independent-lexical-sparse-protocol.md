# Independent Lexical and Sparse Retrieval Research Protocol

Date: 2026-10-20

Status: `PROTOCOL_ONLY` — no experiment result and no production recommendation

Branch: `research/lexical-alternatives`
Original research baseline: `1e221eedf41860c65685dbd3b184922ae68b1d3a`
(`LimiNode/main`, after PR #526, at protocol authoring time)
Historical restack base: `4d6658eff445b71775d11d66aaf15d167ff9973f`
(`LimiNode/main`, after PR #529)
Corrective-review base: `59ddce072d305d3df1dcfb952a94fa4766eab153`
(`LimiNode/main`, before the 2026-10-22 documentation update)
Current PR base after the 2026-10-22 refresh: `f38a22d2e44f6787a882829c08e169baeb777fae`
(`LimiNode/main`, tabular retrieval contracts)

## Research question

Which lexical scoring, learned-sparse, posting-execution, and integer-compression
techniques are worth evaluating for `agent-memory-cpp` after the M0 lexical
contracts stabilize? The first package deliberately produces a literature and
benchmark protocol only. It must not change the importer, `KnowledgeUnitStore`,
`SourceRefSummary`, `IProjectionStore`, public retrieval APIs, or MDBX layout.

The current reference path is sufficient for controlled experiments:

- `StandardTokenizer` is a dependency-free UTF-8/code-aware tokenizer;
- `ExactLexicalIndex` is an exhaustive in-memory Okapi BM25 index with
  `k1=1.5`, `b=0.75`, the project IDF variant, and deterministic `chunk_id`
  tie-breaking;
- `KeywordOverlapIndex` is a deliberately small unique-term-overlap lower bound,
  not a BM25 implementation;
- `RetrievalEvalRunner`, `Evaluation`, and `BenchmarkReport` already provide
  dataset validation, qrels-based `Recall@K`, `nDCG@K`, MRR, no-answer and
  latency reporting;
- the durable M0 path rebuilds one lexical document per resource from canonical
  UTF-8 text; the optional raw-publication profile additionally emits multiple
  revision-bound `Chunk` KnowledgeUnits;
- raw chunks are durable staging records until their active resource manifest
  contains the corresponding bindings. `MdbxKnowledgeUnitStore::scan_*` is a
  storage diagnostic and is not an importer-visible retrieval query;
- `Original`/`SearchProjection` records remain a planned projection-aware
  contract. The current C++ lexical baseline is chunk-shaped and does not claim
  a general `IProjectionStore` implementation.

The roadmap distinction is binding: scoring quality, query expansion, tokenizer,
physical postings, traversal/pruning, and optional model inference are separate
comparison axes. WAND and Block-Max WAND are execution algorithms for a fixed
scoring model; they are not alternative relevance functions.

## Source and implementation matrix

The table records a research candidate, its role, primary reading or reference
implementation, and the reuse risk. A repository license is not a model-weight
license; model and checkpoint terms must be checked again before any runner
downloads or redistributes weights.

| Candidate | Axis | Primary source / implementation | License or reuse status | Initial decision |
| --- | --- | --- | --- | --- |
| Okapi BM25 | scoring baseline | [Robertson et al., TREC-3](https://doi.org/10.1145/290941.291009); project `ExactLexicalIndex` | algorithm/publication; project code owns the baseline | mandatory oracle arm |
| BM25F | scoring / fields | [Pérez-Iglesias et al.](https://arxiv.org/abs/0911.5046); project lexical roadmap | algorithm/publication; no new dependency | evaluate when `SearchProjection` is available |
| BM25+ | scoring / long-document correction | [Lv & Zhai, *Lower-bounding term frequency normalization* (CIKM 2011)](https://doi.org/10.1145/2063576.2063584) | algorithm/publication | classical bake-off |
| BM25L | scoring / length normalization | [Lv & Zhai, *When documents are very long, BM25 fails!* (SIGIR 2011)](https://doi.org/10.1145/2009916.2010070) | algorithm/publication | classical bake-off |
| TF-IDF / pivoted normalization | scoring / normalization | [Salton & Buckley, 1988](https://doi.org/10.1016/0306-4573(88)90021-0); [Singhal et al., 1996](https://doi.org/10.1145/243199.243206) | algorithm/publication | cheap control, not a production default |
| Query Likelihood + Dirichlet | scoring / language model | [Zhai & Lafferty, 2001](https://doi.org/10.1145/383952.384019); Lucene `LMDirichletSimilarity` | Lucene implementation Apache-2.0; formula is independently implementable | classical bake-off |
| DFR PL2 / DPH | scoring / divergence from randomness | [Amati & van Rijsbergen, 2002](https://doi.org/10.1145/505168.505173); [Terrier source](https://github.com/terrier-org/terrier-core) | Terrier repository reports `NOASSERTION`; do not copy code until its terms are verified | literature screen, then implement formula locally if justified |
| Boolean AND/OR/NOT | matching / exact constraints | [Apache Lucene query parser](https://lucene.apache.org/core/), project positional postings contract | Lucene Apache-2.0; project implementation remains independent | code and constraint query arm |
| Phrase / proximity | matching / positions | [Apache Lucene phrase queries](https://lucene.apache.org/core/); project positional postings | Lucene Apache-2.0; project implementation remains independent | code-aware bake-off |
| Trigram / n-gram and fuzzy | matching / typo and prefix tolerance | [Lucene `FuzzyQuery`](https://lucene.apache.org/core/); [Lucene n-gram tokenizer](https://lucene.apache.org/core/) | Lucene Apache-2.0; storage cost must be measured | code/path robustness arm |
| DeepCT | learned sparse / reweighting | [Dai & Callan, 2020](https://doi.org/10.1145/3397271.3401204); [DeepCT repository](https://github.com/AdeDZY/DeepCT) | repository metadata reports BSD-3-Clause; checkpoint/data terms still require review | offline feasibility screen only |
| uniCOIL | learned sparse / lexical impacts | [Lin & Ma, 2021](https://arxiv.org/abs/2106.14807); [Pyserini](https://github.com/castorini/pyserini) | Pyserini Apache-2.0; checkpoints and training data require separate review | offline sparse arm |
| DeepImpact | learned sparse / impact indexing | [DeepImpact reference](https://github.com/AHNR0/DeepImpact); [PISA](https://github.com/pisa-engine/pisa) | PISA Apache-2.0; DeepImpact reference repository reports no verified license; model/checkpoint terms remain separate | offline sparse + impact arm |
| SPLADE v2 / SPLADE++ | learned sparse / expansion | [SPLADE](https://github.com/naver/splade); [SPLADE paper](https://arxiv.org/abs/2107.05720); [SPLADE++](https://arxiv.org/abs/2205.04733) | repository states CC BY-NC-SA 4.0; non-commercial/share-alike risk excludes core dependency | offline research gate |
| SPLADE-v3 query expansion | query expansion / sparse | [Barron et al., 2026](https://arxiv.org/abs/2609.37911) | September 2026 preprint; evidence is not peer-reviewed and model terms remain separate | research candidate only; preserve original-query weight |
| BGE-M3 sparse branch | learned sparse / multilingual | [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding); [BGE-M3 paper](https://arxiv.org/abs/2402.03216) | FlagEmbedding MIT; checkpoint and commercial-use terms must be verified | multilingual offline screen |
| MaxScore / WAND / Block-Max WAND | pruning / exact top-K execution | [Broder et al., WAND](https://doi.org/10.1145/956863.956944); [Mallia et al., BMW](https://doi.org/10.1145/3077136.3080780); [PISA query index](https://pisa.readthedocs.io/en/stable/query_index.html); [PISA source](https://github.com/pisa-engine/pisa) | PISA Apache-2.0 | only after exhaustive parity oracle |
| VByte / Stream VByte | posting compression | [Lemire et al., Stream VByte](https://doi.org/10.1016/j.ipl.2017.09.011); [implementation](https://github.com/fast-pack/streamvbyte) | Apache-2.0 | component/storage arm |
| SIMD-BP128 / FastPFOR | posting compression | [Lemire & Boytsov, SIMD integer decoding](https://doi.org/10.1002/spe.2203); [FastPFOR](https://github.com/fast-pack/FastPFOR); [simdcomp](https://github.com/fast-pack/simdcomp) | FastPFOR Apache-2.0; simdcomp BSD-3-Clause | component/storage arm |
| INT8 / INT5 quantized impacts | scoring representation / compression | project experiment; compare against full-precision sparse impacts | algorithmic experiment; no third-party code or weights implied | INT8 first, INT5 only if calibration is stable |

The list intentionally includes more than one candidate in each axis, but it
does not imply that every candidate will receive a production implementation.
The project must not copy GPL, CC-BY-NC-SA, or undeclared model code into the
static library. A runner may invoke external tools in a separately documented
environment after a license and artifact audit.

## Applicability matrix

Ratings are planning judgments, not measured results. `High` means the method
fits the existing boundary with a bounded adapter; `Medium` means a new
artifact or contract is needed; `Low` means that the cost or lifecycle risk is
currently disproportionate.

| Family | Relevance opportunity | Memory / I/O risk | Runtime risk | Integration complexity | M0/M1/M2+ placement |
| --- | --- | --- | --- | --- | --- |
| BM25 variants, QL, DFR | Medium–High; useful on long, multilingual, or skewed fields | Low | Low | Low | research now; promotion only after qrels |
| Boolean, phrase, proximity | High for identifiers, commands, paths, and exact constraints | Medium (positions) | Low–Medium | Medium | code-aware M1/M2 slice |
| Fuzzy / n-gram | Medium for typos and partial names; high noise risk | Medium–High | Medium | Medium | focused M1/M2 experiment |
| DeepCT / uniCOIL / DeepImpact | Medium–High on vocabulary mismatch | Medium–High (impact postings) | High (inference and update cost) | High | M2+ optional offline adapter |
| SPLADE family | High semantic lexical recall potential | High unless pruned/quantized | High | High | M2+ optional external inference |
| BGE-M3 sparse | Potential multilingual and multi-signal coverage | High | High | High | M2+ optional external inference |
| MaxScore / WAND / BMW | No relevance change; can reduce exact top-K work | Low–Medium | Medium | Medium–High | M2 after exhaustive BM25F |
| VByte / Stream VByte / BP128 / FastPFOR | No relevance change; storage and decode locality | Low–Medium | Medium | Medium | M2 physical projection |
| INT8 / INT5 impacts | Compact learned-sparse postings | Medium | Medium–High (calibration/overflow) | High | research gate; never implicit default |

## Benchmark contract

### Arms and frozen controls

Every run freezes one corpus snapshot, tokenizer configuration, query order,
qrels, scope/access filters, candidate depth, final `K`, and tie rule. The
minimum arm set is:

1. `KeywordOverlapIndex` for tiny-KB deterministic lower-bound checks;
2. exhaustive `ExactLexicalIndex` BM25 on the declared frozen token stream
   (the mandatory scorer oracle; `Original.body` is a future projection arm);
3. classical alternatives (BM25+, BM25L, TF-IDF/pivoted, QL-Dirichlet,
   DFR-PL2/DPH) using the same token stream;
4. code-aware exact/phrase/proximity and optional fuzzy/n-gram arms;
5. each learned-sparse encoder and scorer independently, with inference time
   reported separately from posting traversal;
6. an exact dense control only when a frozen embedding/qrels artifact exists;
7. RRF only as a post-candidate fusion arm, never as a lexical prefilter;
8. MaxScore/WAND/BMW and compressed postings compared to the exhaustive oracle
   using the same scoring function and active statistics epoch.

The benchmark must declare its indexing unit. The current M0 lexical arm is one
durable lexical document per resource; the raw-publication arm is a separate
set of revision-bound `Chunk` units and must not be silently substituted for
the lexical document arm. Until `IProjectionStore` and projection freshness
contracts are implemented, runners may use in-memory records or a read-only
export, but must not invent a production projection schema.

### Query classes

The query set must contain labeled slices for:

- exact C++ identifiers and function/class names;
- file paths, version numbers, commands, flags, and error codes;
- rare terms and negative constraints;
- misspellings, prefixes, and partial identifiers;
- Russian inflectional variants and mixed Russian/English text;
- semantic intent without a literal term match;
- long documents and multi-section material;
- source update, stale-writer non-resurrection, and deleted-unit lifecycle
  cases;

Each slice needs qrels. If a slice has no qrels, it is a diagnostic only and
must be reported as incomplete rather than folded into a quality average.

The runner makes this boundary explicit per query. `judged` queries need one or
more positive qrels and are the only inputs to aggregate Recall, nDCG, and MRR.
`judged_zero_relevant` has one or more explicit zero-grade qrels and records a
completed negative relevance judgment without treating it as a recall zero.
`incomplete_qrels` has at least one judgment but is excluded from quality
aggregates; its presence makes the receipt's quality status partial. `unjudged`
has no qrels, `no_answer` has no qrels and is evaluated only by empty-result
accuracy, and `ignore` has no qrels and is omitted from evaluation. A repeated
`(query_id, doc_id)` pair is invalid even when its grade agrees, preventing a
fixture from silently accepting duplicate or conflicting relevance judgments.

### Metrics and timing boundaries

Quality:

- `Recall@1/5/10/50`, `nDCG@10`, and MRR;
- exact-fact hit rate for numeric/command/identifier probes;
- no-answer accuracy and empty-result fraction;
- top-K identity parity against exhaustive BM25 for execution-only arms;
- per-slice and aggregate results, never one pooled number alone.

Cost:

- query preprocessing and optional model inference;
- candidate search, posting visits, scored documents, and pruning rate;
- p50/p95/p99 query latency with one warmup and repeated query observations;
  percentiles use every query-repeat sample. Fewer than 100 observations are
  directional, 100--999 are descriptive, and at least 1000 are required for
  the receipt's `measured` status. `sample_count` is the number of
  query-repeat observations, not independent queries; `measured` describes
  only that sample distribution and does not guarantee a statistically stable
  p99;
- index build, update/reindex, and delete visibility time;
- logical posting bytes, physical bytes, decoded integers/positions, peak RSS,
  and model footprint;
- cold and warm procedures, compiler/build flags, CPU and storage medium.

Do not combine inference and search-engine time. For lifecycle runs retain the
boundaries `accepted -> durable_commit -> index_ready -> search_visible` and
record stale/deleted-unit non-resurrection. A clean score on a stale index is
not an exact retrieval result.

Every result must carry a compact manifest containing commit, corpus/query/qrels
hashes, tokenizer and chunking configuration, filter policy, index parameters,
candidate depth, final limit, concurrency, cache state, and whether encoding
time is included. Raw reports belong outside Git under the experiment workspace;
the note and a small receipt are the durable evidence.

### Evidence ladder

1. Contract and deterministic ordering tests.
2. Exhaustive scorer/oracle parity on a small fixture.
3. Component measurements (tokenizer, scorer, encoder, codec, decoder).
4. End-to-end retrieval on a versioned local corpus with qrels.
5. Lifecycle replay: warm/cold, update/delete, rebuild, restart, and failure.

Passing a lower rung does not promote a method to a higher rung. An
approximate/pruned path that cannot prove conservative upper bounds or exact
active membership must fail closed or use an exhaustive fallback.

## Top-three hypotheses and gates

These are preregistered investigation targets, not results.

### H1 — code-aware exact matching has the best near-term value

Identifier splitting, exact phrase/proximity, and path/symbol-aware fields will
improve the code/path/command slices over flat BM25 without requiring ML.

Disconfirming result: no qrels lift on those slices, or the extra positional and
field index cost dominates without a measurable latency/quality benefit. The
smallest discriminating check is a paired `Original.body` versus code-aware
run on identical records and qrels.

### H2 — WAND/BMW can reduce exact top-K work without changing answers

For a fixed BM25/BM25F formula, conservative MaxScore/WAND/Block-Max WAND
bounds will preserve ordered top-K identity against exhaustive scoring while
reducing posting visits or scored documents on sufficiently large posting lists.

Disconfirming result: any parity loss, stale-statistics candidate admission, or
no cost reduction after accounting for bound construction and decode. The check
must include same-epoch and changed-generation fixtures, with fail-closed or
full-flat fallback for incomplete overlays.

### H3 — compact learned sparse impacts are useful only for semantic slices

An offline learned-sparse arm (SPLADE/uniCOIL/DeepImpact family) will improve
non-literal semantic queries while remaining non-inferior on exact identifiers;
INT8, and only then INT5, may reduce impact bytes without unacceptable qrels or
exact-fact loss.

Disconfirming result: semantic lift is absent, identifier recall regresses, model
and update cost dominates, or quantization changes the top-K beyond the declared
quality gate. The first discriminating check is a frozen sparse artifact and
full-precision impact oracle; inference is timed and audited separately.

## Staged PR plan

Each stage is an independently reviewable PR and uses a fresh named worktree.
No merge is performed by the research agent.

| Stage / PR | Deliverable | Explicit exclusions |
| --- | --- | --- |
| PR1 — protocol and matrix (this note) | sources, applicability matrix, benchmark design, hypotheses, implementation order | no source/API/MDBX changes, no result claim |
| PR2 — isolated runner | read-only/in-memory runner, separated build/query timing, stable corpus/qrels manifest and smoke fixture | no public API, no mandatory ML, no production layout |
| PR3 — C++ parity | tokenizer contract, scorer parity, diagnostic tokenizer delta, and lifecycle coverage audit | no algorithm bake-off, no production API changes |
| PR4 — classical and code-aware bake-off | BM25 variants, QL/DFR, identifier/phrase/proximity/fuzzy/n-gram diagnostics | no learned model dependency |
| PR5 — execution and compression | impact postings, MaxScore/WAND/BMW, VByte/Stream VByte/BP128/FastPFOR; exhaustive parity and epoch negatives | no promotion without exact oracle and lifecycle checks |
| PR6 — learned sparse and quantization | offline SPLADE/uniCOIL/DeepImpact-compatible artifacts, INT8 then INT5 impacts | no model in C++ core or MDBX production schema |
| PR7 — decision record | consolidate evidence, reject/promote candidates, recommend post-M0 integration order | no integration before `KnowledgeUnitStore`/`IProjectionStore` stabilization |

Recommended post-M0 order is therefore: code-aware exact/phrase support, then
exhaustive fielded BM25F, then execution/compression, then learned sparse only
if H3 passes. The default scorer remains BM25 until a controlled comparison
demonstrates a better fit for the target corpus and operating budget.

## Current result and limitations

Actual result: `not checked` — this PR contains no benchmark run. The matrix is
an investigation map, not an evidence release and not a production decision.

Limitations include the unfinished projection-aware M0/M1 lifecycle, the
separate lexical-document and raw-chunk publication units, incomplete local
qrels for Russian and semantic slices, unknown model-weight terms for some
external checkpoints, and the absence of a learned-sparse/WAND runner in the
current tree. Those gaps are why the next PRs are parity and methodology gates
rather than production integration.

## Follow-up checks

- freeze a small, stable code/path/Russian mixed corpus and qrels manifest;
- implement the read-only runner with the existing `RetrievalEvalRunner` and
  `BenchmarkReport` contracts;
- verify scorer parity against `ExactLexicalIndex` on exported C++ token
  streams before adding alternatives;
- audit existing MDBX fixtures for pending visibility, manifest bindings,
  scope isolation, recovery, and stale-writer non-resurrection before adding
  lifecycle tests;
- obtain explicit license/model terms before downloading any learned-sparse
  checkpoint;
- append measured results to this research line rather than replacing this
  protocol note.

## PR2 runner smoke (2026-10-20, refreshed after M0 publication)

Context: `research/lexical-alternatives-runner`, originally rebased onto the
historical M0 publication baseline `1e221eedf41860c65685dbd3b184922ae68b1d3a`.
The current stacked branch is based on the restacked PR1 above. This is a
harness check, not an algorithm comparison.

Command:

```text
py -3 tools/lexical-research/run_lexical_protocol.py \
  --dataset tools/lexical-research/fixtures/lexical_protocol_smoke.json \
  --self-test
```

Expected result: the fixture exercises an exact identifier, a code-oriented
multi-term query, a Russian query, and an explicit no-answer query. The smoke
must preserve the expected top document for the first three and return no hits
for the no-answer case.

Actual result: `verified` at the runner commit recorded by the PR. The self-test passed with
`Recall@1/5/10/50 = 1.0`, `nDCG@10 = 1.0`, `MRR = 1.0`, and no-answer accuracy
`1.0`. The receipt now records a one-time index-build measurement separately
from query preprocessing, scoring, and end-to-end timing. `--limit` is the
candidate depth and defaults to `50`; a lower depth marks Recall@50 as
truncated instead of presenting it as a complete measurement. A separate
three-repeat receipt was written outside Git under
`.artifacts/lexical-protocol-smoke.json`; its p50/p95/p99 values are local
Python directional timings (the smoke has 15 raw query-repeat observations)
and are not performance evidence. The receipt's `sample_count` is explicitly
`query_count * repeat_count`; repeats of the same query are not independent
queries. The `measured` label describes the observed sample distribution and
does not assert p99 stability. The backward-compatible per-query `latency_ms`
field remains the median of that query's repeats.

Interpretation: PR2 now provides a small, hash-bound corpus/qrels input and a
portable BM25 control for subsequent scorer plugins. It does not establish
parity with the C++ `ExactLexicalIndex`; the runner README and PR3 gate require
that comparison before any quality or latency claim.

Limitations: the fixture is intentionally tiny, uses one frozen tokenizer
profile, has no stale-posting or update replay, and contains no learned-sparse
artifact. Model inference, compressed postings, WAND/BMW, and MDBX remain
unmeasured.
