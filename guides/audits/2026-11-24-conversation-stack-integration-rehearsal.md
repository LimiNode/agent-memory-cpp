# Conversation stack: final 3B closure and integration rehearsal

- Date: 2026-11-24. Owner: Conversation Core integration.
- Scope: temporary verification artifact, NOT a merge proposal to execute
  without a separate owner decision. No main merge/release/migration.
- Local merge-tree checkpoint: `b45823d582bec5c0b45c9d88b82c5dec3ad95bb0`;
  tree `1fa024bebd527e80c3f0a748158af91102e73980`.
- Subsequent changes in this branch are documentation only. Final candidate
  commit/tree and exact-head hosted/independent receipts are published in the
  Draft verification PR after this report is committed, avoiding a self-hash.

## Source revisions and merge ancestry

| Input | Exact reviewed source HEAD |
| --- | --- |
| Current main at rehearsal start | `aea7d89f657c04b7b6388353530d9418a6a6ad81` |
| #690 Event Codec | `7bb88efc078f3b7c3c9a3fb49235d472bbaef717` |
| #693 memory-foundations documentation | `f3ea824f73b6411dd040c786f86b0d6b7835cea8` |
| #696 named identity copies | `3d7b868dae146890ffbce348e1048c2c3eaf1f3b` |
| #694 reference store | `a0c2d6bb31f867ac5eaa45deb9839f76d4f22c7d` |
| #695 source importer, new closure | `7d45c0dee70c488050d07170c0389a7851319f40` |

#695's previous reviewed head was `de63e95a3dc0ed8da8ff8f2ef037eede08eec647`.
Its single new ordinary commit adds only the missing-source fault fixture and
qualification appendix. The other source branches were not modified.

Created `verify/conversation-stack-20261124` and isolated worktree from the exact
main above. All operations below were ordinary `git merge --no-ff`, with the
original commits and source branches preserved; no rebase/squash/force-push.

| Order | Merge commit | Actual merge base |
| --- | --- | --- |
| #690 | `a1954f8ed0276a054dd3e601dc64eef9a4cb809f` | `aea7d89f657c04b7b6388353530d9418a6a6ad81` |
| #693 | `d682000afb1e943561baf35f633243ebb952f3fb` | `aea7d89f657c04b7b6388353530d9418a6a6ad81` |
| #696 | `5691845f42646d11b6b39b6528d385ab0e5dddb3` | `7bb88efc078f3b7c3c9a3fb49235d472bbaef717` |
| #694 | `0b0554887be2b2b7ee15ab1effee4f1a6dbd5bcb` | `7bb88efc078f3b7c3c9a3fb49235d472bbaef717` |
| #695 | `b45823d582bec5c0b45c9d88b82c5dec3ad95bb0` | `cd6d5dffc8c2c3684330c2fcfea46aa5042cdcee` |

`git rev-list --left-right --count` confirms #696...#694 = 2/7 and
#694...old #695 = 4/9 (updated #695 = 4/10). These are not linear dependencies.

## Duplicate patches, conflicts and effective content

No merge conflicted; there were **no manual conflict resolutions**. Git ort
automatically reconciled `guides/architecture.md`,
`guides/retrieval-roadmap-coverage.md` and `tests/CMakeLists.txt` where changes
overlapped. All five combined resolution diffs (`git show --cc`) are empty.

| Logical patch | Origin and distributed equivalents |
| --- | --- |
| Shared explicit copies | `ccc4a3e6` -> #694 `32d0fc75` -> #695 `723bd4b1` |
| Scalar-key fault refinement | `3d7b868d` -> #694 `2117547d` -> #695 `48e8b576` |
| Store clone/move correction | #694 `63f7635e` -> #695 `5ce7fed7` |
| Store NoChange fault lane | #694 `a0c2d6bb` -> #695 `2236727f` |

The content proof is stronger than assuming patch equivalence from subjects:
`git diff 7d45c0de b45823d5 -- src tests CMakeLists.txt examples packaging .github`
is empty. Thus foundation helpers, store corrections/fault tests, importer
staging/fault tests, all build registrations and packaging are exactly the
individually qualified new importer tree, not duplicate implementations. No
production fix was overwritten, no wire fixture changed, no test removed.

The only two-tip differences from #695 at the merge checkpoint are the 15
documentation files from #693. All its additions survive; architecture and
coverage also retain later Conversation updates. Inspected identity policy,
event contract, milestones and source/reference guides keep one normative
owner each and distinguish codec retention, semantic admission, instance-local
reference behavior, future durability and the full Planned Core profile.

Post-merge documentation reconciles the event header's stale Planned importer
status, records closure of the historical store HOLD, links a separate
[Phase 4 implementation brief](../conversation-durable-store-phase4-plan.md)
from milestones, and adds this report. Historical failed measurements remain
historical, not relabelled as passes. #693's ontology-neutral, derived-knowledge
and Managed Memory Blocks boundaries remain documentation, not implementation.

## New missing-source regression

