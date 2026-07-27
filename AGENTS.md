# AGENTS.md

This file is the entry point for AI coding agents working in
`agent-memory-cpp`. Keep it short; put detailed rules in topic guides under
`guides/`.

The project is an embedded C++17 static library for AI-agent memory, retrieval,
indexing, ingestion, storage, and context construction. It is not a general
agent framework.

## Read First

- [Critical defaults](guides/critical-defaults.md) - mandatory rules for every
  repository task.
- [Coding agent workflow](guides/coding-agent-workflow.md) - default workflow
  for file-editing tasks.
- [Project overview](guides/project-overview.md) - scope, current status,
  goals, and non-goals.
- [Architecture](guides/architecture.md) - DDD-like boundaries, dependency
  direction, and planned source areas.
- [Milestones](guides/milestones.md) - normative M0/M1/M2 scope and
  capability manifest.
- [Knowledge activation](guides/knowledge-activation-roadmap.md) - domain maps,
  playbooks, soft routing, and context-planning concepts.
- [Agent runtime integration](guides/agent-runtime-integration-roadmap.md) -
  optional cognitive-runtime trace, perspective, procedure, and reconciliation
  contracts.
- [Codebase orientation](guides/codebase-orientation.md) - current repository
  map and extension points.
- [Build and test](guides/build-and-test.md) - CMake options, local checks, and
  CI expectations.
- [Experiment notes](guides/experiments/README.md) - how to record hypothesis
  checks, benchmark evidence, and exploratory results.
- [Dependencies](guides/dependencies.md) - flat `external/` submodules and
  optional dependency wiring.
- [Embeddings](guides/embedding.md) - embedding contracts and backend adapter
  direction.
- [Coding style](guides/coding-style.md) - naming, file layout, comments, and
  include guards.
- [Commit conventions](guides/commit-conventions.md) - commit format when the
  user asks for a commit.

## Critical Defaults

- Check `git status --short` before editing and do not overwrite user changes.
- Keep edits scoped to the requested PR or task.
- Preserve C++17 as the language baseline.
- Keep core functionality in a static library. Do not turn the project into a
  header-only library unless the user explicitly changes that direction.
- Keep public headers and `.cpp` files side by side under `src/`.
- Isolate optional dependencies behind implementation files and adapter layers.
- Keep source dependencies flat as Git submodules under `external/` when the
  project owns the dependency checkout.
- Keep embedding APIs backend-independent. Do not fork chat/generation wrappers
  just to add embeddings.
- Keep storage, indexing, retrieval, memory strategies, ingestion, and context
  formatting as separate concerns.
- Do not add agent orchestration, browser automation, participant simulation,
  TTS/ASR, LLM inference, or generic prompt-template collections to the core
  library.
- Use non-reserved include guards for project-owned `.hpp` and `.h` headers:
  `AGENT_MEMORY_HEADER_<PATH>_<FILE>_<EXT>_INCLUDED`.
- All code comments and Doxygen text must be in English.
- All changes reach `main` through PRs unless the user explicitly asks for a
  direct push.
