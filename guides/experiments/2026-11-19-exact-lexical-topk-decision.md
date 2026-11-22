# Exact lexical top-k decision: production search and selection costs

Date: 2026-11-19. Status: research instrument; no production recommendation.
Starting baseline: `bfa01a22d4091bb5c5f7c17606199fda6342b790`.

## Question and boundary

Does a cheaper top-k selector matter once `ExactLexicalIndex::search()` includes
BM25 scoring, metadata filtering, result metadata construction, and allocations?
This instrument compares all four selectors inside the same real end-to-end
search implementation, with the unmodified production class as the full-sort
control. It also retains separate isolated selection measurements over actual
scored `LexicalSearchResult` values. It changes neither production source files
nor retrieval defaults. A selector-only timing is not an end-to-end speedup.

This is an in-memory index. There is no persistent storage, MDBX transaction,
disk read, network request, or I/O latency in either measurement scope. Token
construction and index import are outside query timing; import duration and
allocation totals are recorded separately. Query construction and correctness
checks are also outside query timing.

## Source-bound matched search variants

The build-time generator pins normalized SHA-256 of the production
`ExactLexicalIndex.cpp` and `.hpp`, checks unique selection/header anchors, and
generates three private class-renamed copies. Only the final selection block
changes; tokenized-record import, validation, scoring, filtering, candidate
construction, metadata copying, and output truncation remain the same source.
Header/class/options identities are renamed to avoid ODR collisions. No
production strategy hook or public API is added. Generation fails if either
pinned production source changes; that requires an explicit transform review.

Each variant owns a separately populated but identical index. Index construction
and import are recorded separately for every variant. All end-to-end calls use
the same `ILexicalIndex` virtual-dispatch boundary. The generator self-test
reverses the controlled substitutions to recover the original implementation
and rejects changed implementation/header inputs. The receipt records the
generated source manifest, which the validator independently reproduces.

## Frozen fixture and comparison

The deterministic corpus sizes are 128, 1,024, 8,192, and 65,536 chunks. Each
chunk has three tokens, two metadata entries (`scope` and `tenant`), a stable
resource revision, and a distinct chunk identity. One fifth of chunks have
`scope=private`; every query applies `scope=public`. The `common` term occurs
twice everywhere; `anchor` occurs in every seventh chunk.

The words `public`/`private` are synthetic metadata values, not access-control
decisions. Authorization, tenant isolation, and resource-revision freshness
belong to higher retrieval boundaries and are explicitly `NOT_EXERCISED` in
this receipt. Static revision fields and metadata equality do not prove those
guarantees. Only the actual metadata predicate and returned-value parity are
tested at this index boundary.

At each corpus size the cases are mixed scores with K=16, ties with K one
below the filtered candidate count, an absent term/empty result, and K=0.
Production K=0 returns immediately by its existing contract; kernel K=0 is
reported separately and is not assumed to have the same work.

The selector variants are full sort, partial sort, nth-element followed by
sort, and bounded heap. All use score descending, then `ChunkId` ascending,
including ties. The oracle is the full-sort result over the actual production
scored candidates. Equality includes score, identity, metadata, resource,
section, enrichment level, and result tier. This is a selection oracle, not an
independent proof of BM25 scoring correctness.

The production full-result query first materializes the candidates outside
kernel timing. Its ranked output is restored to ascending chunk identity,
matching the production index map traversal before selection. Thus selector
measurements do not accidentally receive already-ranked mixed-score input.
Every isolated selector pays for its own result copy or heap construction, with
actual metadata-bearing values; scoring/filtering are not repeated inside the
kernel. These copies are not added to the matched end-to-end variants: there,
selection acts directly on the vector built by the unchanged production scoring
loop. The heap variant still materializes that complete vector before selection;
this experiment does not introduce streaming scoring/materialization.

## Timing and accounting

Each case has two excluded warmups and eight retained complete-operation
samples per variant and timing scope. Both matched end-to-end search and
isolated selection rotate their four variants cyclically within the same
fixture on each repeat, giving each variant every position twice in each
scope. Raw samples and exact execution orders are retained separately. The
complete matrix has 16 cases and 1,024 measured operation samples. End-to-end
and isolated measurements are not interleaved as if the scopes were equal.

