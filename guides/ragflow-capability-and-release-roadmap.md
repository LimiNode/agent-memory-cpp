# RAGFlow capability comparison and release roadmap

**Status:** documentation-only architecture audit, based on `main` at
`418e122e4e4f120cd5bee8bb799ec248af71fbe2`.

This guide records which ideas observed in RAGFlow are useful for
`agent-memory-cpp`, which are already covered by existing contracts, and which
remain future work. It is not an implementation claim and does not activate
production retrieval, OCR, ASR, an LLM, or an agent runtime.

The comparison uses the public RAGFlow project and release documentation as
external design input:

- [RAGFlow repository](https://github.com/infiniflow/ragflow)
- [RAGFlow documentation](https://ragflow.io/docs/)

For reproducibility, the external baseline is RAGFlow `v1.0.0`, tag commit
[`db1cd5929a7885a3074547791f5ecc4d2f891739`](https://github.com/infiniflow/ragflow/tree/db1cd5929a7885a3074547791f5ecc4d2f891739),
released on 2026-11-06. The source and documentation points used by this audit
are:

- [v1.0.0 release notes](https://github.com/infiniflow/ragflow/releases/tag/v1.0.0);
- [chunk parsing and knowledge fragments](https://github.com/infiniflow/ragflow/blob/v1.0.0/docs/guides/dataset/chunk_parsing_results_and_knowledge_fragment_management.md)
  and [chunker configuration](https://github.com/infiniflow/ragflow/blob/v1.0.0/docs/guides/agent/ingestion_pipeline/configure_chunker_component.md)
  for chunk/parent context behavior;
- [parser component configuration](https://github.com/infiniflow/ragflow/blob/v1.0.0/docs/guides/agent/ingestion_pipeline/configure_parser_component.md)
  and the tagged [DeepDoc PDF parser](https://github.com/infiniflow/ragflow/tree/v1.0.0/internal/deepdoc/parser/pdf)
  for external layout/parser evidence;
- [Knowledge Compilation overview](https://github.com/infiniflow/ragflow/blob/v1.0.0/docs/guides/knowledge_compilation/overview.md)
  for compilation behavior;
- the tagged [agentic evidence registry](https://github.com/infiniflow/ragflow/blob/v1.0.0/internal/agentic_rag/evidence_registry.go)
  and [compiled evidence implementation](https://github.com/infiniflow/ragflow/blob/v1.0.0/internal/agentic_rag/compiled_evidence.go)
  for evidence handling;
- the tagged [DeepDoc table parser](https://github.com/infiniflow/ragflow/tree/v1.0.0/internal/deepdoc/parser/pdf/table)
  for table/layout behavior.

These links establish what the tagged external project documents or implements;
the ADAPT/DEFER decisions below are this project's architectural proposals and
are not claims about RAGFlow internals.

The repository's source-level evidence remains authoritative for this project:
[`first-release-profile.md`](first-release-profile.md),
[`retrieval-roadmap-coverage.md`](retrieval-roadmap-coverage.md),
[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md),
[`canonical-content-storage-roadmap.md`](canonical-content-storage-roadmap.md),
and [`retrieval-execution-roadmap.md`](retrieval-execution-roadmap.md).

This audit is evaluated against the merged baseline containing #601 (the
bounded persistent lexical candidate parity seam), #602 (the source-bound
release qualification contract), and #603 (the executable article-to-citation
vertical). #601 and #602 are integration inputs, not open dependencies of this
documentation PR. Their presence does not promote contract-only release cases
or the optional candidate adapter to default production activation.

## 1. What #603 and the Raw Markdown slice actually deliver

PR #603 is an executable article-to-citation conformance slice. Its scope is
smaller than a complete retrieval and hydration subsystem:

```text
normalized Markdown
  -> StructuralSegmentSet
  -> Original projection publication
  -> snapshot-consistent lexical admission
  -> ResourceRevision -> SourceRevision -> ArtifactBinding join
  -> verified Raw CAS range materialization
```

The merged code provides the typed persisted join
([`ResourceArtifactBinding`](../src/agent_memory/storage/ResourceArtifactBinding.hpp)
and
[`MdbxResourceArtifactBindingStore`](../src/agent_memory/infrastructure/mdbx/MdbxResourceArtifactBindingStore.hpp))
and an MDBX regression test
([`mdbx_article_to_citation_vertical_test.cpp`](../tests/infrastructure/mdbx/mdbx_article_to_citation_vertical_test.cpp))
covering `A@1 -> A@2`, stale-candidate rejection, reopen, digest mismatch, and
missing CAS bytes. The test composes the publication and admission operations
itself. `MdbxRawMarkdownProductIngestion` now supplies a bounded host-facing
composition around those authorities for original `.md`/`.markdown` input:
it derives revision-bound source/binding identities, performs idempotent
catalog/join publication, admits a lexical candidate and calls the
authoritative hydrator. This is an experimental Raw Markdown path, not a
universal `Hydrated` API or a physically atomic cross-store transaction.

The following are deliberately **not** implemented by #603:

- general authoritative admission-plus-hydration beyond the Raw Markdown
  slice;
- atomic publication of manifest, projection, join and CAS retention;
- crash recovery, backup/restore and retention collection for the whole path;
- general table, image, Telegram or timed-transcript ingestion;
- derived Summary/Concept publication;
- production retrieval activation.

In particular, `Current` and a successful materializer lookup must not be
combined mechanically into `Hydrated`. Hydration is valid only when both
statuses refer to the same trusted source revision, scope, locator and digest.

## 2. Source-level comparison

The table distinguishes an external RAGFlow capability from the closest
existing project boundary. “Implemented” means executable project code, not a
roadmap statement or a DTO-only test.

| Capability observed in RAGFlow | Closest agent-memory-cpp source | Current status | Decision |
|---|---|---|---|
| Parser/layout pipeline for PDF, scans and office documents | External connector boundary; [`StructuralSegmentSet`](../src/agent_memory/ingestion/StructuralSegmentSet.hpp) and [`TypedRepresentation`](../src/agent_memory/domain/TypedRepresentation.hpp) | Contract/experimental text path only; no parser dependency in the core | **ADAPT** as an interchange contract; **DEFER** parser implementations |
| Parent-child chunks and parent-context retrieval | [`StructuralSegmentSet`](../src/agent_memory/ingestion/StructuralSegmentSet.hpp), typed locators, retrieval execution roadmap | Not a product path; child identity and bounded context policy are not yet wired to retrieval | **ADAPT** after authoritative hydration |
| Table/image context windows | [`TableRepresentation`](../src/agent_memory/domain/TypedRepresentation.hpp), artifact/provenance contracts, canonical content roadmap | Contracts exist, but no general enrichment or exact cell/image product vertical | **ADAPT** with retrieval enrichment separate from canonical bytes |
| Knowledge Compilation / derived articles | `Summary`, `Fact`, `CompiledArticle`, linked-derived-knowledge contract | Payload concepts and provenance contracts exist; durable multi-input producer path is incomplete | **ADAPT**, using the existing publication substrate |
| Agentic evidence sufficiency evaluation | `ContextBuilder` and evaluation guides | Evaluation contracts are documented; no universal evaluator in the core | **ADAPT** at host/evaluation layer |
| OCR, ASR and multimodal extraction | Artifact/provenance and embedding adapter boundaries | No OCR/ASR runtime in the library | **DEFER** to external connectors |
| Complex tables and merged-cell semantics | `TableRepresentation` and typed locators | Simple table contract only; merged cells and header relationships are missing | **ADAPT** as a later additive representation revision |
| Graph/navigation views | Relation and linked-derived-knowledge guides | Optional views; manifest/provenance remains authoritative | **REJECT** graph-as-source-of-truth |
| Universal orchestration or planning loop | Host/runtime integration roadmap | Explicitly outside the static library | **REJECT** in core |

The comparison does not import RAGFlow's service topology into this library.
The core remains a C++17 static library with storage, indexing, retrieval,
ingestion and context contracts separated from host orchestration.

## 3. Capability inventory and release boundary

The following status vocabulary is normative for this audit:

- `IMPLEMENTED` — executable code and a relevant conformance test exist;
- `PARTIAL` — a bounded vertical or adapter exists, but the product contract is
  incomplete;
- `CONTRACT_ONLY` — interfaces or design rules exist without a complete path;
- `ROADMAP_ONLY` — planned, with no executable implementation;
- `DEFERRED` — intentionally outside the first release or core library.

| Capability | Status at current `main` | Release classification | Evidence / next bounded gate |
|---|---|---|---|
| Raw and normalized article storage | `IMPLEMENTED` for the bounded importer/storage slices | Required foundation | `MdbxResourceImporter`, canonical-content tests |
| Article structural segments | `PARTIAL` — executable in #603's vertical test | Release gate, not general ingest yet | Move composition into a product ingestion path |
| Revision-bound lexical admission | `IMPLEMENTED` for the existing MDBX slice ([`RevisionBoundRetrieval`](../src/agent_memory/retrieval/RevisionBoundRetrieval.hpp)) | Required foundation | snapshot/frontier and current-read conformance tests |
| Persistent lexical candidate parity | `IMPLEMENTED` as the bounded #601 adapter; candidate scoring remains term-frequency-only and the adapter is not default-enabled | Required foundation, not production activation | share a scorer and transaction-bound composition before any serving claim |
| Typed resource/artifact join | `IMPLEMENTED` | Required foundation | #603 join store and reopen regression |
| Authoritative source-backed hydration | `PARTIAL` — bounded Raw Markdown product path with installed consumer; general/multimodal API absent | Release blocker | extend revision-and-locator conformance beyond Markdown |
| Table cell identity and exact cell hydration | `CONTRACT_ONLY` | Release blocker | S0 table fixture and typed cell locator gate |
| Retained Telegram image vertical | `ROADMAP_ONLY` | Release blocker | retained-image bytes, update/stale/reopen fixture |
| Timed transcript evidence | `ROADMAP_ONLY` | Release blocker | timed SegmentSet, alignment provenance and exact read |
| Derived Summary/Concept publication | `CONTRACT_ONLY` | Release blocker | exact input revisions, producer recipe and stale propagation |
| Crash-safe publication/recovery and retention | `CONTRACT_ONLY` | Release blocker | staged publication, reopen, backup/restore and collection matrix |
| Installed external C++17 consumer | `PARTIAL` — packaging lanes exist | Release blocker | clean external consumer reproducing the supported profile |
| Parent-child retrieval/context expansion | `ROADMAP_ONLY` | Post-release enhancement | bounded child/parent fixture after hydration |
| Table/image retrieval enrichment | `CONTRACT_ONLY` | Post-release enhancement | separate derived projection with immutable evidence anchors |
| Evidence sufficiency evaluator | `CONTRACT_ONLY` | Post-release enhancement | host-level evaluator with supported/missing/conflicting states |
| Layout-aware parser adapters | `CONTRACT_ONLY` | Post-release enhancement | versioned parser interchange fixture |
| Merged-cell semantics | `ROADMAP_ONLY` | Post-release enhancement | additive table representation revision |
| Knowledge compilation profiles | `CONTRACT_ONLY` | Post-release enhancement | shared producer contract and rebuild fixture |

The green CI for #603 does not promote any `PARTIAL`, `CONTRACT_ONLY` or
`ROADMAP_ONLY` row to `IMPLEMENTED`.

## 4. Decisions for RAGFlow-inspired mechanisms

### 4.1 Parent-child retrieval and structural expansion

Adopt the idea, but keep it bounded and revision-aware. A candidate child and
its expanded parent context must share one admitted source revision, scope and
frontier. The expansion budget must be explicit in bytes, segments and tokens.
The citation remains attached to the original typed locator; expansion is a
read-time context view and does not rewrite canonical bytes.

Two approaches remain possible:

1. retrieval-time expansion from an admitted child to its parent structure;
2. a versioned context-enriched search projection.

The first is the default recommendation because it avoids duplicating
canonical text and keeps invalidation local. A projection is justified only by
measurements showing that bounded read-time expansion is insufficient.

Identity is structural, not textual: identical child text does not imply the
same child segment, and identical parent text does not imply the same parent.
A segment identity must include the permitted `RepresentationId`,
`SegmentSetId` and structural coordinates/locator, together with its source
revision and scope. Text hashes may be integrity evidence, but are not a
primary identity key.

The future conformance slice must include negative regressions for:

- identical child text in two sections of one document;
- identical parent text in two documents in one scope;
- identical content in different scopes;
- a source-revision update;
- rechunking that creates a new `SegmentSet` while preserving historical
  evidence anchors.

Each case must prove independent parent relationships, exact source locators,
freshness rejection and absence of cross-scope substitution.

### 4.1.1 Context-expansion evaluation gate

Before selecting a production mechanism, compare these three modes on the same
corpus, queries and admissible source revisions:

1. baseline retrieval without expansion;
2. retrieval-time parent/neighbor expansion;
3. a versioned context-enriched search projection.

The modes must use the same scope and freshness rules and the same final
context byte/token budget. The receipt must report retrieval quality (Recall@k
and nDCG@k where qrels apply), exact citation correctness, context bytes and
tokens, read latency, memory cost, stale/cross-scope rejection, and segment
identity preservation. No mode is declared superior in advance. A projection
is admitted only when its measured gain justifies its write, storage and
invalidation cost.

### 4.2 Table and image context windows

Keep two independent mechanisms:

- retrieval enrichment, a derived searchable projection containing heading,
  header and nearby explanatory context;
- context expansion, a post-retrieval read of neighboring canonical segments.

For tables, preserve cell/row/column identity, heading path, header context and
an exact cell locator. For images, retain an `ArtifactBinding` when bytes are
kept, or an explicit external-only reference when they are not. Context text
must never become proof that image bytes were retained.

### 4.3 Layout-aware parser interchange

Treat DeepDoc, Docling, MinerU and similar systems as external producers. The
future interchange record needs page/slide identity, region coordinates and
coordinate system, reading order, layout type, confidence, parser/model
fingerprint, normalized UTF-8 byte-span mappings, and diagnostics for unmapped
or ambiguous regions. OCR engines and parser runtimes are not release
dependencies of the C++ core.

### 4.4 Evidence sufficiency

Separate retrieval completeness from evidence sufficiency. The future evaluator
belongs in `ContextBuilder` or host integration and should classify claims as
supported, unsupported, conflicting or unknown, with provenance and coverage
limits. BM25 score, hit count or retrieval completeness alone is not evidence
sufficiency.

### 4.5 Complex tables

Extend `TableRepresentation` additively for merged cells, row/column spans,
header relationships and typed cell values. Recognized text remains distinct
from the canonical typed value. This is a later representation revision and is
not part of the current documentation PR's implementation.

### 4.6 Knowledge Compilation

Do not create a parallel knowledge system. Map RAGFlow-style compilation onto
the existing `Summary`, `Fact`, `QA`, `CompiledArticle`, `DomainMap`,
`Relation` and Procedure/Skill concepts. Every future derived producer must
record:

- exact input revision set;
- producer identity;
- versioned recipe/model/config;
- output identity and publication generation;
- evidence lineage;
- stale invalidation behavior;
- reconstruction and rebuild semantics.

## 5. Phase 2 engineering specification (planning only)

The next functional PR should implement **authoritative source-backed
hydration**. This section is a specification, not permission to implement it
in this documentation PR.

The API must consume a trusted admission context produced by MDBX;
caller-provided `current` flags, revisions or digests are not authority. The
context may carry the admitted frontier, resource generation, source revision,
binding identity and digest, but it is an evidence record, not a portable live
snapshot token. The current
[`RevisionBoundRetrieval`](../src/agent_memory/retrieval/RevisionBoundRetrieval.hpp)
adapter ends its read transaction before returning a result, so a returned
`Current` cannot by itself authorize a later CAS read.

The public `Hydrated` contract should therefore use **CurrentAtCompletion** as
its linearization point: after resolving
`ResourceRevision -> SourceRevision -> ArtifactBinding`, validating scope,
currentness, typed locator and normalized-view bounds, and reading the bounded
Raw bytes with a complete digest check, the implementation opens a fresh
authoritative MDBX read transaction and revalidates the resource frontier,
binding identity and expected digest. Only that final revalidation may authorize
`Hydrated`. The earlier admission snapshot is retained for diagnostics and
bounded work, not treated as completion-time authority.

More precisely, the operation linearizes at the final authoritative MDBX
snapshot observation that confirms the complete metadata set. An update
published before that observation must be detected and rejected. An update
published after that observation does not invalidate an already completed
operation; it is visible to the next operation. `CurrentAtCompletion` does not
promise that the source remains current after the linearization point or after
the result is returned to the caller.

The final observation must be one consistent metadata snapshot containing the
KnowledgeUnit lifecycle/revision, Original projection and publication identity,
ResourceManifest frontier and source generation/digest,
ResourceArtifactBinding, and ArtifactCatalog binding/body state. Independent
sequential reads from adapters that each open their own MDBX transaction do not
constitute this proof. If an existing adapter cannot perform a transaction-
bound read, the functional implementation must add a narrow read-only seam or
shared transaction view; it must not create a second freshness authority.

If the product later needs a reproducible historical read, it may expose an
internal or separately named `SnapshotCurrent` result tied to an explicit
frontier. That result must not be silently presented as current at completion.

If an update or delete occurs between admission and CAS read, final
revalidation returns `Stale` (or `Missing` when the authoritative record is
gone) and the candidate bytes are discarded. A missing CAS object returns
`Missing`; a digest mismatch returns `Corrupt`. A CAS file is read into owned
memory and hashed before returning, so a caller never receives a lazy file
handle whose contents can change after validation. A bounded retry may repeat
admission and materialization, but exhaustion returns `Unavailable` and never a
false `Hydrated`.

The result state must be explicit:

`Hydrated`, `SummaryOnly`, `Stale`, `Missing`, `Corrupt`, `Unavailable`, and
`Unsupported`.

The future public result should also preserve a diagnostic code. The proposed
mapping is:

| Authoritative condition | Result state | Diagnostic meaning |
|---|---|---|
| Caller request is malformed or locator arguments are invalid | call-level `InvalidArgument`; no result | request was not admissible |
| No exact resource/artifact binding exists | `Missing` | authority has no binding for the requested revision |
| Binding points at a different revision, or final frontier changed | `Stale` | a newer/deleted authoritative state won |
| Binding record is internally inconsistent | `Corrupt` | stored provenance failed validation |
| CAS object absent | `Missing` | retained bytes are unavailable |
| CAS bytes fail the authoritative digest | `Corrupt` | retained bytes cannot prove the binding |
| Locator kind or representation is not supported | `Unsupported` | no safe materializer exists |
| MDBX/CAS I/O or bounded retry exhaustion | `Unavailable` | operation could not establish a safe result |

The existing [`VerifiedArtifactMaterializationStatus`](../src/agent_memory/infrastructure/mdbx/MdbxVerifiedArtifactHydration.hpp)
already exposes `BindingRevisionMismatch` and `InvalidArgument`; neither must
be collapsed into a generic `Unavailable`. `BindingRevisionMismatch` is
`Stale` when authority has advanced and `Corrupt` when the stored binding
itself is malformed. The same distinction must remain visible in logs or a
typed diagnostic field even when a transport wrapper exposes only the result
state.

Required fail-closed cases include:

- update or delete between admission and materialization;
- CAS bytes disappearing or changing after admission;
- forged or mismatched digest and locator;
- wrong scope, inactive dependency or historical revision substitution;
- reopen with the same evidence and with missing/corrupt evidence.

The contract must state which guarantees come from the MDBX read snapshot and
which come from the file-backed CAS. A snapshot does not lock a separate CAS
file; the digest check is therefore mandatory. The implementation must be
exercised by a clean external installed C++17 consumer, not only by an
in-repository test that manually assembles private stores.

Dependencies and exclusions:

- reuse the existing manifest, projection, artifact catalog and typed join;
- do not add a second storage authority or an implicit coordinator;
- keep atomic publication/recovery as a separate staged-publication slice;
- treat merged #601/#602 as existing inputs; do not reinterpret their bounded
  candidate or contract-only evidence as production activation;
- do not activate production retrieval in the hydration PR.

After this gate, qualify structured product ingestion, derived Summary/Concept
publication, table/article, retained Telegram image, timed transcript and full
release evidence as separate slices.

## 6. PCA12 preservation boundary

The documentation file originally drafted alongside #603 was removed before
merge because editing `guides/experiments/README.md` made the historical PCA12
preservation validator classify a protected active path as changed. The
validator was not disabled or weakened, and no historical research material
was rewritten.

The safe documentation pattern is a new independent guide with links to
existing contracts. Before opening this PR, run:

```text
python tools/validate-pca12-residual-preservation.py
python tools/validate-historical-research-preservation.py
git diff --check
```

The first command must report the expected divergent/residual coverage and the
second must complete without changing or reclassifying protected files. This
PR intentionally leaves the experiment index untouched.

## 7. Acceptance and ownership

This documentation PR is complete only when:

1. the source-level comparison and capability matrix remain consistent with
   executable code;
2. every RAGFlow-inspired item has an explicit ADOPT, ADAPT, DEFER or REJECT
   decision;
3. release blockers and post-release enhancements are separated;
4. the #603 implemented/planned boundary is explicit;
5. the Phase 2 hydration specification has testable fail-closed criteria;
6. docs, preservation and whitespace checks pass;
7. no runtime feature, parser dependency or production activation is claimed.

The next review should inspect this document for conflicts with existing ADRs
and roadmap contracts before a separate implementation PR is authorized.
