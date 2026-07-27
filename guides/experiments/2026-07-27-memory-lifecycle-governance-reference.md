# Memory Lifecycle Governance Reference

Date: 2026-07-27

Context: roadmap expansion after external proposal review in
`C:\Users\User\.codex\attachments\adbc32dc-7626-475b-8392-57b5b0e7d653\pasted-text.txt`.

## Question

Which ideas from the proposal set should become `agent-memory-cpp` roadmap
items, and which should remain in sibling agent/runtime projects?

## Sources Checked

- `Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers`
  (arXiv:2603.07670, https://arxiv.org/html/2603.07670v1).
- Graphiti / Zep documentation for temporal graph and search behavior:
  https://help.getzep.com/graphiti/getting-started/overview and
  https://help.getzep.com/graphiti/working-with-data/searching.
- Mem0 public repository (https://github.com/mem0ai/mem0) notes for entity
  linking, multi-signal retrieval, temporal retrieval and memory benchmark
  axes.
- Letta / MemGPT public repository (https://github.com/letta-ai/letta) for
  stateful-agent memory positioning.
- Mind Modeling / ToM references were treated as ADELIA/runtime candidates,
  not as core storage contracts.

## Interpretation

The proposal set is useful for `agent-memory-cpp`, but only if split by
boundary:

- Core roadmap: lifecycle governance, bi-temporal validity, derivation graph,
  causal relation indexing, progressive retrieval, mutation policies and
  expanded memory evaluation.
- Storage substrate: generic typed tables, range/reverse/relation indexes,
  atomic multi-table writes and optional large-value/blob helpers.
- Sibling runtime/ADELIA: mental models, online consultation, workflow engines,
  UI, inference adapters and process reward models.

## Roadmap Result

Added `guides/memory-lifecycle-governance-roadmap.md` with AM-13 through AM-18:

- AM-13: bi-temporal knowledge;
- AM-14: abstraction and derivation graph;
- AM-15: causal memory relations;
- AM-16: progressive retrieval;
- AM-17: expanded memory evaluation harness;
- AM-18: policy-selectable mutation model.

## Limitations

No benchmark numbers were produced. This is a design/reference note, not an
implementation or performance experiment. Public sources were used for
directional architecture validation; final semantics remain project-owned.

## Follow-up Checks

- When M2 planning starts, map AM-13 through AM-18 to capability flags and DBI
  budget changes in `mdbx-containers-extension-tz.md`.
- Add concrete golden datasets for temporal, contradiction, deletion and causal
  recall before claiming benchmark parity with Graphiti, Mem0 or other systems.
