# Markdown Technical Preview release-candidate matrix

This is a readiness report, not a release approval. It evaluates the frozen
candidate main `dde38ae89fd8bef4be4e7306f2250a7fe3a094d4` after #611. No tag,
version publication, package upload, GitHub Release or production retrieval
activation is authorized by this document.

## Evidence baseline

| Surface | Evidence |
|---|---|
| Post-merge CI | [run 43224174466](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43224174466), SUCCESS; Full research CTest SKIPPED |
| Post-merge packaging | [run 43224174341](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43224174341), SUCCESS |
| Operational installed consumer | #611; A@1/reopen/A@2, generation/SHA-256/bytes, stale A@1, missing/corrupt CAS, typed budget, two-resource cold restore |
| Internal persistence regression | #611; post-join interruption, readable completed product, identity-preserving replay and later stale rejection |
| Local focused checks | #611: six MDBX tests, installed consumer, MDBX-off build, header probes, links, preservation and `git diff --check` PASS |
| Dependency baseline | libmdbx `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`; mdbx-containers `4ab32b5eab3cdfce95d1a6c0f05f566019793ec4`; C++17/CMake 3.20+ |
| Package version | `0.1.0`; #613 proposes ExactVersion policy and is still Draft/unreviewed |

The installed product evidence is Ubuntu MDBX in packaging CI, with an
additional local Windows/MinGW run. Windows/macOS default CI lanes validate
the MDBX-off/minimal package, not MDBX product execution. Do not generalize
the Ubuntu product result to those platforms.

## Capability decision

| Classification | Requirement | Evidence / gap | Owner and action |
|---|---|---|---|
| `BLOCKER_FOR_MARKDOWN_PREVIEW` | No unreviewed storage or hydration defect; exact bytes and stale evidence fail closed | #607/#611 executable path and CI are green | First agent: retain independent review and rerun exact-head evidence if candidate changes |
| `AWAITING_ANN_COMPATIBILITY_REVIEW` | Derived-index generation/source-frontier upgrade proof before any release claim including future reindexing | #612 is Draft, currently based on older main and not independently approved | Second agent: rebase/complete #612; first agent must not edit or merge it |
| `DOCUMENTATION_REQUIRED` | Conservative CMake package-version policy for `0.x` with no ABI promise | Current candidate still uses `SameMajorVersion`; #613 tests `ExactVersion` and remains Draft | Independent review and guarded integration of #613, if approved |
| `DOCUMENTATION_REQUIRED` | User-facing platform scope must distinguish Ubuntu MDBX from minimal Windows/macOS package lanes | This matrix and operations guide state the distinction | Preserve this wording in any release notes |
| `SAFE_TO_DEFER` | General migration tooling, online/atomic MDBX+CAS backup, process-kill durability and hot relocation | Explicitly outside #611 cold/offline contract | Future separately scoped operational work |
| `SAFE_TO_DEFER` | Tables/cells, retained images, timed transcripts, derived summaries, graph navigation, general connectors and coordinator runtime | Broad normalized-first profile remains separate | Additive vertical PRs only |
| `SAFE_TO_DEFER` | Stable ABI, cross-version migration and cross-platform MDBX file interchange | No such promise is made by `0.1.0` candidate | Reassess only with explicit compatibility evidence |

## Release contract

If eventually approved, the preview may advertise only C++17, the optional MDBX
backend, original Markdown import, bounded lexical candidate retrieval,
source-backed verified hydration, same-version reopen/host replay and the
qualified cold offline backup/restore procedure. It must not advertise the
broader normalized-first profile, real ANN/R4 serving, arbitrary connectors,
online backup atomicity, crash-safe multi-authority publication or stable ABI.

The backup procedure verifies the expected test resources and full CAS objects;
an MDBX file plus a CAS directory merely existing is not sufficient evidence.
`MdbxStorageContext::open()` can create a new empty database, and a wrong table
prefix can create an empty namespace, so host preflight must preserve and check
the original path, prefix, profile and identities.

## Decision boundary

Current decision: **release candidate frozen, publication blocked**. The next
decision requires independent #612 review/outcome, the #613 package-version
policy decision, a final exact-commit preflight and explicit release approval.
Until then, do not create a tag, publish a package, enable production retrieval
or merge unrelated research work.