The [import qualification appendix](2026-11-24-conversation-source-import-qualification.md#final-phase-3b-missing-source-closure)
owns the new thirteenth fixture and counts. It enters `resolve_target()` with
an absent composite external source target, fails every observed replaceable
allocation once, checks unchanged input/history/mapping/ID/generation and
retry, exact retained unresolved reference/evidence, later call admission,
frozen original replay and a separately resolving new result. No importer
redesign or production change was necessary.

New #695 exact-head CI
[45332608453](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45332608453)
completed SUCCESS (32 successful jobs, two conditional skips), Packaging
[45332608451](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45332608451)
SUCCESS. This is evidence for `7d45c0de`, not a substitute for combined CI.

## Combined local qualification

All local builds select C++17 static library, JSON/MDBX/research OFF. Full
Release builds include tests/examples. MSVC 19.44.35228.0 / VS2022 x64;
Windows GNU MinGW-w64 15.2.0; Linux WSL2 Ubuntu g++/headers package
15.2.0-16ubuntu1 with runtime libstdc++6 16-20260322-1ubuntu1.
Linux builds use native `/var/tmp` directories because mounted-Windows builds
are substantially slower. No unrelated builds/worktrees were removed.

| Check against combined source tree | Result |
| --- | --- |
| Windows GNU Release full build/CTest, `.build/conversation-integration-gnu` | 64/64 passed |
| Windows MSVC Release full build/CTest, `.build/conversation-integration-msvc` | 64/64 passed |
| Linux Release full build/CTest, `/var/tmp/am-conversation-integration-release` | 64/64 passed |
| Linux Debug ASan+UBSan library + relevant identity/conversation executables, `/var/tmp/am-conversation-integration-debug` | 10/10 passed, no sanitizer report; not a full Debug suite |
| Independent AMCE oracle `conversation_codec_goldens.py --check` | 20 literals match |
| Standalone AMXR exact literals and AMCE canonical/security tests | Included in all full suites |
| Store exact/history/Current/CAS/frontier and source semantic/concurrency/atomic admission | Included in all full suites |
| Installed C++17 consumers: library info, identity, conversation, codec, store, importer | All six built/executed on Linux GNU + MSVC |
| Moved-prefix consumers, new independent builds and original prefix absent | All six built/executed on both platforms |
| Changed Markdown local links and heading anchors | 36 files, 255 links, 90 anchors, zero errors; code fences/inline code excluded |
| `git diff --check aea7d89f` | Passed |

Consumers use the repository's `packaging/consumers/installed` fixture with
`CMAKE_CXX_STANDARD=17`, required ON and extensions OFF. Prefixes were moved,
not merely copied with an old package still available. Header manifest checks
execute at consumer configure time. Original prefix paths are absent.
Local CTest logs remain in `.artifacts/conversation-integration-*-ctest.log`;
this committed summary and the published exact-head receipt are durable evidence,
not a promise that local raw artifacts are archived remotely.

The sanitized library and relevant executables use
`-fsanitize=address,undefined -fno-omit-frame-pointer`, without suppression.
The external store CTest uses the same failure executable with an external-key
argument; it is not a separate build target. Compiler warnings in unchanged
TypedRepresentation/ResourceIndexer/VectorSimilarityComputer/FileRawBlobStore
were observed; no warning-free repository claim is made.

## Independent review and acceptance decision

The separately authorized read-only reviewer inspected merge ancestry, empty
combined resolution diffs, #693 preservation, production-tree identity and
new fallback coverage, and independently reran the new fixture on Windows
GNU/MSVC and Linux Debug ASan/UBSan: 17/98/98 new-case failures,
143/820/816 importer totals, no sanitizer report. Preliminary verdict at
`b45823d5` is no blockers. Final report/plan and exact candidate hosted receipts
require its final pass; self-review is not independent review.

| Surface | Local/previous independent verdict | Remaining integration gate |
| --- | --- | --- |
| #690 | READY, reviewed codec subset; exact source checks green | Final combined receipt |
| #693 | READY, documentation only | Final combined document/receipt review |
| #696 | READY, named-copy subset | Final combined receipt |
| #694 | READY, reference-only subset | Final combined receipt |
| #695 | READY, new gap closed + new exact-head CI/Packaging green | Final combined receipt |
| Combined candidate | Local Release + sanitizer qualification passed; no merge conflict | HOLD until exact-head hosted and final independent receipts are observed |

The final PR receipt must explicitly change combined HOLD to READY only after
all those gates pass, naming exact candidate commit/tree, source heads, runs
and reviewer verdict. A report committed before CI cannot claim future success.
The temporary Draft verification PR is necessary because local worktrees do
not trigger hosted PR Actions. It must not be merged or treated as permission
to close/delete/retarget the original PRs.

## Limits and next owner decision

Named copies do not repair arbitrary caller implicit STL copies. Replaceable
allocation sweeps qualify their measured fixtures, not malloc/OS/host allocation,
arbitrary inputs, asynchronous failures or universal OOM safety. MSVC checked
STL remains explicit SKIP 77 outside the Release qualification. Repeated Current
folds, logical-versus-RSS budgets, artifact authenticity, upstream SDK
conformance, production authority, retention/erase and durable/cross-process
recovery remain open boundaries, not integration blockers for this reference
subset. No Clang/libc++ local or real hardware power-loss claim is implied.

If accepted, the owner chooses the actual main integration sequence and any
stack retargeting, preserving the verified effective content and rerunning if
the result changes. Phase 4 is a separate plan and approval boundary. No main
merge, release, branch deletion, database migration or Phase 4 production code
has been performed by this rehearsal.
