# Conversation integration receipt and P4-0 design qualification

- Date: 2026-11-24. Owner: conversation storage/ingestion.
- Integration status: **SUCCESS**. P4-0: **proposed documentation only**.
- Physical owner: [P4-0 contract](../conversation-durable-physical-contract.md).
- Implementation sequence: [Phase 4 plan](../conversation-durable-store-phase4-plan.md).

## Main integration and source-PR disposition

Accepted candidate `e859049796d08986bba49724dacfac9985f2b978` had tree
`00cbb85105cb57a5a455dbb0e380238edef74cfa` and original main
`aea7d89f657c04b7b6388353530d9418a6a6ad81`. Repository rules require a PR,
strict required `CI gate`, resolved review threads and no force update. Promotion
used separate ready [PR #698](https://github.com/LimiNode/agent-memory-cpp/pull/698),
ordinary merge without admin override, rebase, squash or branch deletion.
No candidate code was changed or merge sequence reconstructed.

Final main is **`77a278bef338074227efa05365e83e967334f429`**; its tree is exactly
**`00cbb85105cb57a5a455dbb0e380238edef74cfa`**. The accepted candidate and these
five source heads were checked with `git merge-base --is-ancestor`:

| PR | Reviewed HEAD | Disposition after reachability proof |
| --- | --- | --- |
| #690 | `7bb88efc078f3b7c3c9a3fb49235d472bbaef717` | GitHub marked merged by ancestry |
| #693 | `f3ea824f73b6411dd040c786f86b0d6b7835cea8` | GitHub marked merged by ancestry |
| #696 | `3d7b868dae146890ffbce348e1048c2c3eaf1f3b` | Closed as already integrated, not separately merged |
| #694 | `a0c2d6bb31f867ac5eaa45deb9839f76d4f22c7d` | Closed as already integrated, not separately merged |
| #695 | `7d45c0dee70c488050d07170c0389a7851319f40` | Closed as already integrated, not separately merged |

GitHub also marked verification #697 merged by reachability; it was **not**
directly merged and remains the audit artifact. The five original no-ff merges
remain inside candidate history. Historical duplicate cherry-picks do not create
duplicate implementations. Final main's code/tests/CMake/examples/workflows
match the accepted candidate and the qualified #695 implementation. AMCE/AMXR
goldens are unchanged. No source branch was deleted. The
[posted receipt](https://github.com/LimiNode/agent-memory-cpp/pull/698#issuecomment-7139833567)
preserves exact evidence independently of this later documentation branch.

## Post-main checks actually executed

| Check at final main / exact accepted tree | Observed result |
| --- | --- |
| Hosted [main CI 45451429672](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45451429672) | SUCCESS: 32 jobs successful, 2 conditional skips |
| Hosted [main Packaging 45451429751](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45451429751) | SUCCESS: 6/6 |
| Local Windows GNU Release CTest | 64/64 |
| Local Windows MSVC Release CTest | 64/64 |
| Local Linux GNU Release CTest | 64/64 |
| Local Linux ASan/UBSan conversation subset | 8/8 |
| Local Linux ASan/UBSan external subset | 3/3; one overlap, 10 unique tests across subsets |
| Windows/MSVC installed + genuinely moved-prefix consumers | Six + six executions, all passed |
| Linux/GNU installed + genuinely moved-prefix consumers | Six + six executions, all passed |

Local reruns reused prior exact-tree build/install outputs after fast-forwarding
their source worktree to final main; no fresh local rebuild is claimed. Hosted
final-main workflows use fresh checkouts/builds. The local compilers were
GNU 15.2 and MSVC 19.44 on Windows and GNU on WSL Ubuntu; these runs preserve
C++17. Six consumer programs cover library smoke, AMXR, Conversation Phase 1,
AMCE, reference store and source importer. Existing relocated prefixes were
actually moved in prior exact-tree qualification and are rerun here.

Promotion CI attempt 1 was not successful: GitHub could not acquire a macOS
hosted runner, executed no steps for Framed Zstd and failed the aggregate gate.
Its annotation named macOS arm64 capacity constraints. Only the cancelled job
and dependents were rerun on the unchanged candidate; attempt 2 succeeded
before ordinary merge. Main then passed its own independent hosted runs above.
Historical #697 success was not substituted for either required gate.

Representative post-main commands (repository root):

```powershell
git fetch liminode main
git rev-parse liminode/main
git rev-parse 'liminode/main^{tree}'
ctest --test-dir .build/conversation-integration-gnu --output-on-failure
ctest --test-dir .build/conversation-integration-msvc -C Release --output-on-failure
wsl -d Ubuntu --exec ctest --test-dir /var/tmp/am-conversation-integration-release --output-on-failure
wsl -d Ubuntu --exec ctest --test-dir /var/tmp/am-conversation-integration-debug -R conversation --output-on-failure
wsl -d Ubuntu --exec ctest --test-dir /var/tmp/am-conversation-integration-debug -R external_ --output-on-failure
```

## P4-0 evidence boundary and source audit

P4-0 changes only guides/YAML. Proposed keys, witness codec, corruption checks,
crash/race fixtures, Current benchmarks, persistent capacity enforcement and
portable frontier APIs are **NOT IMPLEMENTED / NOT RUN**. Passing the manifest
validator establishes inventory/projection arithmetic, not runtime durability.
No P4-1 production code, dependency, database, migration, release, tag or
schema/API freeze is introduced.

The design inspection used integrated source:

- `src/agent_memory/infrastructure/mdbx/MdbxStorageContext.{hpp,cpp}`:
  existing shared connection, owned open and retained attach, default max_dbs 96.
- `MdbxStorageIdentityMapping.cpp`: explicit transaction provenance, durable
  sequence/marker refusal pattern; **not** a Conversation allocator to reuse.
- `src/agent_memory/ingestion/ConversationSourceImporter.{hpp,cpp}`:
  `source_key()` and lossless `same_witness()` equivalence, source-target freeze.
- `src/agent_memory/storage/ConversationStore.{hpp,cpp}`:
  global generation on create/import/correction, CAS-before-no-op, immutable
  receipts, exact frontier fold and instance-bound continuation contract.
- `src/agent_memory/domain/conversation_codec.hpp`: all codec budgets, including
  `max_objects`, plus conjunctive reader/writer coverage.
- Pinned `mdbx-containers@c2a2326273a3d85f29283e2cd34942c7b8d72cbd`
  `include/mdbx_containers/common/Config.hpp`: `sync_durable=true`, normal
  non-WRITEMAP default. Attached runtime flags still need actual verification.
- Pinned `libmdbx@fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f` API:
  environment-specific max-key query and zero-valued `MDBX_SYNC_DURABLE`.

The root's unrelated dependency checkout was not changed to inspect pinned
objects. Older backend roadmap statements that a context is conceptual were
identified as historical drift and explicitly time-bounded, not used as evidence
that a second context implementation is necessary.

## Design review gate

P4-0 must pass `git diff --check`, DBI manifest/projection validation and negative
self-tests, relative Markdown link/anchor checks, documentation-only diff review
and independent read-only review of its exact head. Executed results and exact
review/PR identities are recorded in the draft PR receipt; they must not be
inferred from this checklist. No full CMake rebuild is required by this docs-only
delta; final-main code qualification is separate above.

Owner decisions remain the physical proposal's three DBIs, private witness/profile
format and collision budgets, continuation compatibility and minimal conformant
P4-1 exposure. Current production strategy, migration/retention, portable frontier
API and storage-device power-loss qualification remain deferred. P4-1 needs a
separate approval after this draft is reviewed.