Fine-grained end-to-end phase timing in the preserved v1 receipts is
`NOT_MEASURED`: that instrument did not insert timers into the search stages.
Isolated selection samples cannot be subtracted from complete search to
manufacture a phase breakdown. The dated v2 follow-up below adds a separate
directly instrumented scope; it does not relabel the historical v1 measurements.

The executable counts C++ `new`/`new[]` calls and requested bytes inside each
timed operation, including aligned allocations. This is not retained bytes,
RSS, or peak memory, and excludes allocations performed directly through C
allocation APIs. Selector wrapper copy/move counters count whole candidate
operations, not every nested string or metadata-node operation. Production
copy/move totals are not claimed; allocation totals and materialized result
metadata counts are the observable production accounting.

Counters perturb timings. Eight repetitions of fixed synthetic queries are
not independent user requests and do not establish a stable p99. The expected
outcome is exact result parity plus a bounded view of where selection cost sits,
not a universal fastest selector or a production activation decision.

## Reproduction and evidence

```sh
python -m pip install -r tools/requirements-tools.txt
cmake -S . -B build-lexical-topk -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_BUILD_BENCHMARKS=ON -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=ON \
  -DAGENT_MEMORY_BUILD_EXAMPLES=OFF -DAGENT_MEMORY_ENABLE_MDBX=OFF \
  -DAGENT_MEMORY_LEXICAL_TOPK_EXPECTED_PR_HEAD_SHA="$(git rev-parse HEAD)"
cmake --build build-lexical-topk --config Release \
  --target agent-memory-exact-lexical-topk-decision
ctest --test-dir build-lexical-topk --build-config Release --output-on-failure --no-tests=error \
  -R '^agent_memory_exact_lexical_topk_decision_.*self_test$'
build-lexical-topk/tools/agent-memory-bench/agent-memory-exact-lexical-topk-decision \
  --output build-lexical-topk/receipt.json
(cd build-lexical-topk && sha256sum receipt.json > receipt.json.sha256)
python tools/agent-memory-bench/validate-exact-lexical-topk-decision.py \
  build-lexical-topk/receipt.json --expected-head "$(git rev-parse HEAD)" \
  --actual-checkout "$(git rev-parse HEAD)" --source-root . \
  --sha256-sidecar build-lexical-topk/receipt.json.sha256
```

