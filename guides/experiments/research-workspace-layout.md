# Research workspace layout

Research artifacts must have one canonical location and a reproducible lineage.
This avoids treating an incidental checkout, local `tmp/` directory, or CI
workspace as evidence storage.

## Canonical locations

| Kind | Canonical location | Git policy |
| --- | --- | --- |
| Repository source and PR branch | `E:\_repoz\agent-memory-cpp` and a named Git worktree | committed through a PR |
| Immutable source bundle | `E:\_repoz\agent-memory-workspaces\canonical-<dataset>-source\payload` | do not mutate from runners |
| Raw experiment payloads | `E:\_repoz\agent-memory-workspaces\<experiment-id>-v<N>` | outside Git; bind with SHA-256 |
| Compact note, receipt, and audit | `guides/experiments/` | committed with the runner |
| Disposable build output | `build-*` beneath the current worktree | never evidence |

An existing legacy source can remain read-only while it is byte-for-byte bound
to the canonical workspace by a junction and a receipt.  A move or copy becomes
canonical only after the source validator succeeds and the new receipt records
both source hashes.

## Naming and lifecycle

- Create one workspace per logical experiment line, named
  `<yyyy-mm-dd>-<family>-<purpose>-v<N>` or a concise equivalent such as
  `canonical-lsq-25k-strong-v1`.
- Keep all seeds for one protocol beneath that workspace; do not create
  unrelated top-level scratch directories.
- Put the command configuration, stdout/stderr, raw model/code payloads, and
  audit output in that same workspace.
- Record the workspace path, all source hashes, runner SHA, and raw artifact
  hashes in the committed receipt.  A note may summarize results but must not
  replace the receipt.
- A runner may read source bundles but must not download, copy, overwrite, or
  silently regenerate them.
- A partial or timed-out job is not `EXECUTED` evidence.  Preserve its log for
  scheduling diagnostics, but do not cite quality values or update a receipt.

## Worktree discipline

- Use a named worktree rooted at `E:\_repoz\agent-memory-<topic>` for one PR.
- Do not edit the primary checkout when it contains another task's changes.
- Before opening a PR, push the exact branch and verify its remote HEAD; a
  local-only commit is not reviewable evidence.
- Do not use an old worktree as a new source of truth.  Recreate it from the
  target branch or explicitly document a stacked-PR relationship.

## Evidence promotion

An experiment becomes a candidate for a product decision only after it has a
source-bound result, independent audit, complete storage accounting, and a
committed receipt.  Native serving claims additionally require full-corpus
payload materialization and the complete-cascade benchmark.  Historical folds
remain exploratory unless a separately pre-registered evaluation set confirms
them.
