# Retrieval Roadmap Coverage

This is the documentation audit for the current repository state. `Implemented`
means exercised code exists; `Contract only` means a public or storage contract
exists without the complete backend; `Docs/tests only` means the idea is
described or probed but not a product path; `Roadmap only` means planned work.

| Area | Current owner | Status | Missing implementation/research | Next bounded step |
|---|---|---|---|---|
| Core domain primitives | `milestones.md`, `architecture.md` | Implemented | broader payload coverage | finish M0 fixtures |
| Multi-context topology / workspace routing | `storage-backend-integration-roadmap.md`, `federated-retrieval-roadmap.md` | Implemented (R0 canonical MDBX slice) | cross-context publication/reconciliation and per-context frontier execution remain out of scope; no relocation or federation path is implemented | extend the same fail-closed placement contract to the next domain store |
| Storage / MDBX | `mdbx-containers-extension-tz.md` | Contract only | full profile and crash matrix | storage foundation PR |
| Resource manifests / reindex | `resource-reindexing.md` | Contract only | end-to-end publication | targeted reindex fixture |
| Canonical text / in-memory read, materialize and edit | `canonical-content-storage-roadmap.md` | Implemented | durable persistence, compressed body and segment/section integration are outside this implemented subset; the exercised path is the in-memory reference backend and its canonical-content test suite | preserve the semantic conformance cases in [CanonicalContentStore](../src/agent_memory/storage/CanonicalContentStore.hpp) and [canonical-content tests](../tests/domain/canonical_content_test.cpp) |
| Canonical body binding / durable MDBX store | `canonical-content-storage-roadmap.md`, `mdbx-containers-extension-tz.md` | Implemented (C1 raw/plain subset) | framed codecs (C2), segment/section integration, crash/recovery matrix and targeted invalidation integration | complete C2 physical re-encoding and the remaining conformance gates |
| Knowledge units | `knowledge-units-roadmap.md` | Contract only | complete stores | M0 unit/reopen tests |
| Payload contracts/views | `knowledge-base-roadmap.md` | Contract only | all payload DBIs | one payload family at a time |
| Search projections | `lexical-search-roadmap.md` | Contract only | generation-aware publisher | projection lifecycle gate |
| Lexical retrieval | `lexical-search-roadmap.md` | Roadmap only | tokenizer/postings/BM25F | deterministic BM25 MVP |
| Dense retrieval | `embedding.md`, `optimization-roadmap.md` | Contract only | embedder adapter and exact index path | exact dense MVP |
| ExactVectorIndex | `optimization-roadmap.md` | Docs/tests only | canonical library integration | oracle benchmark |
| ANN / HNSW | `optimization-roadmap.md` | Roadmap only | implementation and lifecycle | HNSW spike vs exact |
| Vector compression | `binary-embeddings-roadmap.md`, `advanced-binary-techniques-roadmap.md` | Docs/tests only | codec/index integration | F32/F16/int8 matrix |
| Rare-facet / multi-granularity retrieval | `evaluation-roadmap.md`, `advanced-binary-techniques-roadmap.md`, `knowledge-activation-roadmap.md` | Docs/tests only | equal-budget ASMS/MSBSE/AR0 comparison, rare-facet qrels and lifecycle fixtures | run baseline, projection, associative-expansion and composed-route benchmark |
| Hybrid / RRF | `knowledge-base-roadmap.md`, `retrieval-execution-roadmap.md` | Contract only | route-local pools, hard-filter diagnostics and fusion trace | Gate H0 protocol, then RRF MVP |
| Graph retrieval | `knowledge-activation-roadmap.md` | Roadmap only | graph substrate/traversal | relation-owned graph gate |
| Temporal navigation / historical reads | `memory-lifecycle-governance-roadmap.md` AM-13/T0 | Contract only | bounded timeline/episode/neighbor navigation, cursor/frontier validation and origin-aware fixtures | T0 temporal navigation fixture |
| Durable working context / crash recovery | `agent-runtime-integration-roadmap.md` W0, `context-building.md` | Contract only | host checkpoint/receipt adapter, crash matrix and provider unknown-outcome replay | W0 boundary fixture |
| Adaptive associative recall | `knowledge-activation-roadmap.md` AR0 | Docs/tests only | bounded multi-step cue/context search and cost/provenance comparison | AR0 research screen |
| Event-centric grounding | `knowledge-base-roadmap.md` EG0, `evaluation-roadmap.md` | Docs/tests only | extraction/linking fixture and parity-controlled event retrieval runner | EG0 grounding gate |
| Context compression | `compaction-roadmap.md`, `compression-is-intelligence-roadmap.md` | Roadmap only | budgeted compressor | no-op/extractive baseline |
| Decision-relevant retention / compression evaluation | `evaluation-roadmap.md`, `compaction-roadmap.md`, `compression-is-intelligence-roadmap.md` | Docs/tests only | fixed decision probes, equal-budget extractive/abstractive comparisons and replayable retention evidence | run no-op/extractive/abstractive retention fixture with exact/key-based probes |
| Query transformation | `knowledge-activation-roadmap.md` | Roadmap only | drift/evaluation hooks | no-op + trace contract |
| Reranking | `retrieval-techniques-roadmap.md` | Roadmap only | bounded reranker API | candidate-depth benchmark |
| Temporal memory | `memory-lifecycle-governance-roadmap.md` | Contract only | valid-at implementation | single-axis fixture |
| Prospective expectations / scenario overlays | `agent-runtime-integration-roadmap.md`, `memory-stacks-roadmap.md` | Contract only | durable expectation mapping, resolution/projection integration and runtime adapter | expectation/resolution and scenario-isolation fixtures |
| Memory lifecycle | `memory-lifecycle-governance-roadmap.md` | Roadmap only | supersession/decay policies | lifecycle decision record |
| Evaluation / benchmarks | `evaluation-roadmap.md` | Docs/tests only | unified runner | exact-oracle harness |
| Source trust / provenance | `source-trust-roadmap.md`, `artifact-provenance-roadmap.md` | Contract only | propagation in all payloads | lineage fixture |
| Diagnostics / explainability | `retrieval-explainability-roadmap.md` | Roadmap only | stable explain payload | lexical explain MVP |

The table is deliberately conservative. A green unit test for a contract does
not promote an unimplemented backend to `Implemented`.
