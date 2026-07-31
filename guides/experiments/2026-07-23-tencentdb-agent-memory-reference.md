# TencentDB Agent Memory Reference Review

## 2026-07-23

### Context

Reviewed TencentCloud/TencentDB-Agent-Memory as an architectural and benchmark
reference for `agent-memory-cpp`. The review used the upstream GitHub
repository snapshot cloned into `tmp/tencentdb-agent-memory` and public GitHub
README / issue pages.

`agent-memory-cpp` context:

- Initial note: `9b73202 docs(guides): add addressable compression reference`.
- Boundary correction reviewed here:
  `02dfc5e docs(guides): keep storage primitives domain-neutral`.
- Branch: `pr85-rerank-prepare-cost`.

### Question

Can TencentDB Agent Memory provide useful comparison targets for this project,
and does its architecture imply additional storage requirements for
`mdbx-containers`?

### Setup

- Source: `https://github.com/TencentCloud/TencentDB-Agent-Memory`
- Local snapshot path: `tmp/tencentdb-agent-memory`
- Upstream snapshot:
  - commit: `45e6e80ae2e63b65fad0d89f5e13171229c8f295`
  - commit date: `2026-07-19T23:15:09+08:00`
  - subject: `Update README.md (#535)`
- Inspection date: `2026-07-23`
- Primary inspected files:
  - `README.md`
  - `package.json`
  - `src/core/tdai-core.ts`
  - `src/core/store/types.ts`
  - `src/core/hooks/auto-recall.ts`
  - `src/core/hooks/auto-capture.ts`
  - `src/core/record/l1-writer.ts`
  - `src/core/record/l1-extractor.ts`
  - `src/core/scene/scene-extractor.ts`
  - `src/core/persona/persona-generator.ts`
  - `src/offload/types.ts`
  - `src/offload/storage.ts`
  - `src/offload/l3-helpers.ts`
- Inspected test / benchmark-like files and directories:
  - `vitest.config.ts`
  - `vitest.e2e.config.ts`
  - `hermes-plugin/memory/memory_tencentdb/tests/`
  - `src/offload/benchmark-token-estimate.ts`
  - `src/offload/*.test.ts`
  - `src/utils/*.test.ts`
- Reproduction-oriented inspection commands:

```powershell
git -C tmp/tencentdb-agent-memory rev-parse HEAD
git -C tmp/tencentdb-agent-memory log -1 --format='%H%n%cI%n%s'
rg -n "WideSearch|SWE-bench|AA-LCR|PersonaMem|benchmark|bench|evaluation|eval" `
  tmp/tencentdb-agent-memory/README.md `
  tmp/tencentdb-agent-memory/package.json `
  tmp/tencentdb-agent-memory/src `
  tmp/tencentdb-agent-memory/tests `
  tmp/tencentdb-agent-memory/test `
  tmp/tencentdb-agent-memory/hermes-plugin 2>$null
Get-ChildItem -LiteralPath tmp/tencentdb-agent-memory -Recurse -Force -File |
  Where-Object {
    $_.FullName -notmatch '\\.git|node_modules|dist|build' -and
    $_.FullName -match '(test|spec|bench|eval|hermes)'
  }
Get-Content -LiteralPath tmp/tencentdb-agent-memory/package.json
```

### Expected Result

The project was expected to overlap with `agent-memory-cpp` around layered
long-term memory, hybrid retrieval, local storage, and context compression.
The likely useful output was a list of comparable benchmark axes, not a direct
dependency decision.

### Actual Result

TencentDB Agent Memory is a TypeScript / Node.js OpenClaw and Hermes memory
plugin with:

- host-neutral `TdaiCore` and adapters;
- L0 raw conversation capture;
- L1 extraction into three concrete memory types:
  `persona`, `episodic`, `instruction`;
- L2 scene blocks stored as Markdown;
- L3 `persona.md` synthesis;
- SQLite + sqlite-vec + FTS5 local backend and Tencent VectorDB backend;
- hybrid keyword/vector retrieval with RRF;
- optional short-term context offload where raw tool results go to `refs/*.md`
  and compact Mermaid MMD projections carry `node_id` drill-down refs.

