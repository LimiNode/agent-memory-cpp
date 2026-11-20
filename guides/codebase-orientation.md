# Codebase Orientation

This is a navigation map, not a capability or release claim. For status and
acceptance boundaries, use [Milestones](milestones.md),
[Project overview](project-overview.md) and the relevant owner guide.
Open experimental PRs are not part of this map until integrated.

## Repository layout

- `src/agent_memory/`: the C++17 static library, with public headers beside
  their implementation files.
- `tests/`: domain, adapter, integration and consumer-style regressions;
  registration is in `tests/CMakeLists.txt` and the root `CMakeLists.txt`.
- `tools/`: qualification runners, benchmark executables and receipt validators;
  these are not library runtime dependencies.
- `examples/`: small non-interactive consumers.
- `cmake/`: dependency wiring, feature options and installed-package support.
- `external/`: flat optional dependency submodules, including `libmdbx` and
  `mdbx-containers`.
- `guides/`: normative topic owners, roadmaps and explicitly scoped evidence.
  See [Experiment notes](experiments/README.md) for historical result handling.

## Library boundaries and entry points

| Area | Existing entry points | Responsibility and owner |
| --- | --- | --- |
| Domain | `domain/`, `domain.hpp` | Identifiers, canonical content, KnowledgeUnits, artifacts, source bindings, prompt composition and shared SHA-256. No MDBX dependency. |
| Storage | `storage/`, `storage.hpp` | Backend-neutral contracts and reference implementations: canonical revisions/editing, KnowledgeUnits, projections, publication admission, prompt profiles, identity mapping and raw blobs. See [Canonical content storage](canonical-content-storage-roadmap.md) and [Linked derived knowledge](linked-derived-knowledge.md). |
| Ingestion | `ingestion/`, `ingestion.hpp` | Resource import/reindexing contracts; `ResourceIndexer` remains a pre-chunked dense prototype, not the failure-atomic product importer. See [Resource reindexing](resource-reindexing.md). |
| Lexical and vector indexes | `lexical/`, `index/` and their aggregate headers | Tokenization/BM25, exact vector baselines and optional binary representations. Indexing does not own retrieval policy or source authority. |
| Retrieval | `retrieval/`, `retrieval.hpp` | Hybrid and projection-aware retrieval, revision-bound reads and traces. See [Retrieval execution](retrieval-execution-roadmap.md). |
| Memory | `memory/`, `memory.hpp` | Memory value types. Context and prompt contracts have their own domain/storage owners; see [Context building](context-building.md). Not a general agent runtime. |
| Evaluation | `eval/`, `eval.hpp` | Dataset/run types, quality metrics and representation evaluation helpers. External data loading and experiment execution belong in tooling. |
| Optional MDBX | `infrastructure/mdbx/`, `infrastructure/mdbx.hpp` | Concrete persistent stores, indexes, publication/frontier readers, hydration, ingestion and retrieval facades. Backend types do not leak into domain contracts. |

Use the actual per-area aggregate header to discover exported classes rather
than maintaining an exhaustive filename list here. Internal headers are not a
consumer API. The architecture and dependency direction are owned by
[Architecture](architecture.md).

## Persistent paths

`MdbxStorageContext` owns the shared optional storage context.
`MdbxCanonicalContentStore` provides the existing canonical revision path;
`MdbxKnowledgeUnitStore` and `MdbxProjectionStore` persist their separate
contracts. Publication storage, fenced admission and current-read
revalidation have distinct responsibilities. A stored manifest alone does
not establish authoritative freshness.

`MdbxRawMarkdownProductIngestion`, `MdbxKb0RetrievalFacade` and the verified
hydration adapters compose narrower contracts at the infrastructure boundary.
Their presence does not imply activation of every proposed backend or feature.
The DBI/profile and migration contracts remain in their owner guides and
qualification receipts, not in this orientation map.

MDBX is disabled by default and gated by `AGENT_MEMORY_HAS_MDBX`.
Source dependency wiring adds the flat `external/libmdbx` target before
`external/mdbx-containers`. JSON, compression and other optional features
must retain their declared on/off build boundaries; see
[Dependencies](dependencies.md) and [Build and test](build-and-test.md).

## Consumer includes and extension work

Consumers use the `agent_memory/` prefix or `<agent_memory.hpp>`. The build
interface exposes `src/`; installed consumers use the exported CMake package.
`core::LibraryInfo` is library metadata, not an application facade.

Add behavior to its existing area and follow its owner contract. Create a new
area only for real implementation needs. Keep parser/provider/MDBX details
behind optional adapters, retain exact source revisions and deterministic
identities, and do not move orchestration or inference into the library.

Before a change, read [Coding agent workflow](coding-agent-workflow.md).
Run the narrowest relevant checks from [Build and test](build-and-test.md),
including actual runtime adapter tests when changing persistence. A green
MDBX-off build is not evidence that an MDBX path executed.
