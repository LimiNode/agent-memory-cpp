# agent-memory-cpp

**An embeddable C++17 foundation for memory and retrieval in AI agents.**

[English](README.md) | [Русский](README-RU.md)

Agent memory is more than searching a vector database. A long-running agent needs to retain source material, recognize updates, distinguish observations from inferences, retrieve relevant history, and assemble context without silently promoting repeated or derived claims into facts.

`agent-memory-cpp` is a **library and research/engineering project**, not an LLM, a ready-made assistant, a hosted memory service, or an agent orchestration framework. Applications own their models, decisions, tools, scheduling, and action authority.

**Read the [project comparison and trade-offs](guides/project-comparison.md)** for how this approach relates to Mem0, Letta, Graphiti/Zep, Cognee, A-MEM, graph RAG, and vector-index libraries. The broader reference catalog is in [related projects](guides/related-projects.md).

## Why build it?

- **Embed rather than operate a service.** The core is a C++17 static library; Python or a separate vector database server is not mandatory for the core.
- **Keep memory editable and traceable.** Stable content-block identities, immutable document revisions, historical reads, logical body revisions, and storage-generation separation underpin the canonical-content work.
- **Separate evidence from retrieval popularity.** Source provenance, derived claims, temporal validity, memory influence, and usage-feedback failure modes have explicit design/research contracts. These are not all implemented.
- **Keep domain contracts independent of the database.** An in-memory reference store and an optional MDBX canonical-content adapter implement the same domain-oriented interface. Other backends are future integrations, not automatic drop-in equivalents.
- **Measure instead of promising quality.** Exact/oracle baselines, codec and retrieval experiments, replayable evidence, and failure-oriented tests inform later production choices.

### Where it fits

~~~text
Host application / agent runtime (models, tools, decisions, permissions)
                         |
           memory policies / context planning       [mostly roadmap]
                         |
       domain retrieval + canonical-content contracts
            /                            \
   reference implementations       infrastructure adapters
   in-memory / test kernels          MDBX C1 raw/plain
                         |
         independent evaluation and research harnesses
~~~

The intended system includes knowledge-unit profiles, lexical/dense/graph/temporal retrieval, context assembly, lifecycle policies, and optional external integrations. **This diagram is an architecture direction, not a claim that every box is production-ready.**

## Current implementation status

| Area | What is available | What is not yet a complete product path |
|---|---|---|
| Native foundation | C++17/CMake library, domain interfaces and values, test/example targets | Turnkey agent-memory application |
| Canonical text | In-memory document read/edit/materialization with stable blocks, revisions and net change sets | Full multi-domain or multimodal content editing |
| Persistent canonical text | Optional **MDBX C1** raw/plain adapter: shared context, historical revisions, body bindings, no-reuse ledger | C2 compression/re-encoding, complete cross-store publication and recovery |
| Retrieval/indexing | Bounded exact, lexical and vector primitives, retrieval contracts and experimental harnesses | Complete BM25F + dense + graph + temporal production stack and automatic context planner |
| Evidence and lifecycle | Design contracts, fixtures and research protocols | Finished end-to-end provenance, temporal reasoning, feedback and authority enforcement |
| Multiple databases | Independently constructible MDBX contexts; documented placement/federation boundaries | Production workspace router, heterogeneous backends, or cross-context ACID |

The [retrieval coverage audit](guides/retrieval-roadmap-coverage.md) is the status authority. `Implemented`, `Contract only`, `Docs/tests only`, and `Roadmap only` mean different things. A benchmark prototype does not establish an available production memory stack.

## Design boundaries

1. **Logical identity is not physical location.** Document, block and body revisions do not depend on compression or a particular MDBX path. Multi-context placement/routing is documented separately and is not yet a production router.
2. **Retrieval is not authority.** Candidate, returned result, context selection, provider egress, and host-observed use are different stages. Frequent retrieval does not corroborate a source.
3. **One database context is one transaction boundary.** Multiple MDBX files and future SQLite/CAS/external indexes do not acquire a distributed transaction by sharing a domain interface.
4. **Models remain optional and external.** Embedding generation, LLM inference, entity extraction and autonomous behavior belong to adapters or host runtimes.

See [architecture](guides/architecture.md), [storage backend integration](guides/storage-backend-integration-roadmap.md), [canonical content](guides/canonical-content-storage-roadmap.md), and [memory influence and explainability](guides/retrieval-explainability-roadmap.md).

## Build

Requirements: **C++17**, **CMake 3.20+**, and a supported C++ toolchain (Windows, Linux, macOS).

~~~bash
git clone --recursive https://github.com/LimiNode/agent-memory-cpp.git
cd agent-memory-cpp

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_EXAMPLES=ON

cmake --build build --config Release
ctest --test-dir build --build-config Release --output-on-failure
~~~

Optional MDBX support is **OFF by default**. For the C1 adapter, configure a separate build with `-DAGENT_MEMORY_ENABLE_MDBX=ON` and provide the declared `libmdbx` / `mdbx-containers` dependencies (submodules, parent targets, or installed dependencies).

The target is `agent_memory::agent_memory`. The core aggregate header is `<agent_memory.hpp>`; optional MDBX adapters have their own infrastructure headers. See [examples](examples/) and [CMake options](cmake/AgentMemoryOptions.cmake) for the currently available surface.

## Roadmap and evaluation

- [Milestones](guides/milestones.md) — normative implementation scope.
- [Coverage audit](guides/retrieval-roadmap-coverage.md) — implemented versus contracts and research.
- [Evaluation roadmap](guides/evaluation-roadmap.md) and [experiments](guides/experiments/) — baselines, receipts and reproducibility.
- [Memory-feedback stability F0](guides/experiments/2026-10-07-memory-feedback-stability-protocol.md) — how repeated retrieval, recency refresh and cooldown could distort ranking; **protocol, not a successful experiment**.
- [Project comparison](guides/project-comparison.md) — alternatives, strengths, limitations and selection guidance.

## Non-goals

A hosted memory platform, universal database abstraction, distributed transaction manager, agent runtime, mandatory Python/LLM dependency, or unsupported claims of superior speed/accuracy.

## License

[MIT](LICENSE).
