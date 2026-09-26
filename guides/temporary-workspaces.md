# Temporary research workspaces

Large generated payloads, cloned repositories, raw benchmark reports, and
review attachments must not accumulate directly under `E:\_repoz`. Use:

```text
E:\_repoz\agent-memory-workspaces\
```

Each experiment gets a named subdirectory, for example
`agent-memory-workspaces\postbacklog-rthq-gate`. Keep the Git repository and
small source/config files there; keep DE-1M vectors, generated codes, databases,
and raw reports in a separate payload subdirectory that is excluded from Git.
Record the absolute workspace/payload paths and SHA-256 values in the compact
experiment receipt. Do not delete a workspace merely because it is clean:
first check its current branch, unpushed commits, unique artifacts, and whether
its evidence is already archived.

## Branch/worktree discipline

Research work must use one canonical workspace per active branch:

```text
E:\_repoz\agent-memory-workspaces\<experiment>\
  repo\       # one Git checkout/worktree, named after the remote branch
  payload\    # ignored raw vectors/codes/reports
  receipts\   # small committed or release-bound provenance manifests
```

Do not create additional clones or branch directories directly under
`E:\_repoz`. Before starting work, record `git remote -v`, the exact branch,
the worktree path, and `git status --short`. Before handoff, push the branch,
record the remote head SHA, and leave the worktree clean. If an older scattered
checkout is discovered, do not delete it automatically: inspect its branch,
unique commits, and payload references, then migrate or archive it under this
layout.

The repository policy and index template are documented in
[`research-workspaces.md`](research-workspaces.md); the external workspace
root may mirror that table in `E:\_repoz\agent-memory-workspaces\README.md`.

The canonical DE-1M manifest is produced by
`tools/agent-memory-bench/materialize-frozen-de1m-manifest.py`. It accepts only
explicit payload paths, validates exact byte sizes, computes hashes, and does
not download or copy data implicitly.
