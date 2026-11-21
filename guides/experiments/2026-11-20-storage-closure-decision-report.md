# Storage correctness closure and integration decision

Currency note (2026-11-21): the sections preceding the
[Post-Merge Integration Addendum](#post-merge-integration-addendum) preserve
historical pre-integration evidence. The addendum records the current partial
integration; it is not final acceptance of the remaining queue.

Date: 2026-11-20. Status: engineering qualification complete within the stated
boundaries; no merge authorization.
Scope: first-agent storage batch #654–#663, #667, #669, #670, #672, #673 and #675.
This is an engineering evidence/decision record, not a release decision or a
request to activate the experimental adapters.

## Decision boundary

The corrected numeric adapters are opt-in. The historical production storage
path, logical textual identities, provenance admission and retrieval defaults
are unchanged. This batch has not been integrated into `main`.

The initially reviewed base was
`bfa01a22d4091bb5c5f7c17606199fda6342b790`. During qualification, other work
advanced `main` through #633 to selected cutoff
`c1adfbf32dfde7f3339353568491469514fc1c4c` (#635).
The combined local branch includes that cutoff without modifying the
second-agent research changes. Branches and PRs have been published for review,
but no PR has been merged and no tag/release has been published by this
storage-closure operation.

A later refresh observed `main@aab98c702e684c3fd6e12bf8acb0b6a72b472be0`
(#638/#642). Relative to the selected cutoff, it changes research tools,
receipts and build/CI registration, not `src/`, `external/` or `tests/`.
It is not a documentation-only delta. The final composed preflight includes
this main cutoff, #673 and the completed #663 without changing their research
sources. Evidence below keeps each actual qualification source binding;
integration into main remains a separate decision.

`Ready for Review` below is a review state, not permission for an unchecked
merge. Recheck the exact HEAD, base, protection, comments and required checks
immediately before any separately authorized integration.

## Confirmed defects and corrections

| Boundary | Correction and executed regression |
| --- | --- |
| Revision restoration | Shared revision-aware edit reconstruction replaces bootstrap through initial-revision-only `create_document()`. Differential history covers revisions 0 through 7, text replacement, metadata, insertion/move/deletion, no-change, stale conflict, render and reopen. Logical document revision and physical block revision remain distinct. |
| Concurrent publication | Expected revision, new identity mappings, immutable payloads, structure and head are admitted/published in one MDBX writer. Two independent adapter instances produce one success and one conflict; duplicate create has one winner. Pre-head exceptions roll back both rows and allocations. |
| Identity reassignment | MDBX `load_records()` is initial-import-only into a completely empty, never-used namespace. Identical/replacement/deletion/partial/retired live snapshots cannot rebind IDs. Fresh-namespace remapping remains explicit; historical assignments and high-water marks survive reopen. |
| Bounded identity operations | Table statistics admit allocations/snapshots before decoding; transaction provenance is checked before DBI access. Snapshots are bounded and sort in O(N log N), rather than performing an unbounded range before checking the limit. |
| Physical keys | Versioned BE64+BE64 composite keys replace decimal composite strings. Exact 16-byte encoding/sort/order controls are separate from native `MDBX_INTEGERKEY` tables and their platform constraints. Experimental v1 state is explicitly rejected. |
| Immutable binding | AMF2 envelopes bind schema/type, physical key, document identity and payload SHA-256. Point reads validate structure references and semantic block identity, including rehashed mismatch controls. Existing immutable rows allow identical bytes only; collisions cannot overwrite historical payloads or structures. |
| Envelope allocation | Overflow-safe complete envelope sizing precedes reserve/construction. A 32,768-block oversized fixture rejects create/edit before canonical payload writes; mappings roll back, old head/history survive reopen and a later valid edit succeeds. The fixture explicitly uses 4 KiB pages so MDBX key limits do not mask this control. |
| Typed index corruption | `create_or_get_unit()` now checks the requested scope/kind/content hash, not merely the aliased target's self-consistency. By-kind range keys must have the exact width. Three content-key alias controls and a malformed trailing-byte key reject without partial results or index/frontier mutation. |
| Gate diagnostics | Numeric process-driver negatives identify exactly one intended `FAIL:` line amid MDBX diagnostics; unrelated/empty/ambiguous failures reject. Three Python tests exercise ten diagnostic controls. |
| Native watchdogs | Windows abrupt worker exits use native `_exit`, preserving no-unwind semantics without unresolved legacy-MinGW `_Exit`/`quick_exit` symbols in Debug. Process isolation and deadlines are retained. |
| Shared SHA-256 | #667 removes duplicate implementations without changing hashes. Persisted digest, fencing and artifact vectors remain byte-exact. |

The main corrected adapter regression is
`tests/infrastructure/mdbx/mdbx_canonical_flat_storage_test.cpp` at
[#662 HEAD](https://github.com/LimiNode/agent-memory-cpp/tree/4dc38f7f1244e05efa8e13f9b675337003e00025).
It is differential against both the in-memory reference and legacy C1; it is
not a proof that the legacy production importer has migrated to numeric IDs.

## Navigation and write locality

Executed counters distinguish canonical KV operations from identity dictionary
operations. An outline reads one structure value and no payload; a selected
one-block point/subtree read reads one structure and one payload. Metadata-only
subtree reads no text. Budgets reject before a disallowed payload read, and
missing-document/status precedence matches the reference navigation.

One text edit writes one new block version plus structure and head; the tested
metadata-only edit writes no new block payload. Historical payloads are reused,
not rewritten. These are physical adapter-call observations, **not** O(1)
whole-operation complexity: reading the structure is linear in its encoded
size; decoding/validation remains O((V+H) log(V+H)), with V active references
and H historical IDs in the no-reuse ledger. The shared editor still reads
the current revision and performs linear searches.
No constant MDBX lookup cost, page I/O or production throughput is asserted.

## Compatibility: three different gates

| Gate | Actual execution | Meaning |
| --- | --- | --- |
| Historical exact-generation replay | Pinned OLD `52004324bc0d2b07c3657245195db349dd391774`; rebuilt installed OLD/NEW consumers in separate processes | `HISTORICAL_ONLY`, never numeric migration proof |
| Numeric reject-old/conformance (#670) | Nineteen bounded native processes; current typed units/identity/flat history, secondary indexes/frontiers, repeated reopen, interrupted publication and five corruption controls | `POST_NUMERIC_REJECT_OLD`; `migration_executed=false`, `post_migration_upgrade_proof=false` even on PASS |
| Actual OLD-binary numeric migration | Not implemented/executed by either gate | `NOT_EXECUTED`; no interrupted-migration-recovery or production-upgrade claim |

The OLD input of #670 is a reconstructed real MDBX physical-schema fixture
pinned to `3e21501be41c165a900bd203c494784d3d5bbcba`, **not** an OLD library
binary. Its decoded state is unchanged after rejection. A separately created
numeric fixture is checked against the logical reference. Synthetic stored
source bindings do not establish authoritative freshness admission.

The audited historical replay passed at NEW
`5af04c7f18e7a166f9eabe45e416ed2b66108815`: seven independent processes
(create, upgrade, interrupt, verify, crash, verify, OLD verify), snapshot
SHA-256 `bc29c115cbd644c83837853df9f382105154b178d10f35b3a9f0fab6cc5d97cc`.
Its receipt is retained at `.build/sf-upgrade-audit-artifacts/receipt.json`,
SHA-256 `bb6d193e04df7624e3830df598259a984fa7212c0d5b6d3d9887d8c9c9e3fac9`.
Only external-tree pair `b949d6822582edc1f445837442b91aabf5093ac6` to
`b816ddeccafa0375c15b977e71d4a36f6728f59f` is admitted by audit
`mdbxc-db-load-private-template-2026-11-20`. The sole changed pin is the
reviewed private MDBXC loader template fix; every other recursive dependency
entry must match. Unknown/reverse/additional changes fail closed. This
compatibility audit changes neither gate's numeric-migration evidence class.

The numeric fixture uses thirteen DBIs: five typed KnowledgeUnit tables, four
identity tables and four flat tables. Manifest validation separately checks
schema/comparator/prefix declarations and the combined table budget. The
combined optional profile requires 105 DBIs under its reserve policy; the
default 96-DBI profile is not silently relabelled sufficient.

Golden logical corpora remain unchanged. Pre-numeric raw-key snapshots,
commit-based reopen studies and the old technical-preview decision remain
historical. No blanket post-numeric preview qualification follows from this
batch.

## Local combined matrix

Exact source: `2b60d9c04f9044851706220b3d9fbd9e5719053f`, clean combined
worktree, MinGW GCC 15.2, Ninja, C++17, research tests OFF. JSON uses
nlohmann_json 3.11.3. Dependencies: libmdbx
`fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`, mdbx-containers
`c2a2326273a3d85f29283e2cd34942c7b8d72cbd`.

| Configuration | MDBX | JSON | CTest |
| --- | --- | --- | --- |
| Release | OFF | OFF | 52/52 PASS |
| Release | OFF | ON | 57/57 PASS |
| Release | ON | OFF | 90/90 PASS |
| Release | ON | ON | 96/96 PASS |
| Debug | ON | OFF | 90/90 PASS |

Builds/logs remain under `.build/sf-{roff,rj,rm,ron,don}`. These are local
observations at the stated commit, not claims about every platform or a later
integration commit.

An incremental build at `d77080d138bdcdc6d1b01260ca5fe426ca01ac82` includes
`main@2b65bdfe`, audited dependency runner and VS2022 image correction.
The same five profiles passed 52/52, 57/57, 90/90, 96/96 and 90/90 respectively.
For MDBX profiles, each build has its own `.build/sf-<profile>/profile-temp`
selected through `TMP` and `TEMP`. A first concurrent shared-OS-temp attempt
produced 89/90 Debug and 95/96 JSON-on Release: the incremental lexical fixture
uses fixed database names in `temp_directory_path()`, shared across independent
builds. Those failure logs remain at
`.build/sf-{don,ron}-parallel-shared-temp-failure.log`, not erased as PASS.
The isolated concurrent rerun passed all three MDBX profiles without source
changes. This is an operational test-isolation constraint, not a production
storage correction.

### Final published preflight

Combined branch `preflight/storage-closure-final`, exact published source
[`04e5868bd3cc5d20ee231c365d4d01fc74b63621`](https://github.com/LimiNode/agent-memory-cpp/tree/04e5868bd3cc5d20ee231c365d4d01fc74b63621),
contains corrected parent HEADs, #673/#675, completed benchmark evidence and
`main@aab98c70`. It is a reproducible test composition, not a replacement PR
or an integration into main.

All five MinGW profiles were freshly configured, built and tested at this
clean exact source: **52/52, 57/57, 90/90, 96/96, 90/90 PASS**, in the same
order as the matrix above. Logs are
`.build/sf-<profile>/final-{configure,build,ctest}.log` for
`roff,rj,rm,ron,don`. Each process uses its own
`D:/agent-memory-storage-final-04e5868-tests/<profile>` TMP/TEMP directory.
`git diff --check` passed and the composed worktree remained clean.

The full tracked source tree is `85b114d305bdce1efde785d0c957c6d4ff549164`;
external tree is `b816ddeccafa0375c15b977e71d4a36f6728f59f`. Both match the
earlier `2b60d9c0` and native-qualified `dc085936` trees byte-for-byte. This
source comparison does not relabel earlier installed/sanitizer/native runs as
executions at a later SHA.

The three final numeric receipts report `source_commit=04e5868...`,
`source_dirty=false`, `PASS`, the shared logical snapshot
`515a2865cf0fa5f353d3fced0f798e3fa1a179fa6411542528ec4c6ac37b1940`,
fresh-process reopen and both migration-proof flags false:

- `.build/sf-rm/tests/numeric-conformance-Release/run-gm0teku5/receipt.json`,
  SHA-256 `48aba1de53788b7903e55e4e11aa97c2a8a608fe0e5cc513c83b822ca90434a9`.
- `.build/sf-ron/tests/numeric-conformance-Release/run-a0faw004/receipt.json`,
  SHA-256 `484904e8cefce3b0be5fae857597e0d7b2ac9e9a0b2a405dfcafe0870ed3f8d9`.
- `.build/sf-don/tests/numeric-conformance-Debug/run-wx83z3zh/receipt.json`,
  SHA-256 `d31acd485b35ad64be3709aa08f2c3fc54a29bdfac19808c36b99ff0b807f1d6`.

These include nineteen numeric processes per profile; the corresponding
flat-Markdown CTest separately executes its four fresh-process scenarios.

### Installed consumers

Exact source `2b60d9c04f9044851706220b3d9fbd9e5719053f` passed installed
minimal/JSON/MDBX/hydration/RawMarkdown
consumers, minimal relocation, exact 0.x version acceptance/rejection and
positive/negative installed-header controls. All four exported target trees
exclude source/build dependency paths. Numeric/flat consumers passed 2/2 CTest
in each MDBX profile (4/4 total), with 19 numeric and 4 flat bounded native
processes per profile. Corruption fixtures explicitly link installed
`mdbx_containers`; normal consumers link only `agent_memory::agent_memory`.

Both numeric receipts have `source_dirty=false` and logical snapshot SHA-256
`515a2865cf0fa5f353d3fced0f798e3fa1a179fa6411542528ec4c6ac37b1940`.
Profile details, receipt locations, installed archive hashes and export controls are
retained in `.build/sf-package/QUALIFICATION.md`.

### Sanitizers

WSL Ubuntu/GCC 15.2, Debug, MDBX ON/JSON OFF, ASan+UBSan including leak checks:
7/7 targeted tests PASS at exact source `2b60d9c0...`. The exported source tar
SHA-256 is `dd4cf1b8f364bf5b032c9c5a14005eb07c30a8e7c18a47c3f421848e8a8523a9`.
Targets cover domain flat/versioned decoders, ExactVector/ExactLexical exception
safety, C1, numeric flat, persistent identity and typed units. The oversized
flat regression is included (59.70 seconds); total 61.61 seconds.

The successful configuration sets `MDBX_UNALIGNED_OK=0` for upstream C while
keeping `-fsanitize=address,undefined`, `halt_on_error=1` and leak detection.
The earlier default MDBX configuration **failed** UBSan in
`unaligned_poke_u64` (`mdbx.c:694`) during environment initialization at source
`270e28d6...`. It is not labelled a false positive or hidden by suppression.
Its retained log SHA-256 is
`76590d4fcfdc8e0376c5a17f9e0de7ae43048eaf406a6c8ff60b910d371c5f5b`.
The failure log remains at
`.worktrees/storage-closure-qualification/.build/asan-default-failure.log`;
the successful CTest log/cache remain in the WSL lab
`/home/user/agent-memory-asan-270e28d6/build/`. The exact exported source
archive is `.build/sf-package/storage-final-source.tar`.
TSan was not executed; no broader thread-safety claim follows.

## Opt-in Markdown consumer (#672)

The executable path uses real RawMarkdown ingestion, revision/edit/render,
navigation, point lookup, durable reopen and retrieval/hydration-visible text,
plus corruption/no-reuse/budget checks. Four independent bounded processes
exercise create, reopen, corrupt and reject-corrupt. Core defaults are unchanged.

Limits are deliberate: the importer still uses legacy KnowledgeUnit/lexical
storage; flat CanonicalContent remains a separate host-composed store. Scope
filtering is a host-side metadata predicate rather than a new flat ScopeId API,
and two-store publication is not atomic. This demonstrates a single-writer
consumer consistency boundary, not complete production numeric migration or
canonical provenance admission.

## Performance qualification (#663)

The corrected harness measures actual adapter KV calls/value bytes, mapping
cost, codec time, block-version writes, process CPU/wall time and apparent
MDBX data-file length. It does not measure page/disk I/O or allocations.
The fixture is deterministic balanced binary preorder, not representative
production/shared-hot data. Repeated matched C1/flat samples, warmup/order,
unique complete matrices, raw hashes and every declared p50/p95 are checked.
Launch provenance is procedural source/binary stability, not reproducible-build
proof. Failed/incomplete execution cannot produce a successful receipt.

The 16,384-block matrix did not finish within the prior 1,800-second bound at
source `62c3dde2ddae93dc280f4379be9c188083313983`; receipt is `FAIL` and no
p50/p95 from that run are published. Source
`a7a71807c00571034ccdd9587259e612171123c7` retains completed raw groups on
failure, while the validator still rejects an incomplete matrix. Its four
Release harness/replay/control CTests and ten Python orchestration tests pass;
independent narrow source review found no new P0/P1/P2 in harness/reporting.
The bounded 3600-second follow-up completed at that same measured source:
210 observations, all expected coordinates, five repeats, logical result parity
and every aggregate replayed. Its compact TSV/receipt pair is retained in #663
under the explicit 128 KiB policy in
`guides/experiments/2026-11-20-canonical-storage-comparison.md`.
Raw SHA-256:
`8ff5103a866ff105f41d0c46aa192e3abbcfcd44439d22fcb3d96b69dbe464d1`;
receipt SHA-256:
`24c204346406f4f779b4eb1dc282b91b5dc97f87477643c858633032f4773c12`.
The receipt remains bound to `a7a71807...`, not the later publication HEAD.
Normal synchronization with #673 preserved the exact `src` and `external`
trees used by the measurement.
An independent Decimal-based quantile replay confirmed all 1176 declared
p50/p95 values across 42 aggregates and all 210 logical parity observations.
The retained Git blobs are byte-identical to the checked files. Final
publication HEAD `275390866c0f90caa438d6bee748b352a7077512` passed all five
local harness CTests and exact-head CI/packaging.

| Blocks | C1 single-edit p50/p95 (ms) | Flat single-edit p50/p95 (ms) | C1 / flat written value bytes |
| --- | --- | --- | --- |
| 64 | 43.8110 / 53.2055 | 6.8181 / 39.3316 | 10079 / 6610 |
| 1024 | 132.1323 / 195.1390 | 177.8489 / 214.5054 | 166464 / 101608 |
| 16384 | 26045.6859 / 27870.2962 | 25817.5955 / 38361.6185 | 2800573 / 1687466 |

Flat writes one new payload version per single edit, but reads V+4 canonical
values (68/1028/16388). Bounded payload writes do not make the full edit
bounded work. These are directional local results: native builds ran
concurrently and a separate build exhausted E: free space. All benchmark
operations completed and replay checks passed, but scheduling/storage-pressure
effects were not isolated. The completed matrix closes the evidence-completeness
gate, not a stable speedup or production tail-latency gate. No allocations,
page writes, actual allocated disk blocks or realistic shared-hot workload were
measured. A quiescent realistic comparison remains separate follow-up work.

## Windows MDBX qualification (#673)

The `retrieve_all` MSVC template-deduction blocker was reproduced and corrected
upstream in [mdbx-containers #335](https://github.com/LimiNode/mdbx-containers/pull/335).
The pinned change alters a private `db_load` template signature and adds a
runtime test; no serialization/wire format changes. Focused upstream Release
and Debug tests passed 3/3 each. Core CMake also supplies the required
`/Zc:__cplusplus` and `NOMINMAX`; MDBX runtime consumers remain real consumers,
not MDBX-off substitutes.

The first added CI jobs failed before compilation: `windows-latest` supplied
`windows-2025-vs2026` image `20261116.315.1`, but the requested generator is
VS2022. The corrected job pins `windows-2022`, preserving both configurations
and the mandatory aggregate gate. Separately, full local MSVC Debug reproduced
80/81: ExactLexical aborted
with a CRT dialog. That F1 diagnosis is **not closed** by MinGW or sanitizer
success. Full MSVC Release at `342fffbe9f4909602002dc286562c3b5be15e06c`
passed 81/81 with real MDBX enabled. The separate F1 reproducer localizes the
Debug abort to globally injected `bad_alloc` inside checked-STL `noexcept`
string/proxy construction, including argument preparation before `upsert`.
Copying the argument merely moves the same failure into another such
constructor; neither trace establishes a production index corruption.

### Checked-STL allocation injection (#675)

The independent corrective PR is based directly on `main@c1adfbf3` and changes
only the test, a standalone reproducer and its experiment note. It skips the
exhaustive global-`new` failure-budget matrix only when
`_MSC_VER && _ITERATOR_DEBUG_LEVEL > 0`; the visible SKIP diagnostic identifies
the unsupported boundary. Ordinary semantics, ownership and allocation-count
checks remain enabled. Release and MinGW retain the complete original matrix.
No core ABI, iterator setting, production allocator or CTest registration is
changed.

The bounded standalone reproducer exits 73 with the expected diagnosis in
checked MSVC Debug and exits 0 in MSVC Release/MinGW. Focused ordinary CMake
MSVC Release and Debug each passed 1/1; MinGW Debug executed the full matrix.
Independent narrow source review accepted the mechanism, test guard and
coverage disclosure, with no new P0/P1/P2. These observations do not qualify
the skipped allocation-failure matrix on checked STL.

The full combined source was frozen at
`dc085936c56a7a031bacee25ffb3be1e9a7f9fa0` during native qualification.
The first Debug build exhausted free space on E: after the Release suite;
the curated resource-failure record is `.build/sf-msvc-DISK-FAILURE.md`,
not a complete saved MSBuild log or a test PASS. A fresh Debug build
uses `D:/agent-memory-storage-closure-dc085-msvc` and a separate temporary
directory because that volume has sufficient free space. Only 122 regenerable
Release object files were removed from the old build (57,356,204 bytes);
source, libraries, executables and test logs were retained.

At that frozen source, the combined native MSVC 19.44.35228/VS2022 x64
MDBX ON/JSON OFF Release suite passed **90/90**. Its retained CTest log
SHA-256 is `6a9b54706788484771a68888912a6346015e177ea9559ea9994433e050180f0d`.
The fresh D: Debug configure, build and CTest all returned 0: **90/90 PASS**,
49.29 seconds. No CTest registration was skipped. `LastTest.log:808` contains
the explicit lexical allocation-matrix SKIP; ordinary ownership checks execute
at line 809 (49 allocations at each tested corpus size).
Debug CTest log SHA-256:
`7c763e1b377297e756f1864898a1335028975420d82240baa73ae015d4f6e437`;
Debug archive SHA-256:
`6874642ffea9682341e397a62b8728df68ce0ae4d0dd07e405998832a23047d4`.
These full combined results are distinct from #673's exact-head 81-test CI jobs.

## Integration and remaining decisions

### Exact-head review queue

The following successful CI and packaging observations were checked against
each stated HEAD. Run IDs refer to
`https://github.com/LimiNode/agent-memory-cpp/actions/runs/<id>`.
They establish those branch revisions, not an unspecified future merge result.

| PR | Exact HEAD | CI / packaging | Review state |
| --- | --- | --- | --- |
| #654 | `bedcd178f816d6f3dfce1f18c1013e8f11b95145` | 44558715467 / 44558715437 SUCCESS | Ready |
| #655 | `370abb8ae8d3e04cfb85dc1fff94a6245721eb1b` | 44560082657 / 44560083571 SUCCESS | Ready |
| #656 | `2b4910cd3ac58cc63120d6467fffdce79bb8796d` | 44655618849 / 44655619769 SUCCESS | Ready |
| #657 | `c53f0d28e5bd6c8663f8632b48b42d6b6e0a9f6d` | 44571251135 / 44571250911 SUCCESS | Ready |
| #658 | `5c246a8599025a75ba11a1d0046f8714a37676fd` | 44572529953 / 44572530056 SUCCESS | Ready, coordinated with #661 |
| #659 | `44ca4390271596f9036c9aea37b7b8039ba5f776` | 44649534137 / 44649534558 SUCCESS | Ready |
| #660 | `9d1455c3f5ba04e6b14f1fcc6c61f47ba080fa5a` | 44660891401 / 44660891404 SUCCESS | Ready |
| #661 | `f0fabfdf5e3574f511320ff16bcbc6b95996c97c` | 44647159652 / 44647159595 SUCCESS | Ready |
| #662 | `4dc38f7f1244e05efa8e13f9b675337003e00025` | 44655597520 / 44655597350 SUCCESS | Ready |
| #667 | `2c47031d8988049b16c5b0ed1149ba1a47fa519c` | 44644009637 / 44644009634 SUCCESS | Ready |
| #669 | `cc265e5d639505f2df63d2f8ca8f4c399e941da6` | 44650874625 / 44650874742 SUCCESS | Ready |
| #670 | `e6592367857cd940a23e599d704bfca3b39cc93c` | 44657262515 / 44657262423 SUCCESS | Ready |
| #672 | `eeb7d973636652962b0fad7452a2b6a5254a9d16` | 44664212591 / 44664212488 SUCCESS | Ready |
| #673 | `09ae91e8cbd0ce76e3c64dc6f35c456a1ee29c97` | 44665606318 / 44665606089 SUCCESS | Ready |
| #663 | `275390866c0f90caa438d6bee748b352a7077512` | 44668187280 / 44668187270 SUCCESS | Ready; directional evidence only |
| #675 | `26aaaf205921e9bf377421ffd4935881837cc1a2` | 44665471903 / 44665471971 SUCCESS | Ready; checked-STL coverage limit above |

#663's measured `a7a71807...` CI and packaging both failed: the older F2 revision
lacked the reviewed dependency audit. The published branch now normally includes
#673's audit, VS2022 runner and #675, plus immutable measured evidence and a
dedicated full-evidence replay CTest (5/5 local harness tests PASS). Its new
exact-head checks are required; neither failed a7 checks nor successful checks
from superseded `14ead8d6...` are reused. The earlier #673 CI at `342fffbe...`
was cancelled once its known unsupported Debug fault-injection test started;
only its packaging run 44663852034 was successful.

### Dependency DAG and future integration order

Minimal selected prerequisites, considering merged histories rather than only
the displayed GitHub base branch:

| Dependent PR | Prerequisites |
| --- | --- |
| #660 | #657 |
| #661 | #658 |
| #662 | #654, #661 |
| #670 | #659, #660, #662, #667 |
| #672 | #662 |
| #673 | #659, #675 |
| #663 | #662, #667, #673 |

#654–#659, #667, #669 and #675 have no additional selected ancestry prerequisite.
The corrected #662 retains the original #654 navigation commits
`8bca990e`, `a606c920`, `bedcd178`; its sixteen non-merge patches contain no
duplicate stable patch-id. The original duplicated navigation replay is gone.
#670 contains earlier #660/#659 revisions, so those parents must be integrated
at their corrected HEADs before treating the composed gate as qualified.

One valid future order is:
`#655, #656, #654, #657, #658, #659, #667, #660, #661, #662, #670, #672, #669,
#675, #673, #663`. This order does not waive current branch/dependency checks or
the coordinated #658/#661 admission requirement.

Normal merge preserves shared ancestry. After every parent integration,
retarget the child, inspect its complete diff against the new `main`, obtain
current required checks and recheck the expected HEAD. A squash strategy instead
requires explicit normalization/rebase of descendants, especially cross-merge
histories in #670/#663; do not blindly squash the entire old stack. No selected
PR is closed as superseded: each still owns distinct reviewed work. The combined
preflight branch is a test composition, not a replacement mega-PR.

Completed independent PRs are Ready for Review, not held indefinitely for the
whole roadmap. #673's exact-head native MSVC Debug and Release jobs each passed
81/81; its dependency replay and packaging also passed. #663's full retained
matrix, independent aggregate replay and current CI/packaging also passed;
it is Ready for Review as experimental measurement infrastructure. Production
performance is a different, unqualified decision boundary. This docs-only
report's own current HEAD and checks are recorded in
[PR #674](https://github.com/LimiNode/agent-memory-cpp/pull/674), separately from
the runtime qualification sources above.

Distinguish the following states throughout this batch:

- **IMPLEMENTED:** corrected experimental adapters, conformance drivers,
  consumer, instrumentation and compatibility guards.
- **TESTED:** only the exact commits/configurations/processes enumerated here.
- **REVIEWED:** scoped independent source review of corrections and harnesses;
  no assertion of a formal third-party GitHub approval.
- **INTEGRATED:** composed locally in `preflight/storage-closure-final`;
  **NOT INTEGRATED into main** by this operation.
- **PRODUCTION ACTIVE:** false for the numeric flat/typed adapters.
- **PENDING:** independent GitHub review and any separately authorized
  integration/revalidation. Current report-PR checks are recorded in #674.
  No new P0/P1/P2 was found by the scoped source/evidence reviews; this does not
  grant merge authority.
- **NOT EXECUTED:** actual numeric migration, production activation, release/tag,
  TSan, quiescent representative performance qualification and production
  retrieval/provenance integration.

Any future merge set requires explicit authorization, revalidation against the
then-current `main`, exact expected HEADs and successful required checks.
Keep dependent branches until their children have been safely retargeted.

## Resume after local disk cleanup

The resumed work first reconciled the published branches and retained evidence,
rather than repeating the obsolete pre-cleanup pending list. The audited
dependency transition and the complete 16,384-block comparison above were
already closed. The measured pair remains immutable; no additional timing run
or production performance claim was made during this refresh.

The newly selected main cutoff is
`9005a010b1044e1c93ef3690d41fd56a2c7bbb2e` (#640/#671). Its changes since
`aab98c70` include research tools/evidence, CMake/CI registration and the
power-of-two cosine regression in `tests/index/exact_vector_index_test.cpp`.
They are not a documentation-only delta. Existing preflight branch
`preflight/storage-closure-final` normally merges that cutoff at
`d910d9c8ed10f8941cbfb54b7997ecfd9723b49c`; the composed worktree is clean.
Its `src` tree remains `85b114d305bdce1efde785d0c957c6d4ff549164` and its
`external` tree remains `b816ddeccafa0375c15b977e71d4a36f6728f59f`.

The existing `.build/sf-rm` directory is reused for a bounded MinGW Release,
MDBX ON/JSON OFF, research-tests-OFF refresh. The other four profiles and native
MSVC/sanitizer/installed-consumer results retain their earlier exact-source
bindings; they are not relabelled as executions at this new preflight SHA.
Configure/build returned 0 and the complete Release CTest profile passed
**90/90**, zero skipped tests, in 26.95 seconds. Its isolated TMP/TEMP root is
`.tmp/storage-closure-resume-9005/rm`. Logs are
`.build/sf-rm/resume-9005-{build,ctest}.log`; SHA-256 respectively:
`2e9baf30c885c9a7fcc36d505513a7042bdff0aa92e93441fd9322cd2871c1ae` and
`b7243d2461c3d82c4c91df8d9666c38b410edc240cccdde9006ab48b56832766`.

The new nineteen-process numeric conformance receipt is
`.build/sf-rm/tests/numeric-conformance-Release/run-x30_r320/receipt.json`,
SHA-256 `b4c09ffc442b614a3502275aa42e8b6ee0659a040e8c7388c5be0e44f59ad7c3`.
It reports the exact new source commit, `source_dirty=false`, `PASS`, the same
logical snapshot `515a2865cf0fa5f353d3fced0f798e3fa1a179fa6411542528ec4c6ac37b1940`,
fresh-process reopen and both migration-proof flags false. The flat Markdown
consumer's separate four-process conformance test also passes in this profile.

Additional executed controls at the new clean preflight source:

- retained full benchmark replay and validator self-test: PASS;
- benchmark launch-control tests: 10/10 PASS;
- exact-generation dependency/evidence controls: 4/4 PASS, including
  reverse/unknown/additional dependency rejection and no promotion of
  unexecuted numeric migration evidence;
- retained raw, benchmark receipt and historical upgrade receipt SHA-256 values
  exactly match those recorded above.

The report branch normally includes the same main cutoff and remains a
single added report file relative to it. No normative guide or runtime source
is changed by this report refresh. The seventeen selected PRs remained open
and Ready for Review with successful published-head checks at the resumed
inspection; this is not an assertion of up-to-date merge eligibility.

Effective main ruleset `24881787` requires a PR, resolved review threads and
`CI gate` with strict up-to-date checking. The absence of a legacy branch
protection record does not remove these rules. A Ready PR may therefore be
BEHIND despite its published-head SUCCESS. The dependency order above still
applies: refresh the selected parents/children and checks against then-current
main before any separately authorized integration. No merge/tag/release,
numeric migration or production activation was performed by this refresh.

## Post-Merge Integration Addendum

Date: 2026-11-21. Status: **integration in progress**, not final merge-review.
The user authorized guarded normal merges of the selected storage stack. The
new review requires post-merge CI/packaging before advancing each PR and a
separate final merge-review of this report (#674). This report must not be
automatically merged under that authorization.

### Confirmed integration ledger

Each merge used an exact expected HEAD, normal protected merge, verification
of both parents, and retained source branches. Each merge commit is also the
main SHA observed immediately after that operation.

| PR | Reviewed integrating HEAD | Merge / observed main | Exact-head CI / packaging | Post-merge CI / packaging |
| --- | --- | --- | --- | --- |
| #655 | `50ee4304dd4e824f2cefaf0556380c7561c4e6f5` | `f30901cf8fcee98d61f3b49396a6720f193f6579` | 44768591286 / 44768590922 SUCCESS | 44769910996 / 44769911078 SUCCESS, retrieved retrospectively on 2026-11-21 |
| #656 | `ee8dffe4a9b205fb64ad9b966bac5909bff5ae54` | `0a43df571d4e2d5ad29de2c2cd3b00006637e248` | 44769936646 / 44769936617 SUCCESS | 44770689023 / 44770689239 SUCCESS |
| #654 | `856d6039290f548592e2973d42345a9eb33f0e1a` | `cf054590b6a24a616bc23b03aa534fd6befbcefb` | 44794871155 / 44794871164 SUCCESS | 44796589327 / 44796589264 SUCCESS |
| #657 | `281d73c505864e28697f30e7e01e9b2e922813f7` | `76af612918d84051a0bcd30283f4eb8382d902c9` | 44796648919 attempt 2 / 44796648892 SUCCESS | 44859486239 / 44859486250 SUCCESS |
| #658 | `c2a6f14e520cb08e5f63e692fafac2adaa94fbe1` | `aa71b2b29fc0d68f959304cb26a323c4df53ffc6` | 44861143324 / 44861143210 SUCCESS | 44861882073 / 44861882126 SUCCESS |
| #659 | `91254e0f9fff1abee295da2f70c5af5edc9401bb` | `a7db7d94086aa979212671049e6b2a44dff7b0d0` | 44862754338 / 44862754286 SUCCESS | 44863527891 / 44863527811 SUCCESS |
| #667 | `ea76d6a706ca86fc56a2fde5ffd30f4175f8f801` | `0c8b940373600479dce2d90e5cd80f512d92a4e2` | 44864254966 / 44864254963 SUCCESS | 44865075989 / 44865075992 SUCCESS |
| #660 | `6d6602b952f33dcf0bc904bb0366a9b1bc7f49c8` | `1cc82fbcd5a1b685f9797bbea1afe88d75e953f4` | 44865894692 / 44865892974 SUCCESS | 44866555240 / 44866555095 SUCCESS |
| #661 | `34c8572272b372580b009c469e4e8c4622e10247` | `a28031b698f8569b678fa8813e133fa806fa49ce` | 44867528171 / 44867528271 SUCCESS | 44868248866 / 44868248859 attempt 2 SUCCESS |
| #662 | `4fdfb08a37df8da245185cf1bc1679d9ad622649` | `732bd3329bf38cd1525ff87b129cea22b575ab78` | 44868960744 / 44868960675 SUCCESS | 44869760177 / 44869760078 SUCCESS |
| #670 | `b4b110aeb47f395015f9ed3054bc27d046451c93` | `d188be2010ef8dd12fa7b065aaa55073f40738bb` | 44870824862 / 44870824851 SUCCESS | 44871573981 / 44871573872 SUCCESS |
| #672 | `6e9ed55e1b60b18f050fb7b6cf2b54c7dfbcc550` | `daff45ecf8d204c69f013c8e25a83c676ac0c52b` | 44873219127 / 44873219086 SUCCESS | 44874143284 / 44874143267 SUCCESS |
| #669 | `518e31afcbca1784ca9ab2e0fb64aa923d10d37f` | `8017a6de2c8ab5bcd030ed57fa9e0915679a426d` | 44875003336 / 44875003298 SUCCESS | 44875283009 / 44875283463 SUCCESS |
| #675 | `be4c2506823f3e10609fa4ed60285ad3bd0b5234` | `01e6f4817ddf75a741a2e34f7f56fe0c74003b2e` | 44875602227 / 44875602186 SUCCESS | 44876083593 / 44876083534 SUCCESS |
| #673 | `bcbdabe0b84c578f1ae354ae474bdbec7c415c0c` | `e8c69e65c92d570132e2405a7a632b53a74fa92a` | 44876737183 / 44876737170 SUCCESS | 44877545491 / 44877545482 SUCCESS |
| #663 | `af598cb28d3b4a79beb026fc1fbf6076c79e65ee` | `acd5ad5545386b3feeaa79f8a587855b60801b4d` | 44878411285 / 44878411384 SUCCESS | 44879234606 / 44879234421 PENDING |

The newly imposed per-step post-merge wait applies from #657 onward. The initial
report had no separately captured #655 post-merge result. A later 2026-11-21
retrospective GitHub query recovered its exact-merge successful workflows and
consistent GitHub Actions check records, closing that observation gap. This
does not claim that a per-step wait was observed before #656 was merged.
#654's exact-merge workflows and ordinary protected-rule pass were also
recaptured directly from GitHub on 2026-11-21 into a retained machine-readable
post-merge snapshot; they agree with the earlier observed workflow IDs.
Links follow the repository Actions run form
`https://github.com/LimiNode/agent-memory-cpp/actions/runs/<id>`.

#654's corrective decoder distinguishes full C1 reads from bounded navigation.
The direct-wire 1,048,577-block regression passes after same-process connection
reopen. It does not qualify the ordinary writer at that size, fresh-process
recovery at that size, or numeric migration. #658's normal main refresh resolved
only a CMake test-registration conflict, preserving both the numeric-pilot and
identity tests; those focused MDBX tests passed 2/2. The runtime patch remained
six files / 770 insertions with no accidental main deletions.

That auxiliary two-test build used the host dependency override, whose observed
checkout is `e9e9f2fd5139f7fb386afd458fcdd8e20d7ec6e3`, rather than this PR's
pin. Its 2/2 result is therefore auxiliary only, not exact-dependency
qualification. The exact-head and post-merge CI above use pinned source-built
dependencies and both pass. The seven composed profiles below were independently
checked through their caches and logs: they use the correctly pinned checkout
inside storage-final, not that host override. No dependency was modified.

### Required-check recovery and protected merge evidence

The original #657 check 136505277190 had contradictory in_progress/success
state despite its completion timestamp and successful workflow/suites. The
guard rejected it. The installed GitHub CLI subsequently recovered and its
existing keyring credential could normally rerun that job; a different
environment credential had returned 403. Workflow 44796648919 attempt 2 retained
the exact reviewed HEAD. Replacement gate 136708785347 is completed/success,
owned by GitHub Actions app 15368. No empty commit or manufactured status was
used. The cause of the server-side inconsistency remains unknown; successful
recovery does not establish its root cause.

GitHub protected-rule suites 5261046327 (#655), 5261146696 (#656),
5264438407 (#654), 5271919573 (#657), 5272215667
(#658), 5272415504 (#659), 5272601606 (#667), 5272781596 (#660) and
5272983669 (#661), 5273174532 (#662), 5273393432 (#670) and
5273703139 (#672), 5273858511 (#669), 5273955915 (#675) and
5274119784 (#673) and 5274313624 (#663) record
result pass, not bypass.
The active required-status and pull-request rules from ruleset 24881787
individually passed. The ruleset
remained enabled; no privileged bypass or review-thread bypass was requested.
Before/after diagnostic JSON, merge ledger and raw protected-rule evaluations
are retained under `.artifacts/storage-integration-2026-11-20/`.

#661's first post-merge packaging attempt failed in the Ubuntu minimal-package
negative header-manifest diagnostic matcher, after package build/install and
add_subdirectory, installed and relocated consumer executions had passed.
The workflow did not print its rejected captured diagnostic, so the underlying
cause is unknown. The other five packaging jobs passed. A normal targeted job
rerun at the unchanged merge SHA completed successfully and printed the expected
forbidden-header diagnostic. Both attempt logs are retained; attempt 1 is not
erased or reclassified as success. No source/workflow/protection change was used
to obtain attempt 2. Exact post-CI and a fresh protected-merge verification also
passed before proceeding to #662.

#662's exact CI MDBX lane passed 93/93 tests, including numeric flat history,
identity, navigation and the large C1 decoder regression. The integration
preserved the earlier #654 guards and #667 shared digest changes; its C1
change is the approved sequential metadata decoder, not a second copy of B4.

#670's downloaded exact-PR receipt passed all nineteen processes and five
negative controls. Its actual source is GitHub's synthetic merge
`9aa3139c50bc007ab32db772cb40e27f6893571a`, whose full Git tree independently
equals the integrating HEAD's tree `41e688e85ea177aecc424eb26bfd28d4d41baba5`.
The receipt is not relabelled as the PR HEAD. Its SHA-256 is
`576bed3bfe690273fc8e52ff276bd1f890c965650d0f695bedb8e02c2458010a`.
The separately downloaded post-main receipt names the actual main
`d188be2010ef8dd12fa7b065aaa55073f40738bb`, passes nineteen processes/five
controls, and has SHA-256
`32172638a8d5fa87927b699010d6ddf81b97dcce8c623fa10b50d11d3a7d4b51`.
Both confirm fresh reopen, interrupted-transaction recovery and unchanged OLD;
both retain `migration_executed=false` and `post_migration_upgrade_proof=false`.

#672's first integrating HEAD `38d1bf70...` passed CI 44872484691 but failed
packaging 44872484693 at the same Ubuntu negative diagnostic matcher as #661.
The original captured error was not printed; the cause remains unknown.
Corrective HEAD `6e9ed55e1b60b18f050fb7b6cf2b54c7dfbcc550` captures CMake
stdout/stderr separately, always prints both and bounds each fixture at thirty
seconds, while retaining the exact forbidden-header rejection in stderr.
Windows/Linux fixture pairs and three local rejection controls passed;
independent review found no P0/P1/P2. This is capture/observability hardening,
not a claimed diagnosis of the old failure. Both new exact-head workflows
passed, including all six packaging jobs and the revised Ubuntu fixture.
The PR merged normally; both post-merge workflows and a fresh ordinary
protected-merge verification passed before updating #669.

#673's normal refresh resolved one documentation conflict, retaining both the
exact audited loader transition and main's migration-backed `NOT_EXECUTED`
wording. Its effective change is fourteen approved files; production source,
research, packaging and #675's lexical files remain identical to main. The
CTest registration difference from the composed preflight is order only.
Independent review found no blockers. Four directional audit/upgrade rejection
tests passed locally. Exact-HEAD native MSVC Debug and Release each passed
91/91 tests. Their logs are retained separately from preflight results.

The exact-HEAD historical packaging receipt names actual source
`bcbdabe0b84c578f1ae354ae474bdbec7c415c0c` and source tree
`9fd876a450c3661d60ed6b8ffbd7de1ddb727f43`. It passed seven processes with
fresh-process reopen and exactly the audited external-tree transition
`b949d6822582edc1f445837442b91aabf5093ac6` to
`b816ddeccafa0375c15b977e71d4a36f6728f59f` under audit
`mdbxc-db-load-private-template-2026-11-20`. Receipt SHA-256 is
`2f4506b5678f703bcb627daa059864e572b89ec04cc010855decd358834942ce`.
It remains `HISTORICAL_ONLY`, with both migration proof flags false and an
empty `logical_row_validation` array. No numeric migration proof is claimed.

#663's normal main refresh retained the approved eleven-file effective diff,
all eight owned evidence/tool/header blobs and the immutable raw/receipt pair.
Independent exact-composition review found no blocker. Its exact-head benchmark
job passed all nineteen selected harness cases. Its existing CI regex does not
select the five registered canonical benchmark self-test/replay/launch-control
cases; they require a separate direct final-source execution. The retained measurements still name
`a7a71807...`, not the integrating HEAD or final main. Adapter KV/value-byte
counters do not measure physical page I/O or allocated disk blocks, and the
16,384-block timing does not establish universal flat-over-C1 superiority.

### Fresh composed qualification, separate from final-main acceptance

Clean local composition `847b49b334798bc8b8b214aeb607aa71e6cc15e9` normally
includes main `76af6129...` and the remaining selected stack. Its src tree is
`87a70c0f850266e549458be40f05760febb1050f`; its external tree is
`b816ddeccafa0375c15b977e71d4a36f6728f59f`. It is an existing preflight branch,
not a replacement integration PR. The following are new C++17 executions at
that exact source, not relabelled historical benchmark or main results.

| Toolchain / configuration | MDBX / JSON | CTest | Receipt SHA-256 |
| --- | --- | --- | --- |
| MinGW Release | OFF / OFF | 52/52 PASS | `3c44021e18a70c7dd0b2547c971ad997c10845b19178db41d5ea25edcd49f988` |
| MinGW Release | OFF / ON | 57/57 PASS | `1f6e646b9b22e4a2dc10cda3e78e973113bb6536c8e9377c74c5d44f1b9a9f85` |
| MinGW Release | ON / OFF | 91/91 PASS | `20dfa0ad3877bfe62c8c50ac14c58fa32db368ecd564794c647ec261396f100a` |
| MinGW Release | ON / ON | 97/97 PASS | `3f1b9c83ed63aaace63a033a9fe8bb19b53f6dded88a5cabd8a613e756e6139a` |
| MinGW Debug | ON / OFF | 91/91 PASS | `5112246f9302c090b3007e194cbbac9bd45ae2099c5a9faead3a5c1840d132ab` |
| MSVC Release | ON / OFF | 91/91 PASS | `b7e98d85da7423f6043a6474ef865d286ff725afb92893445e49cd573ab232c7` |
| MSVC Debug | ON / OFF | 91/91 PASS | `131866d0e8366c7a63258b5eaa8be1414428baa0127f0f11692f0475c6eef45b` |

All seven have zero failures/disabled tests. Existing build directories were
reconfigured and rebuilt, not empty-cache builds. Research/benchmark registration
is OFF. TMP/TEMP is isolated per profile and CTest is serial. The first MSVC
Release execution with the longer temporary path failed the artifact catalog
test (exit 0xc0000409); its original log/XML is retained. The identical binary
passed a short-TEMP focused rerun, then the complete short-TEMP Release/Debug
profiles passed as above. This does not establish general long-path support.
The local runner was also corrected to retain native nonzero exit evidence
instead of stopping on PowerShell's stderr conversion before writing a receipt.

Fresh installed-consumer execution at this source covers minimal, JSON, both
MDBX catalog profiles, both hydration profiles, both Raw Markdown profiles and
numeric/flat conformance (2/2 CTest per MDBX profile). The installed fixtures use
public installed targets, not core source/build include paths. Relocation,
export-path checks, exact version policy, missing-prefix negative control,
positive/negative public-header manifest checks and archive equality all pass.
New prefixes are under `.build/sf-package-847b49b/`; the historical package
qualification is preserved. Receipts SHA-256:
`46ae5500ef0c278f5ad08d086025cc5335926e9a10777e69b727264d55f794f6`
(installed consumers) and
`37842e0d55de94b6e3bbefade233607c1022a2d461e2db02b90a949bab9a1483`
(package controls). An initial local runner argument-shape failure is retained
as FAIL and is not library qualification evidence.

The installed profiles above use MinGW. An additional native MSVC 2022 x64
C++17 installed-consumer run at the same composed source passed all 22
configure/install/build/execute steps, including catalog, hydration and Raw
Markdown executables in both Release and Debug (six executions, not six CTest
cases). Core and MDBX archives were installed to disjoint configuration
prefixes and matched their qualified source-built archive hashes. Consumer
project include/link settings reference installed prefixes, not core build or
source headers. The repository's Unix-oriented test provider hardcodes
libmdbx.a; this native check therefore used an explicitly retained local native
SDK fixture packaging the matching mdbx.lib plus pinned headers and standard
dependency target, without changing the repository provider or dependency.
It does not qualify the unchanged Unix fixture as a native MSVC provider.
Receipt SHA-256:
`16ddeed3415a7f3a334fb09698c988c2206cb4aab37fe0599fd7e3f0fac21e0f`.

Direct current-source DBI manifest/self-tests, publication audit, dependency /
upgrade evidence controls (4/4), benchmark validator/self-tests, benchmark launch
controls (10/10) and full retained benchmark replay pass. These direct checks
are necessary because the matrix disables research/benchmark CTest registration.
Validator receipt SHA-256:
`b3a4f31a78b43f0e91882670c88ebe234ba01ce4425c6177d55560bc6bc1db8d`.
The measured TSV/receipt and their original measured-source hashes were not
modified. Older sanitizer results above retain their original source binding.

Fresh WSL GCC 15.2 C++17 Debug ASan/UBSan execution at this exact composed
source, with leak detection and explicit MDBX_UNALIGNED_OK=0, first returned
7/8 PASS: numeric flat storage exceeded its unchanged 60-second CTest limit
(60.06 seconds). That run remains FAIL. A same-binary serial rerun changing
only TMPDIR from DrvFs to an isolated native Linux temporary directory passed
8/8 with zero failures/disabled; flat storage took 59.14 seconds and the
1,048,577-block C1 decoder regression took 49.72 seconds. No timeout property,
test selection, source, or sanitizer suppression changed. This demonstrates
completion in that setup, not general timing robustness or an established
root cause for the first timeout. The small margin remains a qualification
limitation. Receipt SHA-256:
`412dcfa0f85d06f295f63d793d701115ecc41a7ab8ebd58ac424fc3196cd03e8`.
Both original and repeat logs/XML are retained under the integration evidence
directory. TSan remains unexecuted.

### Remaining queue and decision boundaries

#658 and its no-reuse corrective #661 are both MERGED. The combined identity
slice has successful exact-head and post-merge checks; final-main qualification
is still pending. #672 is merged with successful post-merge checks.
#673 is merged with successful post-merge checks. #663 is merged; its post-merge
checks remain pending. Post-merge confirmation is complete through #673. The earlier
dependency DAG
still applies; source branches are retained for safe child retargeting.

Final-main qualification and this report's separate final merge-review remain
pending. TESTED and REVIEWED do not imply MERGED; MERGED does not imply
PRODUCTION ACTIVE or MIGRATED. Numeric production retrieval/default activation,
real database migration, tag/release and changes to second-agent research were
not performed. Both migration flags remain false. The retained OLD physical
fixture is not a historical OLD library executable. Historical replay and
fresh-process numeric conformance remain distinct evidence classes.
