# Temporary research workspaces (legacy compatibility)

The normative local layout is defined in
[`workspace-layout.md`](workspace-layout.md). The old external root below is
kept only as a migration reference for existing paths; do not create new
worktrees there.

Large generated payloads, cloned repositories, raw benchmark reports, and
review attachments must not accumulate directly under `E:\_repoz`. Use:

```text
E:\_repoz\agent-memory-cpp\.worktrees\
```

Each Git experiment gets a named subdirectory, for example
`.worktrees\postbacklog-rthq-gate`. Keep large payloads and raw reports under
`.artifacts\<experiment>`, not inside the worktree.
Record the absolute workspace/payload paths and SHA-256 values in the compact
experiment receipt. Do not delete a workspace merely because it is clean:
first check its current branch, unpushed commits, unique artifacts, and whether
its evidence is already archived.

## Branch/worktree discipline

Research work uses one registered Git worktree per active branch and keeps
external payloads under the canonical checkout:

```text
<repo-root>/.worktrees/<experiment>/         Git checkout only
<repo-root>/.artifacts/<experiment>/payload/ raw vectors, codes and reports
<repo-root>/.artifacts/<experiment>/receipts/ local provenance manifests
```

Small committed receipts belong in the experiment notes or their declared
repository location. The old external layout below remains a historical
migration reference for existing work; do not rewrite these paths as if they
had already moved:

```text
E:\_repoz\agent-memory-workspaces\<experiment>\
  repo\
  payload\
  receipts\
```

Do not create new clones or branch directories directly under
`E:\_repoz` or the historical external root. Before starting work, record
`git remote -v`, the exact branch, the worktree path and
`git status --short`. Before handoff, push the branch, record the remote head
SHA, and leave the worktree clean. If an older scattered checkout is
discovered, inspect its branch, unique commits and payload references before
migrating or archiving it.

The repository policy and index template are documented in
[`research-workspaces.md`](research-workspaces.md); the historical external
root may mirror that table in
`E:\_repoz\agent-memory-workspaces\README.md` during migration.

The canonical DE-1M manifest is produced by
`tools/agent-memory-bench/materialize-frozen-de1m-manifest.py`. It accepts only
explicit payload paths, validates exact byte sizes, computes hashes, and does
not download or copy data implicitly.