Public README benchmark claims:

| Area | Benchmark | Baseline | With plugin | Extra signal |
|---|---:|---:|---:|---|
| Short-term | WideSearch | 33% success | 50% success | 221.31M -> 85.64M tokens |
| Short-term | SWE-bench | 58.4% success | 64.2% success | 3474.1M -> 2375.4M tokens |
| Short-term | AA-LCR | 44.0% success | 47.5% success | 112.0M -> 77.3M tokens |
| Long-term | PersonaMem | 48% accuracy | 76% accuracy | no token figure |

No committed runner for the README WideSearch / SWE-bench / AA-LCR /
PersonaMem table was found in snapshot `45e6e80`. The repository does contain
`src/offload/benchmark-token-estimate.ts`, but it benchmarks a fast token
estimator against `tiktoken cl100k_base`; it is not the long-horizon memory
evaluation harness behind the README table. Other visible tests are mostly
unit/recovery tests (`vitest`, plus Hermes Python recovery/shutdown tests).

GitHub issue #106 is treated only as supporting external signal: it asks the
project to add reproducible memory evaluation benchmarks and suggests axes such
as storage integrity, retrieval precision, clustering, forgetting
directionality, multi-hop reasoning, and deep retrieval. It is not an official
proof that no internal Tencent benchmark harness exists.

### Interpretation

TencentDB Agent Memory is a strong architecture reference, but not a direct
library dependency or a currently reproducible benchmark baseline for this
repo. Its most important lesson for `agent-memory-cpp` is not the fixed L0-L3
hierarchy; it is the downstream invariant that compact memory should remain
addressable:

```text
compact projection -> evidence/source relation -> canonical SourceRef/ResourceId
```

This should map to `agent-memory-cpp` through typed components, canonical
`SourceRef` / `ResourceId`, and application-owned relation tags over generic
graph storage rather than through new domain-specific `mdbx-containers` API.

### TZ Impact

The initial `9b73202` TZ update overfit the upstream storage layer by naming
`AddressableCompression`, `ArtifactRefTable`, `DerivationIndex`, and
`DrillDownIndex` in `mdbx-containers` terms. Follow-up correction:

- keep `AddressableCompression` / `ProgressiveDisclosure` as
  `agent-memory-cpp` capabilities, not upstream `mdbx-containers`
  capabilities;
- represent derivation, evidence, contradiction, summary/detail, and
  drill-down links through existing generic `graph_edges_by_src` /
  `graph_edges_by_dst` physical orientations;
- keep artifact/source identity under canonical `SourceRef` / `ResourceId`
  unless a later `agent-memory-cpp` roadmap adds an application-owned
  descriptor;
- leave only generic relation-helper guidance and storage-only acceptance
  criteria in `guides/mdbx-containers-extension-tz.md`.

### Limitations

- The public Tencent benchmark numbers were not independently reproduced.
- Snapshot `45e6e80` did not reveal the WideSearch/SWE-bench/AA-LCR/PersonaMem
  runner setup used for the README table.
- The local review did not run Tencent tests; it inspected source and docs.
- Their benchmark claims are host/runtime-level, while `agent-memory-cpp` is a
  lower-level C++ storage/retrieval substrate.

### Follow-up Checks

- Track Tencent issue #106 for any reproducible benchmark harness.
- Add our own benchmark axis for progressive disclosure:
  traceability coverage, drill-down latency/read amplification, layer selection
  accuracy, compression utility, and explicit failure classification.
- When `KnowledgeUnit` work starts, ensure derived profile/scenario claims keep
  evidence and contradiction links over generic graph relations instead of
  being promoted into raw facts.
- If artifact descriptors become necessary, first reconcile them with
  `SourceRef`, `ResourceId`, descriptor size limits, and external raw artifact
  retention policy in `agent-memory-cpp`.
