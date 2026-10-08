# Agent memory systems: positioning and project comparison

**Scope:** architectural comparison and selection guide, not a benchmark or a feature-completeness ranking.
**Reviewed:** 2026-10-08.

**Project:** [agent-memory-cpp](../README.md) ([Russian overview](../README-RU.md)).

## The problem we address

A vector index answers *which stored vectors are similar to this query?* A durable agent memory also needs to answer:

- What was observed, imported, asserted, inferred, selected, or merely hypothesized?
- Which revision and temporal frontier does a result represent?
- Can a document or memory be edited without losing its historical identity and references?
- Which source supports a statement, and is a later summary independent evidence?
- Why did a candidate enter retrieval, reach a context pack, or fail an access rule?
- Does repeated retrieval make a memory crowd out equally relevant alternatives?
- Can the host embed the memory substrate without delegating cognition and action authority to it?

The design goal is a **small, host-neutral C++17 memory/retrieval substrate** with optional persistent adapters, explicit semantic invariants, and empirical research gates. The product is **not** a managed service, agent runtime, autonomous memory editor, or replacement for every vector database.

## Landscape (not a winner/loser table)

The descriptions below reflect projects' own public documentation at the review date. "Trade-off" describes the difference in problem boundary; it is **not** a claim that the other project lacks some undocumented feature.

