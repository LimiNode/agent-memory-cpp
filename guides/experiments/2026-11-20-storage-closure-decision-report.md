# Storage correctness closure and integration decision

Date: 2026-11-20. Status: qualification in progress; no merge authorization.
Scope: first-agent storage batch #654–#663, #667, #669, #670, #672 and #673.
This is an engineering evidence/decision record, not a release decision or a
request to activate the experimental adapters.

## Decision boundary

The corrected numeric adapters are opt-in. The historical production storage
path, logical textual identities, provenance admission and retrieval defaults
are unchanged. This batch has not been integrated into `main`.

The initially reviewed base was
`bfa01a22d4091bb5c5f7c17606199fda6342b790`. During qualification, other work
advanced `main` to `2b65bdfef8b43978c56a2e75bbfd1d736a2fcb16` (#633).
The combined local branch includes that revision without modifying the
second-agent research changes. Branches and PRs have been published for review,
but no PR has been merged and no tag/release has been published by this
storage-closure operation.

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
whole-operation complexity: structure decoding/validation remains O(V), and
the shared editor still reads the current revision and performs linear searches.
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
integration commit. The current-main composition will receive a separately
identified incremental qualification.

### Installed consumers

The same exact source passed installed minimal/JSON/MDBX/hydration/RawMarkdown
consumers, minimal relocation, exact 0.x version acceptance/rejection and
positive/negative installed-header controls. All four exported target trees
exclude source/build dependency paths. Numeric/flat consumers passed 2/2 CTest
in each MDBX profile (4/4 total), with 19 numeric and 4 flat bounded native
processes per profile. Corruption fixtures explicitly link installed
`mdbx_containers`; normal consumers link only `agent_memory::agent_memory`.

Both numeric receipts have `source_dirty=false` and logical snapshot SHA-256
`515a2865cf0fa5f353d3fced0f798e3fa1a179fa6411542528ec4c6ac37b1940`.
Commands, receipt locations, installed archive hashes and export controls are
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

Limits are deliberate: the legacy importer still uses C1/string IDs, scope
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
Full performance qualification remains pending a bounded follow-up run.

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
success. Final corrected MSVC and historical-dependency replay observations
must be recorded before #673 becomes Ready.

## Integration and remaining decisions

The complete exact-HEAD/CI and dependency/readiness matrix is being finalized.
Completed independent PRs are Ready for Review, not held indefinitely for the
whole roadmap. #663 remains Draft for missing full performance evidence; #673
remains Draft until its actual Windows and dependency-replay failures close.

Distinguish the following states throughout this batch:

- **IMPLEMENTED:** corrected experimental adapters, conformance drivers,
  consumer, instrumentation and compatibility guards.
- **TESTED:** only the exact commits/configurations/processes enumerated here.
- **REVIEWED:** scoped independent source review of corrections and harnesses;
  no assertion of a formal third-party GitHub approval.
- **INTEGRATED:** composed locally in `preflight/storage-closure-final`;
  **NOT INTEGRATED into main** by this operation.
- **PRODUCTION ACTIVE:** false for the numeric flat/typed adapters.
- **BLOCKED/PENDING:** full-size performance gate and final MSVC qualification.
- **NOT EXECUTED:** actual numeric migration, production activation, release/tag,
  TSan and production retrieval/provenance integration.

Any future merge set requires explicit authorization, revalidation against the
then-current `main`, exact expected HEADs and successful required checks.
Keep dependent branches until their children have been safely retargeted.
