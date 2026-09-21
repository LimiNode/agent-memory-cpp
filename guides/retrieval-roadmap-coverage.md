# Retrieval Roadmap Coverage

This is the documentation audit for the current repository state. `Implemented`
means exercised code exists; `Contract only` means a public or storage contract
exists without the complete backend; `Docs/tests only` means the idea is
described or probed but not a product path; `Roadmap only` means planned work.

| Area | Current owner | Status | Missing implementation/research | Next bounded step |
|---|---|---|---|---|
| Core domain primitives | `milestones.md`, `architecture.md` | Implemented | broader payload coverage | finish M0 fixtures |
| Storage / MDBX | `mdbx-containers-extension-tz.md` | Contract only | full profile and crash matrix | storage foundation PR |
| Resource manifests / reindex | `resource-reindexing.md` | Contract only | end-to-end publication | targeted reindex fixture |
| Knowledge units | `knowledge-units-roadmap.md` | Contract only | complete stores | M0 unit/reopen tests |
| Payload contracts/views | `knowledge-base-roadmap.md` | Contract only | all payload DBIs | one payload family at a time |
| Search projections | `lexical-search-roadmap.md` | Contract only | generation-aware publisher | projection lifecycle gate |
| Lexical retrieval | `lexical-search-roadmap.md` | Roadmap only | tokenizer/postings/BM25F | deterministic BM25 MVP |
| Dense retrieval | `embedding.md`, `optimization-roadmap.md` | Contract only | embedder adapter and exact index path | exact dense MVP |
| ExactVectorIndex | `optimization-roadmap.md` | Docs/tests only | canonical library integration | oracle benchmark |
| ANN / HNSW | `optimization-roadmap.md` | Roadmap only | implementation and lifecycle | HNSW spike vs exact |
| Vector compression | `binary-embeddings-roadmap.md`, `advanced-binary-techniques-roadmap.md` | Docs/tests only | codec/index integration | F32/F16/int8 matrix |
| Hybrid / RRF | `knowledge-base-roadmap.md` | Contract only | candidate trace and fusion | RRF MVP |
| Graph retrieval | `knowledge-activation-roadmap.md` | Roadmap only | graph substrate/traversal | relation-owned graph gate |
| Context compression | `compaction-roadmap.md`, `compression-is-intelligence-roadmap.md` | Roadmap only | budgeted compressor | no-op/extractive baseline |
| Query transformation | `knowledge-activation-roadmap.md` | Roadmap only | drift/evaluation hooks | no-op + trace contract |
| Reranking | `retrieval-techniques-roadmap.md` | Roadmap only | bounded reranker API | candidate-depth benchmark |
| Temporal memory | `memory-lifecycle-governance-roadmap.md` | Contract only | valid-at implementation | single-axis fixture |
| Memory lifecycle | `memory-lifecycle-governance-roadmap.md` | Roadmap only | supersession/decay policies | lifecycle decision record |
| Evaluation / benchmarks | `evaluation-roadmap.md` | Docs/tests only | unified runner | exact-oracle harness |
| Source trust / provenance | `source-trust-roadmap.md`, `artifact-provenance-roadmap.md` | Contract only | propagation in all payloads | lineage fixture |
| Diagnostics / explainability | `retrieval-explainability-roadmap.md` | Roadmap only | stable explain payload | lexical explain MVP |

The table is deliberately conservative. A green unit test for a contract does
not promote an unimplemented backend to `Implemented`.
