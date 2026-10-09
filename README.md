# agent-memory-cpp

**A C++17 library for building memory and retrieval systems for AI agents.**

[English](README.md) | [Русский](README-RU.md)

`agent-memory-cpp` helps a host application retain documents, conversations, events and knowledge, keep their history, retrieve relevant information, and assemble inspectable context for a model. It is designed for local and embedded use, so the core does not require a Python process or a separate memory service.

The project is a library and research/engineering project, not an LLM, ready-made assistant, hosted memory platform, or agent orchestration framework. The host application owns models, decisions, tools, scheduling and action authority.

## Why use it?

- **Embed it in the host.** The core is a native C++17/CMake library with optional storage and model integrations.
- **Keep content editable and traceable.** Canonical documents have stable block identities, immutable revisions, historical reads and deterministic materialization.
- **Separate storage, retrieval and evidence.** Domain contracts do not require one database, and retrieval frequency does not become proof of truth.
- **Compose memory profiles.** The target design can combine conversations, documents, events, knowledge units, relations and procedural experience with the retrieval paths an application needs.
- **Measure behavior.** Exact baselines, replayable evidence, codec experiments and failure-oriented fixtures are preferred to unsupported quality claims.

## What it is intended to support

The following is the target capability model. It describes the direction and contracts of the project; it does not mean that every item is implemented today.

- Conversation history and session-derived memory.
- Semantic, episodic, procedural, temporal and user/project memory profiles.
- Markdown and structured documents, events, facts, entities and relations.
- Exact, lexical, dense, hybrid, graph and temporal retrieval, with reranking and context assembly.
- Pluggable embedding/model adapters, index implementations and storage backends.
- Local knowledge bases and agent applications that can keep memory without a mandatory external service.

The current implementation status is listed separately below and in the [coverage audit](guides/retrieval-roadmap-coverage.md).

## How it differs

- It is an embeddable domain library, not a complete agent platform with its own runtime, tools or model.
- Canonical content, revision history and provenance are part of the design instead of an unexamined blob behind a vector index.
- Storage backends implement domain contracts such as canonical content stores; the library does not force every backend into a generic `IDatabase` abstraction.
- Retrieval and evidence status are separate. A candidate can be returned or placed in a context without becoming current, verified or authorized for an action.
- The repository publishes implementation status and research gates explicitly. See the [project comparison](guides/project-comparison.md) for balanced trade-offs against related projects.

## Architecture

The intended data flow is:

~~~mermaid
flowchart TD
    S["Documents / conversations / events"] --> C["Normalize and retain canonical representations"]
    C --> P["Revisions, provenance and lifecycle"]
    P --> I["Lexical / dense / graph / temporal projections"]
    I --> R["Retrieve and assemble ContextPack"]
    R --> A["Host application / agent model"]
    B["In-memory / MDBX / future backends"] -.-> C
~~~

Storage adapters, index implementations and model providers are composition choices around the domain contracts. The diagram is an architecture direction, not a claim that every box is a finished production subsystem.

## Current implementation status

| Area | What is available | What is not yet a complete product path |
|---|---|---|
| Native foundation | C++17/CMake library, domain interfaces and values, tests and example targets | Turnkey agent-memory application |
| Canonical text | In-memory document read/edit/materialization with stable blocks, revisions and net change sets | Full multi-domain or multimodal content editing |
| Persistent canonical text | Optional **MDBX C1** raw/plain adapter: shared context, historical revisions, body bindings and no-reuse ledger | C2 compression/re-encoding, complete cross-store publication and recovery |
| Retrieval/indexing | Bounded exact, lexical and vector primitives, retrieval contracts and experimental harnesses | Complete BM25F + dense + graph + temporal production stack and automatic context planner |
| Evidence and lifecycle | Design contracts, fixtures and research protocols | Finished end-to-end provenance, temporal reasoning, feedback and authority enforcement |
| Multiple contexts | Independently constructible MDBX contexts and documented placement/federation boundaries | Production workspace router, federated executor, heterogeneous backends and cross-context publication |

The [retrieval coverage audit](guides/retrieval-roadmap-coverage.md) is the status authority. `Implemented`, `Contract only`, `Docs/tests only` and `Roadmap only` have different meanings. No cross-context ACID guarantee is part of the design; independent contexts expose separate transaction and snapshot boundaries.

## Main components

- **Canonical content:** `ICanonicalContentStore`, `ICanonicalContentEditor` and `InMemoryCanonicalContentStore` provide the reference read/edit/history contract. `MdbxCanonicalContentStore` is the optional C1 persistence adapter.
- **Domain model:** typed identifiers, metadata, canonical document revisions, stable content blocks and net change sets.
- **Retrieval and indexes:** exact lexical/vector primitives, retriever interfaces, evaluation adapters and experimental index implementations.
- **Integration seams:** embedding, ingestion, storage and model/runtime adapters are kept outside the canonical domain contract.
- **Evaluation:** coverage tables, exact oracles, research protocols and reproducible receipts track what is implemented, specified or still experimental.

## Build and quick start

Requirements: **C++17**, **CMake 3.20+** and a supported toolchain on Windows, Linux or macOS.

~~~bash
git clone --recursive https://github.com/LimiNode/agent-memory-cpp.git
cd agent-memory-cpp

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_EXAMPLES=ON

cmake --build build --config Release
ctest --test-dir build --build-config Release --output-on-failure
~~~

Optional MDBX support is **off by default**. Enable it with `-DAGENT_MEMORY_ENABLE_MDBX=ON` and provide the declared `libmdbx` / `mdbx-containers` dependencies. The core target is `agent_memory::agent_memory`; the aggregate include is `<agent_memory.hpp>`.

### Minimal canonical-content example

The executable example is the source of truth for this path: [`examples/canonical_content.cpp`](examples/canonical_content.cpp). It creates revision `0`, commits one edit, materializes both historical and current Markdown, and checks that the two revisions remain distinct.

The public API shape is:

~~~cpp
InMemoryCanonicalContentStore store;
const auto result = store.commit(
    CanonicalEditRequest{document_id, 0, std::move(operations)}
);
const auto previous = store.materialize_markdown(document_id, 0);
const auto current = store.materialize_markdown(document_id);
~~~

Build and run the complete example with:

~~~bash
cmake -S . -B build \
  -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_EXAMPLES=ON
cmake --build build --config Release
ctest --test-dir build --build-config Release \
  -R "^agent_memory_canonical_content_example$" --output-on-failure
~~~

For the full semantic cases, see the [canonical-content contract](guides/canonical-content-storage-roadmap.md), the [public header](src/agent_memory/storage/CanonicalContentStore.hpp) and [canonical-content tests](tests/domain/canonical_content_test.cpp).

## Roadmap and evaluation

- [Milestones](guides/milestones.md) — normative implementation scope.
- [Coverage audit](guides/retrieval-roadmap-coverage.md) — implemented slices versus contracts and research.
- [Storage topology](guides/storage-backend-integration-roadmap.md) — contexts, placement, identity and federation boundaries.
- [Evaluation roadmap](guides/evaluation-roadmap.md) and [experiments](guides/experiments/) — baselines, receipts and reproducibility.
- [Memory-feedback stability F0](guides/experiments/2026-10-07-memory-feedback-stability-protocol.md) — protocol, not a completed experiment.
- [Project comparison](guides/project-comparison.md) — alternatives, strengths, limitations and selection guidance.

## Non-goals

A hosted memory platform, universal database abstraction, distributed transaction manager, agent runtime, mandatory Python/LLM dependency, or unsupported claims of superior speed or accuracy.

## License

[MIT](LICENSE).
