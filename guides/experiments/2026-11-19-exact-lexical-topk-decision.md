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

Fine-grained end-to-end phase timing is `NOT_MEASURED`: this instrument does
not insert timers into candidate generation, BM25 scoring, filtering, metadata
copying, or output construction. Isolated selection samples cannot be
subtracted from complete search to manufacture a phase breakdown. A separately
controlled stage-profile experiment remains a follow-up, not a completed gate.

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
