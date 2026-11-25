# Conversation durable P4-1: internal vertical qualification

- Date: 2026-11-25. Owner: conversation storage/ingestion.
- Status: internal implementation under review; no deployment, release or
  automatic merge. Full Conversation Core remains Planned.
- Base: `4418a58c41d6c4e8f42d311708f849e00e40e59f` (integrated #699).
- Normative owners: [physical contract](../conversation-durable-physical-contract.md),
  [Phase 4 plan](../conversation-durable-store-phase4-plan.md),
  [source semantics](../conversation-source-import.md),
  [reference oracle](../conversation-reference-store.md),
  [DBI inventory](../dbi-manifest.yaml).

## Question and expected observation

Can the approved three-table profile preserve exact source admission, original
witnesses, compact numeric IDs and bounded work across close/reopen, without
advertising unsupported public store methods? Expected: create/import/exact/
history/replay match the reference oracle, refusals consume no IDs/generation,
and throwing preparation rolls back every row. An exception during commit is
an unacknowledged outcome: reopen/retry must establish all-before or all-after,
not infer rollback from the exception alone.

## Implementation and boundary

`ConversationDurableCodecInternal` implements private key/profile/value/witness
codecs. `MdbxConversationDurableInternal` implements the optional MDBX vertical
using one retained existing context/connection, explicit wrapper transactions
and three DBIs. The importer shares its original validation, witness equality
and normalization through `ConversationSourceImporterInternal`; the in-memory
algorithm and total caps are not weakened. All three private headers are
excluded from installation and from the public-header compilation inventory.

One create transaction writes conversation, generation and profile. One import
transaction writes revision@1, source witness, generation, allocator and profile
charges. Immutable rows use no-overwrite. Exact physical changed-row bytes are
checked before writes; owning results are prepared before commit and moved
after success. Replay/conflict precede new-entry quotas. Original unresolved
targets stay frozen after a late call arrives. Source bodies and tool execution
remain outside the journal.

Open verifies marker/incarnation/namespace, exact descriptor and quotas, real
bytewise comparator/flags, actual durability flags, geometry and complete
host-declared peak DBI budget plus 16 reserved slots. All-three-missing schema
initialization is atomic; partial/markerless existing tables are never stamped.
Owned `MdbxStorageContext::open` and attached contexts are exercised. Attached
open does not change host flags or geometry. The pinned comparator-check API
`mdbx_dbi_open_ex` is deprecated; its explicit identity check is intentional,
and the compiler warning is retained rather than silently weakening admission.

## Setup and reproduction

Windows GNU 15.2 and MSVC 19.44 Release, C++17; JSON OFF, tests/examples ON,
research tests OFF. Linux uses WSL Ubuntu GNU 15.2 Debug with
`-fsanitize=address,undefined -fno-omit-frame-pointer -g1` on C and C++, leak
detection and halt-on-error enabled. Optional dependencies are pinned to
libmdbx `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f` and mdbx-containers
`c2a2326273a3d85f29283e2cd34942c7b8d72cbd`. Fresh test environments use
normal non-WRITEMAP synchronous commits; no weaker sync override is enabled.

```sh
cmake -S . -B <build> -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_ENABLE_MDBX=ON -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED \
  -DAGENT_MEMORY_ENABLE_JSON=OFF -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_EXAMPLES=ON -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF
cmake --build <build> --parallel 4
ctest --test-dir <build> --output-on-failure
```

The four durable targets are `agent_memory_conversation_durable_codec_test`,
`agent_memory_mdbx_conversation_durable_test`,
`agent_memory_conversation_durable_boundaries_test` and
`agent_memory_conversation_durable_failure_test`. The backend suite launches
its own `--seed` and `--reopen` child modes for independent-process reopen.
The fault matrix uses actual replaceable `new`/`new[]` failures, including
aligned/nothrow entry points; MSVC checked-STL Debug intentionally skips this
matrix with exit 77, not a claimed pass. Release and Linux Debug are exercised.

Local artifacts are under `.build/conversation-durable-p4-1-*` and
`.artifacts/p4-1-*` in the repository workspace, untracked. Exact commit,
hosted CI and packaging receipts belong to the Draft PR; a working-tree run is
not attributed to a later commit until source identity is checked.

## Executable evidence

| Control | Observation |
| --- | --- |
| Literal physical grammar | 26/46-byte independent keys, 1/2/10/255/256/MAX ordering, namespace/prefix/incarnation isolation; witness absent/empty/false and composite scalar distinctions; exact consumption, malformed tags/lengths, overflow and maximum 8 MiB text fields |
| Source-shaped oracle parity | Authored message/object-revision/local snapshot/tool-call/result cases; original vs effective request, all result origins/outcomes, attempt identity, binary call IDs, target denials and original witness conflicts |
| Fresh-process reopen | Same AMCE bytes/ref/generation; early composite result remains unresolved after late call/replay, separate later result resolves; no allocator gap |
| Quotas/collisions | Zero/exact/one-over durable quotas and aggregate bucket rows/bytes, forced digest collision, existing replay at full quota; smaller local work budgets refuse without corruption |
| Invalid environment/storage | Nonmutating schema/version/marker/flags/profile/namespace/prefix/map/capacity refusals, initialization rollback, oversized persisted profile/bucket classified as corruption |
| Numeric boundaries | Qualification-only checksum-valid MAX seeds: last entry MAX-1 then exhaustion sentinel MAX, generation to MAX once, checked aggregate no-wrap; not a populated MAX-sized history |
| Ownership/work | Owning DTOs after transaction/context destruction; many short attachments charge container storage; maximum default 1 MiB text replays under 12 MiB local live budget while a new admission refuses writer preparation |
| Precommit hooks | Every tentative revision/source/generation boundary rolls back all rows/counters before retry; this is exception rollback, not a process-kill/power-loss test |

Frozen local matrix: GNU Release MDBX ON 109/109 PASS; MSVC Release MDBX ON
109/109 PASS, MDBX OFF 65/65 PASS. MSVC reinstall and all six existing installed
C++17 consumers pass; all three private headers are absent and exported package
files have no build/source path leak. The installed/built MSVC archive SHA-256
is `09cb64646a23fb29c9264fbb97369e37d5a27b0d8c020cf0405186a8e2cec6dd`.
Linux sanitizer and exact-head hosted checks are recorded in the final PR
receipt, not implied by these local results.

| Compiler / operation | Actual allocation failures | Strict precommit rollback | Commit-boundary all-after | Largest / cumulative requested bytes |
| --- | --- | --- | --- | --- |
| GNU / create | 18 | 18 | 0 | 434 / 1598 |
| GNU / composite import | 53 | 53 | 0 | 2771 / 25458 |
| MSVC / create | 23 | 22 | 1 | 434 / 1966 |
| MSVC / composite import | 105 | 104 | 1 | 2771 / 29666 |

Every ordinal propagates an actual bad_alloc. Precommit cases preserve all raw
rows/counters and succeed at the same next identity on retry. Both observed
MSVC commit-boundary cases reopen to the independently encoded complete-after
state and retry as original NoChange. Changed witnesses still Conflict without
mutation or receipt. GNU observed no allocation at that boundary; this is not
promoted to a portable zero-allocation guarantee.

## Memory interpretation and commit-boundary finding

The buffer budget is phase-aware and includes container-object slabs, retained
keys/witnesses, row/AMCE decode storage, validation and encode scratch. It does
not count the caller's input, MDBX malloc/mmap, OS caches or allocator metadata
as a measured process peak. Full reader compatibility requires the persisted
descriptor/quotas and the declared 256 MiB raw/512 MiB live supported local
profile; lower local defaults may refuse without invalidating stored data.

The pinned wrapper calls `check_mdbx(rc, const std::string&)` after native
commit, constructing its diagnostic context even on success. MSVC allocates
that temporary; GNU may optimize it away. Therefore the test separates strict
precommit rollback from commit-boundary exceptions and verifies exact raw
all-before/all-after state, fresh reopen and retry. It does not falsely claim
zero allocations throughout commit or reinterpret a committed operation as a
rollback. There is no new dependency patch or bypass of wrapper tracker
lifetime. This is not potentially throwing result construction after commit.

Measured allocation requests are per-operation C++ requests only. Largest and
cumulative requested bytes are not peak live heap/RSS, latency percentiles,
power-loss guarantees or a throughput/SLA improvement.

## Open gates and next slice

P4-1 is deliberately not installed as `IConversationStore` or
`IConversationImportSink`: Current, corrections/CAS receipts, public tokens,
portable frontier export and quota updates are unavailable, not stubbed.
The internal exclusive seek/frontier selector is qualification-only, not a
portable continuation. No AMCE/AMXR wire bytes or digest preimages change.

NOT RUN: deterministic process termination at write/commit/acknowledgment
boundaries, two-process admission races, map-full/readers-full/storage I/O fault
matrix, power-cut/device/firmware qualification, corrections/frontiers/Current
parity, peak RSS/live-heap or latency distribution measurement, and the 65537-
record scale fixture. The [Phase 4 plan](../conversation-durable-store-phase4-plan.md)
retains that concrete scale gate before Phase 4 completion; u64 source inspection
and synthetic MAX seeds do not replace it. Actual MDBX minimum keysize is checked
on supported environments; no artificial unsupported 45-byte environment was
executed or claimed.

P4-2 continuation: child-process acknowledged barriers before each tentative
write, before commit and after native commit/before response; terminate and
freshly reopen all three DBIs. Then two independent processes import equal,
conflicting and distinct keys; replay actual commit order against the oracle.
Keep synchronous flags and report filesystem/device assumptions separately.
No migration/deployment/release or automatic P4-1 merge is authorized.
