# Critical Defaults

Mandatory rules for AI coding agents working in `agent-memory-cpp`.

- Check `git status --short` before editing and do not overwrite user changes.
- Prefer project MCP/code graph tools for code discovery when they are available;
  otherwise use `rg` or `rg --files`.
- Keep edits scoped to the requested task and the relevant local style.
- Preserve C++17 compatibility. Do not introduce compiler extensions.
- Use the static library target `agent_memory` as the primary build product.
- Avoid a header-only architecture for core functionality. Keep dependency-heavy
  code in `.cpp` files or isolated adapters.
- Keep headers and implementation files side by side under `src/`.
- Keep public includes under the `agent_memory/` prefix.
- Keep project-owned source dependencies as flat Git submodules under
  `external/`; do not hide them as nested dependency checkouts.
- Keep embedding contracts backend-independent. Add concrete embedding backends
  as optional adapters, not as forks of chat/generation wrappers.
- Separate storage, embedding, indexing, retrieval, memory strategies, ingestion,
  and context assembly. Do not collapse behavior into a single facade.
- Keep derived records traceable to their source resource when adding ingestion,
  indexes, or mutable memory features.
- Treat derived representations as optional, versioned projections: preserve
  the original evidence and its exact revisions, and do not claim a retrieval
  improvement without matched no-retrieval/chunk/derived arms plus downstream
  answer, provenance, cost and stale-update checks.
- Prefer targeted resource reindexing over whole-store rebuilds for source
  updates.
- Persist source and chunk text as UTF-8 by default. Use UTF-32 only as a
  temporary tokenizer/code point buffer or benchmarked derived artifact.
- Lexical search results should be addressable by `ChunkId`; lexical postings
  should preserve `ResourceId` and generation for targeted reindexing.
- Optional infrastructure adapters must depend inward on core contracts, not the
  other way around.
- Do not add general agent orchestration, browser automation, participant
  simulation, TTS/ASR, LLM inference, or prompt-template collections to the core
  library.
- Use non-reserved include guards for project-owned `.hpp` and `.h` headers:
  `AGENT_MEMORY_HEADER_<PATH>_<FILE>_<EXT>_INCLUDED`.
- Public/configuration macros use the `AGENT_MEMORY_` prefix and should not
  reuse include-guard names.
- All comments and Doxygen text must be in English.
- For code changes, run the narrowest relevant CMake configure, build, and CTest
  checks before handing off.
- All changes reach `main` through PRs unless the user explicitly asks for a
  direct push.
