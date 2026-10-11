# Research workspace index and lifecycle (legacy compatibility)

The normative layout is defined in
[`workspace-layout.md`](workspace-layout.md). Existing rows below describe
legacy paths and must be migrated without deleting their commits or payloads.

All active research checkouts and large payloads belong under:

```text
E:\_repoz\agent-memory-cpp\.worktrees\<experiment>\
```

Each experiment uses one checkout/worktree and one remote branch. The minimum
index row is:

| experiment | branch | checkout | payload | state |
| --- | --- | --- | --- | --- |
| `<name>` | `<remote branch>` | `<absolute repo path>` | `<absolute payload path>` | `active` / `blocked` / `archived` |

Before work starts, capture the remote URLs, branch, worktree path, and clean
status. Before handoff, push the branch, record the remote head SHA in the
receipt, and leave the checkout clean. Payloads stay outside Git and are bound
by SHA-256 in receipts.

The current residual-correction wave uses the existing checkout
\`E:\_repoz\agent-memory-rslm-faithful\` as a transitional location. New work
should use a child checkout under
\`E:\_repoz\agent-memory-cpp\.worktrees\<experiment>\`, rather than adding
another top-level clone under \`E:\_repoz\`. Migration of this checkout must not
delete unreviewed commits or payloads.

The historical external workspace root
\`E:\_repoz\agent-memory-workspaces\<experiment>\` is retained in the index
below as a migration reference for existing checkouts and payloads. It is not a
location for new work.

The recovered canonical DE-1M payload is also transitional for now. Its current
index row is:

| experiment | branch | checkout | payload | state |
|---|---|---|---|---|
| `canonical-de1m-source` | n/a (payload-only) | `E:\_repoz\agent-memory-workspaces\canonical-de1m-source` | `payload\` junction to legacy `E:\_repoz\agent-memory-cpp\tmp\native-ann-confirmation-v1\de-1m\e5` | recovered; junction active |
| `fidelity-closure-v1` | `research/fidelity-closure` | `E:\_repoz\agent-memory-workspaces\fidelity-closure-v1` | `fidelity-closure-payload-v1\` | active; research wave closure |
| `fidelity-closure-payload-v1` | n/a (payload-only) | `E:\_repoz\agent-memory-workspaces\fidelity-closure-payload-v1` | LSQ/PLSQ models and codes bound by manifest | active; external artifacts |

The payload is bound by the E5 `manifest.json` and by the hashes recorded in
`2026-09-24-thq-tq1-residual-correction-next-wave.md`. A runner must accept an
explicit payload root and must not search arbitrary clones or silently copy
large files.
