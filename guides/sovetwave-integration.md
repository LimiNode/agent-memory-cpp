# Project-local Sovetwave integration

This guide defines the reproducible development-tool integration for the
project. Sovetwave is optional host tooling; it is not a C++ dependency, a
runtime component, a release prerequisite, or a replacement for the
repository's engineering guides.

## Pinned source

- upstream repository: <https://github.com/LimiNode/sovetwave>
- release tag: `v0.5.0-beta.4`
- resolved commit: `2d9534de0168bfd2d2d217de8e3153a7999dd7bf`
- `SKILL.md` Git blob: `92beec2728508b1feae39e6b2f0311473bcc4d42`
- upstream MIT `LICENSE` Git blob: `daf9c006e677b8b01ec043d7563dbfc7ffce2c2f`

The tracked bootstrap script copies the exact upstream
`skills/sovetwave/` payload and the upstream MIT license after verifying the
tag, resolved commit and Git blob IDs. It does not copy upstream evaluations,
research fixtures, workflows or development files.

## Host discovery boundary

The Codex host used for this repository was observed to discover the project
skill from the ignored `.codex/skills/sovetwave` path (exposed as the local
`r0/sovetwave` skill root). No repository-local `.agents/skills` discovery was
available in that host configuration, so this integration targets `.codex`
only. The script does not modify global Codex or Claude configuration.

Because the host-owned directory is intentionally ignored, a fresh checkout
must run the bootstrap script before a session that needs the skill:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File guides/tooling/bootstrap-sovetwave.ps1
powershell -NoProfile -ExecutionPolicy Bypass `
  -File guides/tooling/bootstrap-sovetwave.ps1 -VerifyOnly
```

The second command verifies the installed `SKILL.md` blob against the pinned
source. A fresh agent session is required to observe discovery; an already
running session is not evidence that a newly installed skill was loaded.

## Precedence and scope

Project guides and `AGENTS.md` remain authoritative for C++17, naming,
include guards, CMake dependency resolution, MDBX ownership, package
compatibility and release policy. Sovetwave contributes review discipline and
engineering workflow guidance only. It must not authorize production include
migrations, release publication, or changes to public API and storage
contracts.

The hybrid include decision remains the one recorded by the include-policy
audit: matching-header-first for implementation translation units, domain
umbrellas for public entry points, and narrow leaf includes where they reduce
coupling. This integration does not add a second copy of the skill to the
repository and does not change production source files.

## Verification record

The bootstrap was exercised against the pinned release and then checked with
`-VerifyOnly`. The current host session exposed the installed project-local
skill as `r0/sovetwave`; this is host evidence, not a claim that every Codex or
Claude host implements the same discovery convention. A future host migration
must repeat the fresh-session discovery check rather than infer it from the
directory's presence.
