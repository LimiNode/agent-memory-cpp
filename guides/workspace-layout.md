# Local Workspace Layout

This repository has one canonical checkout and one canonical root for local
working data:

```text
E:\_repoz\agent-memory-cpp\
    .worktrees\<name>       registered Git worktrees only
    .build\<name>           build and test output
    .artifacts\<name>       research data, reports, models, and evidence
    .tmp\<name>             short-lived disposable files
```

These directories are local-only and ignored by Git. They must not be
committed.

## Git worktrees

Every new worktree must be created below `.worktrees/`, using a short stable
purpose name such as `.worktrees/r1-federated-canonical-reads`.

Move an existing registered worktree with `git worktree move`; do not move its
directory with Explorer or `Move-Item`. A dirty worktree is not disposable.
Worktrees containing submodules may require a dedicated migration step because
Git refuses to move them directly. Detached worktrees and worktrees for
`main`, an open PR, or preserved research must remain explicitly identified.

The repository root remains the canonical checkout. It must not be replaced by
a worktree or used as a disposable build directory.

## Builds and temporary files

Build output belongs in `.build/`, separated by configuration or task. Files
that can be regenerated and have no retention value belong in `.tmp/`; include
an experiment or date in the directory name when they may outlive one command.
The historical `tmp/` and `build-*` locations are migration-only.

## Research artifacts

Research outputs that are not committed to the repository belong in
`.artifacts/`, one directory per experiment or evidence package. Each retained
package must include a small manifest containing, when applicable:

- experiment or artifact identifier;
- source branch and exact commit SHA;
- input/data/qrels manifest identities;
- producer command or script revision;
- creation time and retention class;
- whether the result is reproducible, superseded, or unique evidence.

Large raw payloads and model files are artifacts, not worktrees. Do not place
them below `.worktrees/`.

## Migration and cleanup

Before removing a registered worktree, verify its path with
`git worktree list --porcelain`, inspect `git status --short` in that worktree,
and check the branch SHA against local branches, remote-tracking refs, tags,
open PRs, and preservation records. Remove it with `git worktree remove` only
after that check.

Unregistered build directories may be removed after confirming that no process
uses them. Unregistered research artifacts may be removed only after their
manifest, exact duplicate, or archival replacement has been verified. Never
delete an entire legacy root merely because its name contains `tmp`, `pr`, or
`workspace`: legacy roots may contain registered worktrees and unique evidence.