| Project | Main focus / strengths | Trade-off relative to this project's goal | Relationship to agent-memory-cpp |
|---|---|---|---|
| [Mem0](https://github.com/mem0ai/mem0) | High-level memory infrastructure and managed/SDK experience; extraction, user/agent/session memory and multi-signal retrieval. | A more opinionated integration path; managed-platform results must not be treated as OSS or our results. | A useful end-to-end memory benchmark and possible host integration pattern, not a low-level C++ equivalent. |
| [Letta / MemGPT](https://github.com/letta-ai/letta) | Stateful agents with long-lived identity, tools, conversations and memory; application/runtime workflows. | Much broader runtime scope than a storage/retrieval library. | Complementary: a host/runtime could consume a memory substrate rather than be replaced by it. |
| [Graphiti](https://github.com/getzep/graphiti) | Open-source temporal knowledge graphs with episodes, validity windows, lineage and hybrid search. | Graph-first and requires a graph/backend setup; the C++ embedded-store surface is a different integration choice. | Closest design reference for a **future temporal-context-graph memory profile**; not equivalent to today's implemented code. |
| [Zep](https://www.getzep.com/) | Managed context graph infrastructure, SDKs and operational features around per-user graphs. | Operational service/product boundary rather than an embedded library. | Compare its managed experience separately from the open-source Graphiti framework. |
| [Cognee](https://github.com/topoteretes/cognee) | Self-hostable AI memory/data platform that builds searchable knowledge graphs from documents, code and conversations. | End-to-end ingestion and platform features imply a different deployment and integration scope. | Design reference and potential integration layer; no claim of equivalent current extraction/graph functionality. |
| [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) | Layered long-term memory and symbolic compression/progressive disclosure for agent context. | A particular higher-level memory organization and workflow; our profile model aims not to hard-code one hierarchy. | Inspiration for layered disclosure and evaluation, not evidence of a deployed equivalent. |
| [A-MEM](https://github.com/agiresearch/A-mem) | Agentic note generation, dynamic linking and Zettelkasten-like memory evolution. | Research/workflow emphasizes LLM-driven organization; this project keeps reasoning and admission outside core storage. | Research comparison for future consolidation and provenance-aware memory evolution. |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Lightweight graph + vector retrieval over document corpora. | Primarily a RAG framework, not a general durable task/decision/provenance memory substrate. | Retrieval baseline and graph-RAG pattern donor. |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | Corpus transformation, graph communities and global question-answering research. | Indexing-oriented document RAG, often with greater extraction/preparation cost. | Reference for corpus-level summaries and research methodology, not a drop-in memory backend. |
| [LlamaIndex](https://github.com/run-llama/llama_index) | Broad document/agent data connectors, indexing, retrieval and application integrations. | High-level ecosystem versus narrow embeddable C++ domain contracts. | May be an application/integration neighbor; different abstraction level. |
| [FAISS](https://github.com/facebookresearch/faiss) / [sqlite-vec](https://github.com/asg017/sqlite-vec) | Efficient similarity search and embedded vector storage primitives. | They do not by themselves define canonical agent-memory revisions, admission, evidence ancestry or host authority. | Index components/benchmarks can complement memory; they are **not** direct agent-memory product competitors. |

### Why these distinctions matter

**Against pure RAG/index libraries:** a similarity score does not tell you whether a fact is current, what its source was, or whether a derived summary is independent confirmation. Those semantics belong to the memory/domain layer.

**Against full agent platforms:** the library should not decide what the agent believes, which tools to run, or when to create commitments. The host can choose an LLM/runtime and use only the memory contracts it needs.

**Against graph-first memory:** temporal relationships and evidence lineage are valuable, but graph traversal is one possible retrieval route. The intended profiles also allow lexical, dense, episodic, procedural and structured records; these should not collapse into graph edges by default.

**Against managed services:** an embedded build can offer local operation and explicit lifecycle ownership, but also transfers integration, persistence, maintenance, security and quality responsibility to the application.

## Our benefits — and our costs

| Intended benefit | Concrete meaning | Current caveat |
|---|---|---|
| Embeddability | Native C++17 library; optional MDBX integration, no mandatory Python process for the core. | Host must assemble its own runtime and model adapters. |
| Correctable history | Stable canonical block IDs, historical revisions, logical body vs physical generation separation. | Implemented for the text/in-memory and MDBX C1 raw subset, not all memory forms. |
| Verifiable provenance | Source/evidence ancestry, derived-claim and time semantics are explicit design contracts. | Full end-to-end enforcement remains staged work. |
| Non-monolithic retrieval | Exact, lexical, vector, temporal, graph and fusion strategies are independent research/implementation lanes. | Production BM25F/graph/temporal/federated stack is not delivered. |
| Storage portability | Domain-level interfaces rather than a generic `IDatabase`; multiple independent backend contexts are possible. | SQLite and heterogeneous workspace routing are not production adapters yet. |
| Evidence-led optimization | Controlled codecs, exact-oracle evaluation, reproducible traces, candidate/quality/cost accounting. | Benchmarks do not demonstrate superiority to the above products; some gates remain protocols only. |

### Current, contract, and research: do not conflate them

| Capability | Status in this repository | Where to verify |
|---|---|---|
| C++17 core, value objects, reference index/eval harnesses | Implemented in bounded slices | [Code](../src/), [coverage audit](retrieval-roadmap-coverage.md) |
| Canonical text edit/history in memory | Implemented | [Canonical-content contract](canonical-content-storage-roadmap.md) |
| Persistent canonical body with MDBX raw/plain | Implemented **C1 subset** | [Coverage audit](retrieval-roadmap-coverage.md), [MDBX contract](storage-backend-integration-roadmap.md) |
| Full typed knowledge-unit profiles and memory lifecycle | Contracts/roadmaps, partial primitives | [Memory stacks](memory-stacks-roadmap.md), [milestones](milestones.md) |
| Full hybrid BM25F/vector/graph/temporal retrieval | Not an integrated product path | [Coverage audit](retrieval-roadmap-coverage.md) |
| Provenance-aware influence, temporal expectations and scenario replay | Semantic contracts / future integration | [Explainability](retrieval-explainability-roadmap.md), [runtime](agent-runtime-integration-roadmap.md) |
| Feedback stability under repeated retrieval | **F0 protocol proposed; experiment pending** | [F0](experiments/2026-10-07-memory-feedback-stability-protocol.md) |
| Multi-MDBX workspace routing and federated access | Topology/federation contracts; router pending | [Storage integration](storage-backend-integration-roadmap.md), [federation](federated-retrieval-roadmap.md) |

The [coverage audit](retrieval-roadmap-coverage.md) and [normative milestones](milestones.md) take precedence over any summary on this page. Do not infer production readiness from code sketches, an experimental index, a green unit test, or a planned `MemoryProfileSpec`.

## Choose by workload

- **Choose an existing high-level system** when you want ready agent experiences, memory extraction and operational integrations. Evaluate Mem0, Letta, Cognee, Zep/Graphiti against your deployment and data model.
- **Choose a graph RAG or vector library** when the problem is primarily searching a fixed corpus or nearest neighbors. Avoid introducing an elaborate memory system when indexing alone suffices.
- **Consider agent-memory-cpp** when you control a native C++ host, need to compose storage and retrieval without handing it an agent runtime, or want to study persistent revisions, provenance boundaries and retrieval behavior. Today, expect engineering work beyond the C1/reference slices.
- **Combine layers** when appropriate: an external model, a host agent, an embedded canonical store and a vector index need not come from one product.

## How to compare fairly

A claim of a better agent memory needs matched data, qrels/tasks, generation/frontier, memory-update policy, retrieval budget, latency and cost measurements. Compare memory **formation, update, retention/forgetting, retrieval, context inclusion, and outcome** rather than one top-K score. Repeated-use feedback must be tested for self-amplification, source concentration and cold-start starvation; copied summaries must not count as independent evidence.

The research protocols and metrics are under [evaluation](evaluation-roadmap.md) and [experiments](experiments/). There is **no validated cross-project performance ranking** here.

For a longer bibliography of additional libraries, papers and pattern donors (including research-only ideas), see [related-projects.md](related-projects.md) and [research-reading-map.md](research-reading-map.md).