The receipt distinguishes expected PR HEAD from the actual compiled checkout
(including GitHub's synthetic merge checkout). CI supplies the former through
`AGENT_MEMORY_LEXICAL_TOPK_EXPECTED_PR_HEAD_SHA`; the latter is compiled at
configure time. Reconfigure after switching commits. Source manifest SHA-256
is recomputed from actual benchmark/lexical/metadata source bytes by the
validator, independently of the declared Git SHA. Compiler, version, generator,
and configured build-environment digest are recorded. The fixture is frozen in
the benchmark source, which is included in that manifest.

The research CI lane has Ubuntu/GNU and Windows/MSVC matrix arms, each of which
builds and executes this actual benchmark rather than relying on a generic
core build. Each arm runs three self-tests, the complete matrix, receipt
validation, and SHA-256 sidecar validation, then uploads the raw receipt and
sidecar. A required failure fails the common CI gate. Negative controls cover
wrong HEAD/checkout, malformed digest, non-execution, result parity, missing
variant, invalid samples, unbalanced order, wrong K, metadata accounting, and
receipt byte corruption. Pinned-source transformation adds three further
negative controls. Generated variants and raw reports are not committed.
The C++ controls also exercise an actually empty production index, absent
metadata, no filters, and rejected invalid import with unchanged existing
results for all four index implementations.

## Results and next decision

Execution results are retained in each platform's source-bound CI artifact.
The initial Linux pilot below does not substitute for the later Windows lane.
Before considering a
production selector change, independently review those matched end-to-end
artifacts and repeat with representative token lengths, metadata sizes,
selectivities, and query distributions. The present fixed three-token corpus
and one filter cannot establish those broader claims.

## Initial Linux pilot, preserved historical receipt

The initial Ubuntu/GNU 15.2.0 Release run used benchmark commit
`7a99add27e71fbe5010ac43443a97a4c8f2b2545`, synthetic merge checkout
`9724be450f6ff436652066deacf6f8ea338fc55c`, and
[CI run 44614602649](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44614602649).
Its `exact-lexical-topk-decision` artifact contains a validated receipt with
SHA-256 `55ca25cd96095a29c11401dc28d3ce7a3a594c45fe89a0345484676275c89097`.
This is the receipt from before adding explicit Windows execution; its source
bindings and results are not rewritten to a later HEAD.

Median complete search time in milliseconds at 65,536 documents (eight samples
per variant; directional synthetic evidence only):

| Case | Full sort | Partial sort | Nth-element + sort | Bounded heap |
| --- | ---: | ---: | ---: | ---: |
| Mixed scores, K=16 | 36.7489 | 24.7793 | 25.5771 | 24.8523 |
| Ties, K=52,427 of 52,428 candidates | 21.1164 | 31.1728 | 21.5933 | 36.1097 |
| No matching term | 12.3767 | 12.5627 | 12.5556 | 12.5132 |

All 16 cases/1,024 samples satisfied selection and returned-value parity.
The observed reversal between small K and K near N argues against declaring
one universal winner from this fixture. It does not establish production
latency, phase attribution, a stable tail, or an activation recommendation.

## 2026-11-20 R5 closure: direct stages and bounded workload profiles

The v2 receipt retains the original 16-case uninstrumented search/kernel matrix
and adds four explicitly `SYNTHETIC` fixture recipes with three query cases
each. The committed `lexical-topk-stage-profiles.json` varies 3/16/128 tokens
per document, 2/8/16/32 metadata fields, common and rare terms, 100%/50%/10%
filter selectivity, repeated equal scores, small K, K near the candidate count,
and empty results. These are controlled stress profiles, not measured user
workload frequencies. The recipe SHA-256 covers its exact committed bytes, not
a claimed hash of materialized corpus bytes; corpus expansion is bound by the
separate source manifest.
Each profile additionally records `materialized_corpus_sha256` over ordered
compact JSON-lines containing chunk/resource/revision, token texts in position
order, and metadata. Token ranges/kind follow the fixed generator recipe.
The existing library SHA-256 accumulator hashes actual population input; Python
independently expands the recipe and reproduces every corpus digest. All eight
instrumented/uninstrumented arms must receive identical corpus bytes.

Four additional class-renamed copies instrument the same pinned source.
Reversing all declared timer/traversal substitutions and the selector change
must recover the original source exactly. No production API, strategy hook or
storage format is added. The original three uninstrumented variants remain
the latency comparison, and their measurements are retained independently.

The directly measured intervals are:

| Stage | Actual interval |
| --- | --- |
| Candidate generation | Map iterator dereference/increment for each scanned record; `begin()` and loop-condition overhead remain unattributed. This index has no postings candidate generator. |
| Metadata filtering | Actual `matches_metadata_filters()` call before scoring. |
| BM25 scoring | Actual `score_record()` call for every metadata-eligible record. |
| Candidate construction | Hit fields, metadata copying and candidate-vector insertion/growth. |
| Top-K selection | Actual selector, including the heap's additional vector and moves. |
| Result materialization | Final vector truncation and destruction of discarded hits only; the earlier metadata copies belong to candidate construction. Returning the already-built vector does not create another full copy phase. |
| Full search | Enclosing function interval, including validation and timer overhead. |

Raw nanosecond samples and invocation counts are recorded directly. They are
not a subtraction of separate kernel timings. Every record adds clock calls;
especially cheap stages are heavily perturbed. Instrumented totals are therefore
not interchangeable with uninstrumented latency. The validator checks that
disjoint stage intervals fit within their enclosing full-search interval and
that call counts match an independently expanded recipe. Missing stages,
tampered timings, changed recipes, wrong provenance, parity failures and
invented workload distributions fail validation.

The actual `HybridRetrievalEngine` is exercised over every uninstrumented
selector, using both `scope=public` and `tenant=alpha` metadata predicates,
contradicting filters and trusted source/metadata preservation. This proves
filter propagation and result preservation, not authoritative access control.
`RetrievalRequest` has no authorization or pinned-revision context;
`RevisionBoundRetrieval` uses a different unit/projection boundary and does not
consume `ILexicalIndex`. The unified authorization, tenant-authority and revision
freshness integration remains `NOT_EXECUTED`: implementing the missing executor
would exceed this research slice and overlap the storage/execution workstream.
See `retrieval-execution-roadmap.md` for the hard-filter/frontier contract and
`tests/retrieval/revision_bound_retrieval_test.cpp` for the separate existing
revision-bound controls. Metadata predicates must not be promoted into a
security/freshness guarantee.

The C++ self-test also checks profiled/production parity and K=0 timer behavior.
Current validators retain v1 schema support without modifying old receipts;
v2 requires the new profile and boundary evidence. CI executes the expanded
benchmark on GNU/Linux and MSVC/Windows and retains generated source files with
the receipt and its sidecar. Peak RSS and retained workspace remain
`NOT_MEASURED`; requested allocation totals are not those measurements.

Decision remains `CONDITIONAL` for the selector experiments and `KEEP CURRENT`
for production defaults. Without a representative query distribution and the
unified product-boundary qualification, these synthetic profiles do not justify
an implementation PR or dispatch threshold.

### Executed closure pilot

The clean local source/expected/compiled revision was
`1c062ba7bd8ac3b4bf68af03e8f6689731e795cb`: GNU 15.2.0, Ninja, Release,
C++17, Windows/AMD64, 64-bit pointers. It is not a Linux or MSVC result.
Receipt SHA-256 is
`85cfc08c07cbd561c9b6e4651d7e4b9a247a6549a89cb5963a9bec141f1384aa`.
The local raw receipt/sidecar remain under the canonical checkout's
`.build/r5-closure-lexical/receipt-1c062ba7.json[.sha256]`; this locator is not
claimed as a publicly archived artifact. Public source-bound reruns, including
their generated sources, are retained by each exact-head CI matrix arm.

All 16 legacy cases plus the 12 profile cases passed source/generated-source,
independent materialized-corpus digest, exact output parity, timer call-count,
interval and receipt-sidecar checks. The three targeted CTests passed, including
37 validator rejection controls and the three pinned-source rejection controls.
The complete run retained 1,792 operation samples across its distinct scopes;
these are repetitions of fixed fixtures, not 1,792 independent user requests.

Uninstrumented complete-search p50, milliseconds, eight samples per arm:

| Synthetic profile / N / K | Full sort | Partial sort | Nth + sort | Heap |
| --- | ---: | ---: | ---: | ---: |
| Short narrow / 1,024 / 16 | 1.2691 | 0.9846 | 1.0310 | 1.0079 |
| Long wide / 1,024 / 16 | 8.7550 | 8.2462 | 7.9217 | 9.1907 |
| Rare selective / 8,192 / 16 (9 candidates) | 3.7717 | 3.7058 | 3.8680 | 3.8156 |
| Long repeated ties / 1,024 / 1,023 | 4.8015 | 5.2260 | 4.7882 | 5.2397 |

For the separately instrumented full-sort arm, the rare/selective small-K case
directly recorded p50 filtering 3.6456 ms and selection 0.0018 ms; the long/wide
case recorded candidate construction 3.9249 ms and selection 0.1087 ms. These
are timer-perturbed stage observations, not a decomposition of the table's
uninstrumented medians. The latter also enables allocation counters, whereas
the direct-stage scope records clock intervals without those counters. Separate
phase medians must not be added or subtracted as if they were one sample.

The observed regime changes support the existing conditional decision. A faster
selector cannot remove scanning, filtering or metadata construction costs, and
the same heap is not uniformly best even at small K. This shared-host synthetic
pilot does not establish a production threshold or stable tail latency.

## 2026-11-21 integration maintenance

Search integration extracts the existing float BM25 formula into
`bm25_term_score()` in `Lexical.cpp`, adds explicit arithmetic validation and
removes the private duplicate average-length helper. The generator pins now
track those production sources. Selection, class renames and explicit stage
transforms are unchanged; their reversal and rejection controls remain required.
The evaluator source manifest already includes `Lexical.cpp` and `Lexical.hpp`,
so the shared scorer remains source-bound.

This is instrument compatibility maintenance, not a new performance result.
Historical raw receipts, generated-source hashes and measured revisions above
remain unchanged and must be replayed from their historical checkout. New CI
receipts belong only to their actual checkout and expected PR HEAD.
