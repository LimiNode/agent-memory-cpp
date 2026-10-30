# Retrieval Roadmap Coverage

For the broader product-readiness, ANN extensibility, and technical-preview
audit at the current `main` baseline, see
[`product-ann-readiness-audit.md`](product-ann-readiness-audit.md). This table
remains the capability inventory; the audit adds source-level evidence and
release acceptance gates without changing milestone ownership.

This is the documentation audit for the current repository state. `Implemented`
means exercised code exists; `Contract only` means a public or storage contract
exists without the complete backend; `Docs/tests only` means the idea is
described or probed but not a product path; `Roadmap only` means planned work.

| Area | Current owner | Status | Missing implementation/research | Next bounded step |
|---|---|---|---|---|
| Core domain primitives | `milestones.md`, `architecture.md` | Implemented | broader payload coverage | finish M0 fixtures |
| Multi-context topology / workspace routing | `storage-backend-integration-roadmap.md`, `federated-retrieval-roadmap.md` | Implemented (R0 canonical MDBX slice) | cross-context publication/reconciliation and per-context frontier execution remain out of scope; no relocation or federation path is implemented | extend the same fail-closed placement contract to the next domain store |
| Storage / MDBX | `mdbx-containers-extension-tz.md` | Contract only | full profile and crash matrix | storage foundation PR |
| Resource manifests / reindex | `resource-reindexing.md`, `m0-implementation-gap-audit.md` | Implemented (lexical-first + raw Chunk + manifest-aware Original publication profiles) | locator-backed `ChunkPayload` hydration, full `source_refs`, general projection lifecycle and full reindex/compaction protocol remain unimplemented | add bounded projection-aware lexical retrieval fixture |
| Canonical text / in-memory read, materialize and edit | `canonical-content-storage-roadmap.md` | Implemented | durable persistence, compressed body and segment/section integration are outside this implemented subset; the exercised path is the in-memory reference backend and its canonical-content test suite | preserve the semantic conformance cases in [CanonicalContentStore](../src/agent_memory/storage/CanonicalContentStore.hpp) and [canonical-content tests](../tests/domain/canonical_content_test.cpp) |
| Host-facing revision-bound document access/edit | `canonical-content-storage-roadmap.md`, `product-path.md` | Contract only | durable host adapter, line/materialization conformance fixture and source-note binding remain unimplemented | add revision-bound read/edit, conflict/NoChange and reopen fixture |
| Canonical body binding / durable MDBX store | `canonical-content-storage-roadmap.md`, `mdbx-containers-extension-tz.md` | Implemented (C1 raw/plain subset) | framed codecs (C2), segment/section integration, crash/recovery matrix and targeted invalidation integration | complete C2 physical re-encoding and the remaining conformance gates |
| Typed multimodal result / source hydration | `knowledge-base-roadmap.md`, `artifact-provenance-roadmap.md`, `multimodal-binary-retrieval-roadmap.md` | Contract only | host materialization adapter and per-modality hydration/conformance fixtures remain unimplemented | add text/image/audio/video revision-and-locator fixture |
| Knowledge units | `knowledge-units-roadmap.md`, `m0-implementation-gap-audit.md` | Implemented (M0 durable Chunk envelope + importer binding subset) | supersedence lineage, non-Chunk payload stores and full lifecycle policy remain unimplemented | add supersede/lineage and payload-specific M0 fixtures |
| Payload contracts/views | `knowledge-base-roadmap.md` | Contract only | all payload DBIs | one payload family at a time |
| Search projections | `lexical-search-roadmap.md`, `projection-aware-lexical-retrieval.md` | Implemented (revision-aware Original M0 slice, manifest-aware publication, bounded projection-aware lexical retrieval) | locator-backed full-body hydration, persistent postings, projection-aware index lifecycle and reclamation | add batched candidate reader and projection-to-lexical retrieval benchmark |
| Tabular / TSV-CSV structured retrieval | `structured-data-retrieval-roadmap.md`, `artifact-provenance-roadmap.md` | Roadmap only | dialect-aware parser, canonical table representation, stable row/cell locators, structured indexes, table discovery and targeted invalidation remain unimplemented | define the S0 tabular fixture and run the table-level versus row/column projection bake-off |
| Lexical retrieval | `lexical-search-roadmap.md`, `m0-implementation-gap-audit.md` | Implemented (durable lexical importer slice) | BM25F/product-wide projection lifecycle, traces and non-text modalities remain unimplemented | add the next bounded lexical projection/retrieval fixture |
| Dense retrieval | `embedding.md`, `optimization-roadmap.md` | Contract only | embedder adapter and exact index path | exact dense MVP |
| ExactVectorIndex | `optimization-roadmap.md` | Docs/tests only | canonical library integration | oracle benchmark |
| ANN / HNSW | `optimization-roadmap.md` | Roadmap only | implementation and lifecycle | HNSW spike vs exact |
| Vector compression | `binary-embeddings-roadmap.md`, `advanced-binary-techniques-roadmap.md` | Docs/tests only | codec/index integration | F32/F16/int8 matrix |
| Rare-facet / multi-granularity retrieval | `evaluation-roadmap.md`, `advanced-binary-techniques-roadmap.md`, `knowledge-activation-roadmap.md` | Docs/tests only | equal-budget ASMS/MSBSE/AR0 comparison, rare-facet qrels and lifecycle fixtures | run baseline, projection, associative-expansion and composed-route benchmark |
| Hybrid / RRF | `knowledge-base-roadmap.md`, `retrieval-execution-roadmap.md` | Contract only | route-local pools, hard-filter diagnostics and fusion trace | Gate H0 protocol, then RRF MVP |
| Graph retrieval | `knowledge-base-roadmap.md`, `retrieval-techniques-roadmap.md`, `knowledge-activation-roadmap.md` | Roadmap only | relation-owned graph substrate/traversal, local/global/hybrid route fixtures and matched evaluation | run relation-owned graph gate |
| Temporal navigation / historical reads | `memory-lifecycle-governance-roadmap.md` AM-13/T0 | Contract only | bounded timeline/episode/neighbor navigation, cursor/frontier validation and origin-aware fixtures | T0 temporal navigation fixture |
| Durable working context / crash recovery | `agent-runtime-integration-roadmap.md` W0, `context-building.md` | Contract only | host checkpoint/receipt adapter, crash matrix and provider unknown-outcome replay | W0 boundary fixture |
| Adaptive associative recall | `knowledge-activation-roadmap.md` AR0 | Docs/tests only | bounded multi-step cue/context search and cost/provenance comparison | AR0 research screen |
| Event-centric grounding | `knowledge-base-roadmap.md` EG0, `evaluation-roadmap.md` | Docs/tests only | extraction/linking fixture and parity-controlled event retrieval runner | EG0 grounding gate |
| Offline consolidation / replay | `offline-consolidation-roadmap.md`, `compaction-roadmap.md`, `evaluation-roadmap.md` | Docs/tests only | bounded replay selection, source-frontier receipts, protected cases, rebuild and recovery fixtures | run OC-0 no-op/extractive/abstractive/consolidation comparison |
| Hierarchical DomainMap navigation | `hierarchical-navigation-roadmap.md`, `knowledge-activation-roadmap.md`, `usage-llm-wiki.md` | Docs/tests only | curated/derived map parity, bounded recursive activation, stale-map handling and flat fallback | run HN-0 map-conditioned retrieval comparison |
| Linked derived knowledge | `linked-derived-knowledge.md`, `knowledge-units-roadmap.md`, `source-trust-roadmap.md` | Contract only | durable multi-input exact-revision bindings, typed Relation publication, reverse lookup, bounded evidence drill-down and stale propagation are not implemented | implement the narrow Note/Summary/Playbook relation publication and reopen fixture |
| Codec-neutral derived representations | `derived-representations-and-codecs.md`, `embedding.md`, `optimization-roadmap.md` | Contract/research split | arbitrary aspect registry, persisted dictionary IDs, retention policies and library compact-vector adapter are not implemented; THQ/LSQ/PLSQ/TQ/RSLM evidence remains research | implement one bounded compact-vector preview after storage/retrieval foundations |
| Optional JPEG XL image representation | `artifact-provenance-roadmap.md`, `visual-retrieval-roadmap.md`, `experiments/2026-10-13-jpeg-xl-image-representation.md` | Research candidate | matched-corpus codec, metadata, lossless-roundtrip and decoder-portability evidence | benchmark before any adapter or production status |
| Context compression | `compaction-roadmap.md`, `compression-is-intelligence-roadmap.md` | Roadmap only | budgeted compressor | no-op/extractive baseline |
| Decision-relevant retention / compression evaluation | `evaluation-roadmap.md`, `compaction-roadmap.md`, `compression-is-intelligence-roadmap.md` | Docs/tests only | fixed decision probes, equal-budget extractive/abstractive comparisons and replayable retention evidence | run no-op/extractive/abstractive retention fixture with exact/key-based probes |
| Marginal value of memory / context read | `evaluation-roadmap.md`, `context-building.md`, `compaction-roadmap.md` | Docs/tests only | independent MVA-0 benchmark for optional derived projections and bounded ContextPack selection | run baseline, measured-gain and Value-of-Read comparison with protected fixtures |
| Task closure / evidence opening | `evaluation-roadmap.md`, `retrieval-execution-roadmap.md`, `context-building.md` | Docs/tests only | independent task oracle, one-shot/fixed/adaptive comparison, selective source-opening and post-compaction protected fixtures remain unimplemented | run closure benchmark on a technical corpus with a full cost and evidence ledger |
| Query transformation / corrective retrieval | `memory-stacks-roadmap.md`, `retrieval-techniques-roadmap.md` | Roadmap only | typed representation semantics, external adapter and bounded corrective-loop implementation | no-op + trace + budget fixture |
| Reranking | `retrieval-techniques-roadmap.md` | Roadmap only | bounded reranker API | candidate-depth benchmark |
| Temporal memory | `memory-lifecycle-governance-roadmap.md` | Contract only | valid-at implementation | single-axis fixture |
| Prospective expectations / scenario overlays | `agent-runtime-integration-roadmap.md`, `memory-stacks-roadmap.md` | Contract only | durable expectation mapping, resolution/projection integration and runtime adapter | expectation/resolution and scenario-isolation fixtures |
| Memory lifecycle | `memory-lifecycle-governance-roadmap.md` | Roadmap only | supersession/decay policies | lifecycle decision record |
| Evaluation / benchmarks | `evaluation-roadmap.md` | Docs/tests only | unified runner | exact-oracle harness |
| Source trust / provenance | `source-trust-roadmap.md`, `artifact-provenance-roadmap.md` | Contract only | propagation in all payloads | lineage fixture |
| Diagnostics / explainability | `retrieval-explainability-roadmap.md` | Roadmap only | stable explain payload | lexical explain MVP |
| Session extraction / derived memory projection | `memory-lifecycle-governance-roadmap.md` AM-24 | Contract only | source-frontier receipts, idempotent extraction and derived summary/fact fixtures remain unimplemented | add session extraction projection fixture |
| Cross-kind Context Composition | `knowledge-base-roadmap.md` | Contract only | shared-frontier multi-lane retrieval and deterministic fusion fixture remain unimplemented | run flat, single-lane and composed context comparison |
| Context-Pack positional robustness | `evaluation-roadmap.md` | Docs/tests only | position/order/fill fixture with exact protected probes remains unimplemented | run held-out positional robustness gate |

The table is deliberately conservative. A green unit test for a contract does
not promote an unimplemented backend to `Implemented`.
