# Conversation P4-2/P4-3 integration receipt

- Date: 2026-11-26. Owner: conversation storage/ingestion.
- Status: **INTEGRATED / POST-MAIN CI AND PACKAGING PASS**.
- Scope: guarded integration authorized by the owner's 2026-11-26 assignment.
  This receipt authorizes no release, migration, deployment or automatic merge
  of new P4-4/P4-5 Drafts.
- Contract owner: [Phase 4 plan](../conversation-durable-store-phase4-plan.md).

## Accepted source and ordinary merges

| Boundary | Commit | Tree |
| --- | --- | --- |
| Starting main | `2c895c9812128dcfc3ccd07ae84a7175e5ff371c` | `86ec0e739dd382a45b0c6bd89302a276abbc8dfa` |
| Accepted #702 source | `b546791191bb15b89436b7b09e25de08035a234b` | `917cf985c5f4f6076382ad078d4db9ea3937542c` |
| #702 main merge | `6e5a4d4895b72143c04a5e3683d7b66255dee604` | `917cf985c5f4f6076382ad078d4db9ea3937542c` |
| Accepted #703 source | `fb8656176fcaa4994cb29aa9a1fc497519a932a8` | `ffe987701da42600079c36d54b69537d75532c6c` |
| #703 protected-branch integration HEAD | `498a9a8cfcaf639951e8a2173f254214f0515d08` | `ffe987701da42600079c36d54b69537d75532c6c` |
| Final main / #703 merge | `8a092d515f99ed34e50ffbc7642cd2fb80e4ef8d` | `ffe987701da42600079c36d54b69537d75532c6c` |

Both PRs used ordinary protected-branch merge with exact-head matching. #702
merged at `2026-11-26T19:06:43Z`; #703 at `2026-11-26T19:34:10Z`. Original source
history and branches are preserved. No squash, rebase, force-push, admin bypass,
duplicate cherry-picks or branch deletion was used.

#703 was retargeted to main only after #702 post-main CI/Packaging passed. Its
ordinary merge was initially refused because the branch was BEHIND main. A
normal merge of main into the source branch produced `498a9a8c`, parents
`fb8656176fcaa4994cb29aa9a1fc497519a932a8` and
`6e5a4d4895b72143c04a5e3683d7b66255dee604`. The tree remained exactly the accepted
#703 tree; no source/test/doc/dependency edits were made during integration.
Fresh checks passed on that exact integration HEAD before the protected merge.

Final main has parents `6e5a4d4895b72143c04a5e3683d7b66255dee604` and
`498a9a8cfcaf639951e8a2173f254214f0515d08`. `git show -s --format='%H %T %P'
liminode/main` verifies this commit/tree/parent relationship.

## Hosted checks — distinct executions

| Candidate | CI | Packaging |
| --- | --- | --- |
| #702 accepted HEAD | [45850534246](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45850534246): PASS, 14 success / 16 conditional skips | [45850534204](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45850534204): PASS, 6/6 |
| #702 actual main merge | [45881153706](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45881153706): PASS, 14 success / 16 conditional skips | [45881153603](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45881153603): PASS, 6/6 |
| #703 accepted source HEAD | [45859831981](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45859831981): PASS, 32 success / 2 conditional skips | [45859831860](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45859831860): PASS, 6/6 |
| #703 protected-branch integration HEAD | [45882637877](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45882637877): PASS, 32 success / 2 conditional skips | [45882637827](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45882637827): PASS, 6/6 |
| #703 actual main merge | [45884614646](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45884614646): PASS, 32 success / 2 conditional skips | [45884614214](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45884614214): PASS, 6/6 |

Raw final metadata is retained locally under `.artifacts/p4-2-post-main-*.json`,
`.artifacts/p4-3-integration-head-*-final.json` and
`.artifacts/p4-3-post-main-*-final.json`. Hosted run links above are the published
execution identities; generated job dumps are not committed.

The [P4-2](../experiments/2026-11-25-conversation-durable-p4-2.md) and
[P4-3](../experiments/2026-11-26-conversation-durable-p4-3.md) notes retain original
local GNU/MSVC, MDBX-off, sanitizer, consumer, process/fault and independent
review evidence. These are historical source-bound executions, not new local
runs on the merge commits. Exact tree equality establishes no source delta;
post-main hosted checks remain separately identified above.

## Remaining gates

The private P4-1/P4-2/P4-3 backend is integrated, not a complete installed durable
Conversation API. The [P4-4 admission audit](2026-11-26-conversation-p4-4-admission-gate.md)
records the confirmed Edit/Tombstone/Restore DTO/witness boundary and explicit
owner choices. No public API, witness/profile version, schema marker or DBI
delta is adopted by that proposal. Correction remains distinct from a new
message-state event, and raw append must not bypass coordinated source mapping.

Full Current oracle parity/benchmark, materialized/checkpoint comparison,
65,537 committed-admission scale fixture and public durable installed/relocated
consumer are **NOT RUN**. P4-4 and P4-5 remain separate future Drafts; Phase 4
full/public activation is HOLD pending the reviewed contract decision and actual
qualification. The unchanged predeclared
[Current experiment](../experiments/2026-11-24-conversation-durable-current-plan.md)
is not replaced by this integration receipt.

The parallel [mdbx-containers #336](https://github.com/LimiNode/mdbx-containers/pull/336)
fix remains a separate unmerged Draft at
`d65aa8bafce84f15b3f4f006b5ef04b2805dc81f`. Its
[final receipt](https://github.com/LimiNode/mdbx-containers/pull/336#issuecomment-7201740808)
records exact-head CI/Archcheck PASS, independent source review READY and the
supported/supplemental toolchain boundaries. This is not downstream durable
fault qualification. Its acceptance must precede a downstream
dependency-update PR and repeat durable allocation matrices. No dependency
gitlink changed here; idempotent retry and uncertain-outcome recovery remain
required. Neither process-crash tests nor the helper fix prove device power-loss
or native-commit-internal failure guarantees.

No further cleanup was performed during integration. Unique logs, configurations,
source data, unrelated worktrees and branches remain preserved. New build/worktree
storage uses C: to avoid filling E: again.

## Documentation review

An independent read-only pass reviewed these six documentation changes against
the unchanged source and GitHub commit/tree/run metadata: READY, no confirmed
contract drift or source blocker. It did not edit files or run builds. At review
time #703 post-main Packaging was PASS and CI was pending; root subsequently
verified completed SUCCESS on exact main HEAD with 32 success / 2 conditional
skips, recorded above. No source change followed that review. Local checks:
relative links in all six changed documents, staged/unstaged `git diff --check`,
historical research preservation and PCA12 residual preservation passed. No new
Current, scale or source-code build evidence is inferred from those doc checks.
