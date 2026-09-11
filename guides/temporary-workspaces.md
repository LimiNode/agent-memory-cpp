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

The canonical DE-1M manifest is produced by
`tools/agent-memory-bench/materialize-frozen-de1m-manifest.py`. It accepts only
explicit payload paths, validates exact byte sizes, computes hashes, and does
not download or copy data implicitly.
