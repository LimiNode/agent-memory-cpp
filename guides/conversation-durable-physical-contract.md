# Conversation Phase 4: approved MDBX physical contract (P4-0)

- Status: **P4-1/P4-2/P4-3 INTERNAL IMPLEMENTATION INTEGRATED; NOT DEPLOYED**.
  No published durable API/deployed-schema freeze; full Phase 4 remains open.
- Date: 2026-11-24. Owner: conversation storage/ingestion.
- Base: integrated Phase 2/3 tree `00cbb85105cb57a5a455dbb0e380238edef74cfa`,
  `main@77a278bef338074227efa05365e83e967334f429`, promotion PR #698.
- Review trigger: P4-1 authorization, identity/witness/profile change, new index,
  retention, synchronization change or migration.
- Parent: [Phase 4 plan](conversation-durable-store-phase4-plan.md).

This guide owns the **approved physical encoding and transaction boundary**,
not event meaning. The [event contract](conversation-event-contract.md),
[reference store](conversation-reference-store.md),
[source importer](conversation-source-import.md), [identity policy](identity-policy.md)
and [backend boundary](storage-backend-integration-roadmap.md) remain their
semantic owners. [YAML](dbi-manifest.yaml) is the sole DBI inventory/budget
authority; the [physical guide](mdbx-containers-extension-tz.md) contains its
checked review projection. P4-1 executable evidence and its unrun gates are
tracked in the [qualification note](experiments/2026-11-25-conversation-durable-p4-1.md).
P4-2 crash/race qualification and P4-3 private corrections/frontiers are integrated
through #702/#703. Current strategy and full scale remain later gates; complete
state-event admission follows the owner-approved
[exact DTO/witness2/read-only delta](conversation-durable-admission-v2-proposal.md).
The owner accepted exact proposal `aa0bae40` and authorized implementation on
fresh experimental environments. The grammar below describes the integrated
witness1 implementation; the approved delta supersedes its profile/witness/API
sections for the separate, not-yet-qualified P4-4 implementation. There is no
authorization to activate the new profile against existing user data.
The owner accepted P4-0
on 2026-11-25 and authorized P4-1 in a separate Draft after qualified #700/#699
integration. P4-0 deploys nothing; release, migration and automatic P4-1 merge
remain unauthorized. AMXR byte APIs are integrated through #700 at
`main@7a9be667b4f5657c1c50590dde6b0a4888acdd4b`.

#699 was integrated at `4418a58c41d6c4e8f42d311708f849e00e40e59f`.
#701 was integrated at `2c895c9812128dcfc3ccd07ae84a7175e5ff371c`, tree
`86ec0e739dd382a45b0c6bd89302a276abbc8dfa`. The owner's 2026-11-26 assignment
then authorized guarded #702/#703 integration. New P4-4/P4-5 Drafts are not
authorized for automatic merge.
P4-1 uses private physical codecs and a private optional MDBX vertical, not
an incomplete `IConversationStore`/`IConversationImportSink`. Its three headers
are excluded from install. No Current/correction method or public continuation
is advertised. This is the internal alternative selected in section 8.

P4-2 passed at `b546791191bb15b89436b7b09e25de08035a234b` (#702),
CI 45850534246 / Packaging 45850534204, with source-bound local and independent
review evidence in its [note](experiments/2026-11-25-conversation-durable-p4-2.md).
P4-3's accepted private correction/history slice is tracked in the
[P4-3 note](experiments/2026-11-26-conversation-durable-p4-3.md). Both are merged;
the [integration receipt](audits/2026-11-26-conversation-p4-2-p4-3-integration.md)
records final main `8a092d515f99ed34e50ffbc7642cd2fb80e4ef8d`, unchanged accepted
tree and post-main checks. No Current or full public store is advertised. This
status update does not amend the physical grammar, schema marker, witness or
DBI manifest; P4-4 contract approval is recorded above, separately from
implementation qualification.

## 1. Reuse, ownership and smallest layout

Use the existing optional `MdbxStorageContext`, its owned `open` / host-owned
`attach` modes, one shared `mdbxc::Connection`, explicit transaction overloads
and existing SHA-256 infrastructure. Keep handles behind optional MDBX
infrastructure/PImpl. The current `open(path, relative_to_exe, max_dbs)` does
not expose the full proposed profile configuration; P4-1 must validate the
actual environment, not pretend those options already exist. Attached stores
retain the connection but never shut it down or change host environment flags.
No generic core transaction API or mandatory dependency is added.

Use **three dedicated bytewise KV DBIs with typed row tags**. Existing generic
`unit_components` and `conversation_episode_payloads` have different ownership
and meaning; neither becomes the journal. One DBI per revision/receipt/index
would waste capacity. A single untyped mega-table would obscure the admission
boundary and quotas. Three tables isolate metadata, immutable history and
potentially large source witnesses without speculative graph/time indexes.

The proposed profile is `conversation_durable_v1`, schema marker
`conversation_durable.v1.be.keys.sha256.values.witness1`. YAML names are
suffixes, physically `<prefix>_<suffix>`; default prefix `agent_memory`.
Proposed prefixes are exact ASCII `[a-z][a-z0-9_]*`, 1..64 bytes: reject invalid
input rather than sanitize two spellings into one namespace. Check resolved
names against every selected profile in the context before opening/creating
tables. A table prefix, path, scope or environment incarnation is not a new
logical Conversation identity or an authorization capability.

| Proposed DBI suffix | Row families | Required query |
| --- | --- | --- |
| `conversation_meta_v1` | Profile descriptor; conversation scope/allocator | Open/profile verification, exact conversation admission |
| `conversation_events_v1` | Immutable revisions/receipts; generation log | Exact, ordered history, latest representation at frontier |
| `conversation_sources_v1` | Digest bucket with full source key and original witness | Exact replay/source-target lookup |

No Current/checkpoint DBI is selected. Generation rows are a required consistency
index, not a second journal of source facts. Artifact bodies stay in artifact
storage; no artifact/network I/O or tool execution occurs in these transactions.
Logical replication/export must remap local refs under existing owners; raw
KV mirroring is not logical replication. Sync activation is DEFERRED.

### DBI capacity proposal

The delta is optional, `default: false`, and used only by the internal P4-1
qualification slice. It adds three steady/peak named DBIs; it does not alter
existing reference totals or activate a default/public capability. Named-DBI
capacity excludes MDBX's main DB.

| Composition | Accounted peak | Required max_dbs for 16 free | Result |
| --- | ---: | ---: | --- |
| Conversation alone | 3 | 19 | Default 96 leaves 93 |
| Existing expanded reference 76 + Conversation | 79 | 95 | Default 96 leaves 17 |
| Existing expanded+numeric 89 + Conversation | 92 | 108 | Existing explicit 105 is insufficient |
| Expanded reference + two independent Conversation prefixes | 82 | 98 | Default 96 is insufficient |

The existing expanded 76 includes its eight-slot migration reserve. This delta
does not earmark those eight slots exclusively for Conversation. A future
dual-layout migration needs a separately reviewed simultaneous peak. Profile
composition must fail before writes when the reserve/capacity is insufficient;
never silently increase the default 96. The existing validator checks inventory,
counts and projection, but does not yet validate arbitrary compositions of this
proposed profile; arithmetic above is review evidence, not runtime enforcement.

## 2. Byte-level keys and ordering

All integers below are unsigned big-endian unless explicitly `i64`. No native
struct bytes, padding, locale comparator, `MDBX_INTEGERKEY`, `DUPSORT` or
host-endian `CompositeKey` serialization. Every DBI uses flags **0**, default
unsigned-byte lexicographic comparison. Key/value encodings are independent
from AMCE envelope and payload-schema versions.

```text
U8, U16, U32, U64 = fixed-width big-endian unsigned integers
B(x) = U32(byte length of x) | exact bytes x
O(x) = U8(0) if absent; U8(1) | x if present
C = U16(namespace byte length) | namespace ASCII bytes | U64(ConversationId)
R = C | U64(EntryId) | U64(EventRevision)
P = U64(ConversationId)  # profile-qualified physical conversation component

meta/profile         = 01 00
meta/conversation    = 01 01 | P
events/revision      = 01 01 | P | U64(EntryId) | U64(EventRevision)
events/generation    = 01 02 | U64(commit_generation)
sources/occurrence   = 01 01 | P | SHA256(logical_source_key) [32 bytes] | U32(slot)
```

The first byte is key version 1, the next is the table-specific row tag.
Unknown versions/tags are unsupported schema, not ignored rows. `C` has a
1..128-byte namespace validated by the existing Conversation owner; all local
IDs/revisions are nonzero u64. The profile binds one immutable allocation namespace.
`C` and `R` remain full logical references in values/witnesses; they are NOT the
physical key grammar. Within an exact `P` prefix, EntryId/revision order is numeric. Generation order
is numeric and profile-global. Source digest/slot order has no chronological
or semantic meaning. Resolve `P` only through the verified profile; reject any
request/value namespace differing from it before reading or writing rows.

| Key | Maximum bytes | Derivation |
| --- | ---: | --- |
| Profile | 2 | version + tag |
| Conversation | 10 | 2 + 8 |
| Revision | 26 | 10 + 8 + 8 |
| Generation | 10 | 2 + 8 |
| Source bucket member | 46 | 10 + 32 + 4 |

Before table creation/writes, query the actual environment's maximum key size
for flags 0 (`mdbx_env_get_maxkeysize_ex(env, 0)` at the pinned dependency) and
require at least **46 bytes**. Do not assume 511 bytes or one page size.
Read-only open also refuses a layout it cannot interpret. Any exact key has
fixed size independent of namespace length. Never append a full AMXR to a physical
key: a valid AMXR can be 65,536 bytes before source-revision framing.

Literal key control: namespace `a`, conversation 1, entry 2, revision 3.
P4-1 independently exercises this grammar in its private-codec fixtures:

```text
01 01 00 00 00 00 00 00 00 01
00 00 00 00 00 00 00 02 00 00 00 00 00 00 00 03
```

P4-1 must independently encode/decode this 26-byte literal, 128-byte namespace
profile maximums, 255/256 boundaries and UINT64_MAX. Match the integrated
importer: UINT64_MAX is the reserved exhausted-next sentinel; allocate
1..UINT64_MAX-1 only. Persist next EntryId, not an exhausted flag.
Generation/revision counters may reach UINT64_MAX but cannot advance beyond it;
they are not the import allocator. Reject exhaustion without mutation or wrap.
Aborted tentative IDs may be retried; committed identities are never reused.

### Namespace alternatives and isolation decision

| Choice | Revision/source key bytes | Identity and operational consequence |
| --- | --- | --- |
| A: full namespace per key | 156 / 176 maximum | Self-describing component, but still needs profile/scope/integrity validation. Prefix includes length+namespace. |
| B: immutable profile namespace (selected) | 26 / 46 fixed | One namespace per verified three-DBI set; numeric prefix scans; no mapping table. Full logical identity retained in values and digest context. |
| C: u64 namespace surrogate | 34 / 54 fixed | Requires atomic dictionary, no-reuse and backup/reopen checks; justified only by a future multi-namespace requirement, absent here. |

B is selected because the profile already prohibits multiple allocation
namespaces, not merely because it saves up to 130 bytes per key.
Each prefix resolves to exactly one marker, namespace and incarnation. Open and
attach validate all three DBIs, flags and descriptor before returning a handle;
a second namespace uses a separate validated prefix/profile set. Reject wrong
namespace before a compact-key seek. Backup/restore/relocation retains the marker
and all tables together; copying one table alone is invalid. No path/prefix
enters logical identity. Rebinding a prefix to another namespace or attaching
foreign rows is rejected, never inferred from a numeric key. Value integrity
below binds namespace/incarnation against accidental cross-profile substitution.
A malicious writer able to replace the marker and recompute digests is outside
checksum protection; host admission and fencing remain necessary in A, B and C.
Reduced width suggests better fanout/less write amplification, but no actual page
or latency improvement is measured. Qualification compares pages/tree depth/write
bytes at equal logical data. A multi-namespace requirement reopens this decision;
it does not silently add a dictionary.

### Exact source identity and collision-verifying indirection

Logical key grammar (all bytes exact, no normalization):

```text
logical_source_key = U16(1) | U8(identity_kind) | identity_body
ExternalEvent=1:   B(canonical AMXR event) | B(selected SourceRevisionId)
ObjectRevision=2: B(canonical AMXR object) | B(provider_revision_marker)
                  | B(selected SourceRevisionId)
LocalObservation=3: B(selected SourceRevisionId) | B(evidence ArtifactBindingId)
                    | B(exact record_locator)
```

Conversation/scope qualification is the outer `C` and immutable admitted scope.
This is a byte encoding of Phase 3B's existing `source_key()` equivalence,
**not a new occurrence recipe**. Object/event roles, provider/service/domain,
parent presence, signed versus unsigned versus text and composite component
names/order remain encoded by unchanged standalone AMXR. A signed -1, unsigned
UINT64_MAX, text `-1` and text `001` do not coalesce. SourceRevisionId is exact,
not an integer parsed from text. Provider marker is legacy opaque bytes, not
UTF-8-normalized text. Recipe is deliberately absent from this occurrence key
but present in the witness; changing it under the same key conflicts.

SHA-256 is **only a bucket locator**. Under one transaction scan every bounded
slot of the exact `(C,digest)` bucket and compare full key bytes. Equal digest
with unequal full key allocates a distinct next slot; never overwrite/coalesce.
Slots start at 0, remain contiguous and append-only; no slot reclamation in this
profile. Default bucket bound 16 rows / 8 MiB inspected value bytes; supported
caps 256 rows / 32 MiB. Reject a new key or incomplete lookup with ResourceLimit
when either bound would be exceeded. NEW admission checks aggregate row count
and sum of encoded value sizes of the ENTIRE post-insert bucket, not just the
candidate or scan prefix. Persist admission bounds in the immutable descriptor;
never admit a bucket larger than a conforming replay reader can scan. Later
insertion must not make an earlier mapping unreplayable. A stricter local lookup
budget may return ResourceLimit, never NotFound/Conflict/corruption; it is not
the full replay-compatible profile. A digest-only negative/equal result is
forbidden. Identical retry within the admitted bound remains possible at quota.
Forced-digest-collision tests must retain distinct refs and detect changed
witnesses for each key. Full keys/witnesses are value-sized, not key-sized.

## 3. Values, original witness and invariants

Each value is privately framed as:

```text
value = U16(value_schema=1) | B(body) | digest [32 bytes]
digest = SHA256(B(ASCII schema marker) | B(allocation namespace) | incarnation[16]
                | U8(table_kind) | B(physical_key)
                | U16(1) | B(body))
table_kind = 1 metadata | 2 events | 3 sources
```

The digest binds profile/table/key/schema/body, detecting accidental substitution, not
authenticating a hostile writer. Check finite total size, lengths, version,
integrity, exact consumption and semantic consistency before owning copies or
publication. No trailing fields/unknown value versions are silently skipped.
Corrupt stored bytes are not decoded as an empty record. SHA-256 is reused,
not a new required dependency or artifact identity.

Proposed bodies, in order:

```text
profile = B(schema marker) | B(allocation namespace) | incarnation[16]
          | B(storage profile descriptor) | U64(last generation)
          | U64(conversation count)
          | U64(entry count) | U64(revision count) | U64(mapping count)
          | U64(canonical logical charge) | U64(mapping logical charge)
          | U64(quota policy version) | U64(conversation quota) | U64(entry quota)
          | U64(revisions per entry quota) | U64(canonical logical byte quota)
          | U64(mapping row quota) | U64(mapping logical byte quota)
conversation = B(exact scope bytes) | U64(created generation)
               | U64(next EntryId)
revision = U64(admission generation)
           | O(U64(previous revision) | B(reason code)) | B(canonical AMCE)
generation = U8(create=1 | import=2 | correction=3) | C
             | O(U64(EntryId) | U64(EventRevision))
source = B(full logical_source_key) | B(original witness)
         | R(original ref@1) | U64(original admission generation)
```

Booleans/presence are only 0/1. Profile incarnation is a generated physical
instance marker retained by backup/reopen, not a global source identity. A host
must not write competing authoritative copies of a restored environment;
relocation/fencing stays under the backend topology owner. Reject a nonempty
layout lacking its marker or containing inconsistent names/flags/profile.
Initialize all three tables and marker in a single explicit schema transaction
only for a verified empty namespace; never stamp an unknown existing table.

Storage profile descriptor schema 1 is `U16(1)` followed by U64 values in this
fixed order: codec frame, owned bytes, text bytes, attachment count, link count,
binding bytes, locator bytes, recipe bytes, extension count, extension bytes,
codec allocation-object units;
import observation bytes;
physical transaction bytes, bucket rows, bucket inspected bytes. Names correspond
to codec option owners, including `ConversationCodecLimits::max_objects`.
Persistent quotas are NOT reference-store option fields. The six quota fields
above require explicit host configuration at creation. A versioned quota update
can only RAISE them atomically (CAS on quota policy version, checked no-wrap).
Lowering persisted quotas requires a separately reviewed maintenance contract;
it is not an implicit reopen setting or an unbounded scan of all history.
Quota updates do not change the immutable codec/bucket descriptor.
They advance quota policy version only, not event commit_generation; no domain
event/receipt is fabricated for this administrative configuration transaction.
Any added field or changed ordering requires a new descriptor schema before
deployment; omission must not silently relax a reader/writer bound.
These values are bounded, not permission to relax namespace/AMXR invariants.
Immutable profile compatibility is checked on reopen; local read work/output
budgets are separate and may be stricter without diagnosing corruption.

An event's correction receipt resides with its immutable revision, not in a
mutable side row. Its previous revision is exactly current-1 at admission;
reason uses existing `[a-z][a-z0-9._-]*`, 1..128 bytes. Generation/ref/key/AMCE
must agree. Original imports have no receipt; corrected revisions require one.
Value overhead for the defined revision grammar is exactly frame+51 bytes
without receipt, frame+63+reason_length with receipt (at most frame+191).
This counts neither MDBX pages nor source mapping/secondary rows.

### Original source-shaped replay witness

Persisting only normalized AMCE is **incorrect**: it loses producer, raw flags,
effective request, declared source target and distinctions normalized to Unknown.
The private witness is lossless over the admitted `ConversationSourceObservation`
DTO, with schema 1, fixed field order, U32 lengths/counts, U8 explicit enum tags,
O-presence, exact nested AMXR and B for both text and opaque bytes. The binary
framing does not redefine field validation. Source witness equality must match
the existing `same_witness()` predicate exactly.

Proposed witness grammar (`content` attachments retain order):

```text
witness = U16(1) | C | B(scope bytes) | source_binding | B(producer)
          | O(actor) | O(observer) | O(llm_role)
          | O(i64(source_unix_us)) | O(i64(observed_unix_us)) | source_payload
actor = U16(namespace length) | namespace bytes | U64(ActorId)
source_binding = U8(source identity: 1/2/3 as above)
                 | O(B(AMXR object)) | O(B(AMXR event)) | B(SourceRevisionId)
                 | O(B(provider marker bytes)) | B(adapter recipe) | evidence
evidence = B(ArtifactBindingId) | B(record locator)
content = O(B(text UTF-8)) | U32(attachment count) | B(ArtifactBindingId)*
source_payload = U8(1 message) | U8(Created=1 | Snapshot=2) | content
               OR U8(2 call) | B(call_id bytes) | B(tool_name UTF-8)
                  | B(original_request) | O(B(effective_request))
               OR U8(3 result) | call_target | B(call_id bytes) | U8(origin)
                  | U8(reported_outcome) | O(B(result artifact))
                  | O(U8(is_error)) | O(i64(exit_code))
call_target = U8(1 exact) | R
              OR U8(2 source) | C | B(scope bytes) | source_binding
              OR U8(3 unresolved) | O(B(AMXR external)) | evidence
```

Role tags: System=1, Developer=2, User=3, Assistant=4, Tool=5. Origin tags:
Reported=1, PermissionDenied=2, Recovery=3, Timeout=4, Repaired=5, Unknown=6.
Outcome tags: Unknown=1, Succeeded=2, Failed=3, Cancelled=4. Do not serialize
the C++ variant index or enum ordinal as an implicit persisted contract.
`i64` is eight-byte two's complement, decoded without implementation-defined
out-of-range unsigned conversion. Unknown witness version/tag refuses replay
and writes; it cannot be interpreted using the closest current enum.

Absent versus empty/false, binary `00`/`FF` call tokens, opaque source marker and
scope bytes, exact actor/observer/producer and requested/effective arguments all
survive reopen. Text retains existing strict UTF-8 contracts, not new blanket
validation of legacy opaque bindings. Original source-shaped target stays in
the witness even if normalization resolved it; normalized frozen resolution
stays in original AMCE. Correcting AMCE never rewrites this row or its original
ref/generation. Exact original replay after correction still returns original@1.

## 4. Atomic publication and error boundary

One source observation is one writer transaction; no archive/batch API. Validate
caller/namespace/scope and finite input preflight first. Prepare immutable
artifact evidence before entry (host obligation). Then:

1. Open one writable transaction from the shared context; verify transaction
   provenance, current schema/profile and immutable conversation scope.
2. Read the exact digest bucket and compare full source keys/witnesses inside
   that transaction. Identical witness returns NoChange with original ref and
   generation; changed witness returns Conflict with no hidden ref/count.
   Perform replay lookup **before new-entry capacity checks** so retries work
   at an already reached quota. Neither outcome writes or advances counters.
3. For a genuinely new occurrence, resolve only declared exact/source targets
   under the same snapshot and existing host access. A missing source key may
   normalize to unresolved once; denied/local-missing targets refuse. Never
   search by token/time or reconcile an old unresolved result implicitly.
4. Read durable next EntryId and profile generation. Check exhaustion, limits,
   source/target invariants, canonical AMCE and exact transaction-byte preflight.
   Prepare witness, rows, owning result copies and other throwing work before
   commit. Revalidate any state-dependent result inside the writer transaction.
5. Write revision@1, source row, generation row, allocator and all charges in
   **that same transaction**. No-overwrite for immutable rows; an unexpected
   occupied revision/slot is corruption/conflict, never replacement.
6. Commit once; only then publish Applied using nonthrowing result moves.

Create is also one transaction: immutable scope, allocator initialized to 1,
created generation and generation row with absent EntryRef. It increments
generation exactly as the reference oracle does. Repeat same scope returns
NoChange at current generation; a hidden scope returns NotFound as in the
reference import sink, without a ref/generation. Correction checks CAS
before no-op and appends revision/receipt/generation/counters in one transaction;
it leaves source rows/allocator unchanged. Correction NoChange reports current
generation, whereas import replay reports original admission generation.

MDBX's environment-wide writer coordination, not a process mutex or max-ID
scan, establishes order across independent processes. Different committed
imports receive consecutive per-conversation IDs; corrections/create can
interleave global generations. A refusal/aborted transaction consumes neither.
No callbacks, artifact hydration, nested wrapper-owned transaction, or borrowed
MDBX view escapes the operation. Never hold a write transaction while waiting
for external tools. Foreground 50 ms is a measured target, not a hard atomic
commit deadline; abort at safe pre-commit work bounds, never interrupt commit
and report success speculatively.

| Condition | Required handling |
| --- | --- |
| Invalid DTO / budget | Existing InvalidRequest / ResourceLimit; no mutation |
| Stale correction / changed witness | Conflict; no ref leak or generation advance |
| Denied scope/policy | Existing refusal; no hidden data/counts |
| Unsupported physical schema/profile | Explicit open/write refusal; no automatic upgrade |
| Map full / readers full / shutdown / I/O | Backend category + retained diagnostic; no Applied, no unlimited retry |
| Integrity/structural inconsistency | Corruption category; not NotFound/empty/Unknown |
| Allocation failure before commit | Propagate; rollback all tentative rows |
| Commit outcome not acknowledged | No speculative success; fresh-process reopen/retry establishes committed outcome |

Backend exceptions remain infrastructure errors, not fabricated domain Conflict
or a new giant public error hierarchy. A commit-error fixture must inspect
reopen state rather than assume that every exception means durable rollback.
For an already-fetched row, check an impossible structural length against its
immutable profile/row-family ceiling before charging local raw-read bytes.
This is a constant-time length check, not permission for unbudgeted decoding
or further scanning. A structurally admissible row may still exhaust a smaller
local budget without being diagnosed as corruption.

### Finite profile and operational limits

Reuse codec grammar, source validation and logical charge definitions, NOT the
reference backend's total ceilings. Its 4096/65536 entries/mappings, 64/1024
revisions and 64/256 MiB totals are in-memory implementation policy, not durable
limits. NoChange still validates current access/input. The durable limits below
are separate; private P4-1 options support create/import/exact/history only,
not corrections or quota updates.

| Limit | Default / supported maximum | Unit; scope; reason | Exhaustion / reopen behavior |
| --- | --- | --- | --- |
| Wire lengths/counts | u32 lengths, u16 AMCE counts | Bytes/elements per field; grammar, not permission to allocate | Malformed encoding rejects; unchanged on reopen |
| AMCE frame | 32 MiB / 32 MiB | Bytes/object; current implementation cap, not total history | ResourceLimit; immutable codec profile covers admitted frames |
| Observation | 2 MiB / 32 MiB | Logical input bytes/operation; source preflight | ResourceLimit; persisted admitted maximum; smaller local budget is not corruption |
| Codec fields/objects | Existing `ConversationCodecLimits` descriptor, every field persisted | Per frame; conjunctive field/object bounds, no domain relaxation | Codec ResourceLimit; incompatible descriptor refuses reopen |
| Conversations, entries, mappings | REQUIRED explicit quotas / UINT64_MAX-1 each | Rows/profile; deployment capacity | ResourceLimit for new admissions; replay first; reopen uses persisted policy, mismatch refuses writes pending explicit update |
| Revisions per entry | REQUIRED explicit quota / UINT64_MAX-1 | Rows/entry; independent of bounded page work | ResourceLimit on correction; persisted lowering unsupported |
| Canonical / mapping charge | REQUIRED separate quotas / UINT64_MAX-1 each | Logical bytes/profile; checked u64 aggregate counters | ResourceLimit; persisted lowering unsupported |
| Collision bucket | 16 / 256 rows; 8 / 32 MiB | Aggregate encoded value bytes and rows/bucket; immutable admission ceiling | ResourceLimit BEFORE insertion; full replay remains possible; incompatible reopen refuses |
| Changed transaction bytes | 32 / 256 MiB | Sum changed key+value bytes/transaction, including repeated witness/index bytes | ResourceLimit before writes; persisted writer profile, not page/RSS bound |
| Changed rows | 16 / 16 | Rows/transaction; layout safety bound | ResourceLimit before writes; extension needs review |
| Read raw bytes/rows | 32 / 256 MiB; 262144 / 262144 rows | Visited physical bytes/rows per call, including bucket scans | BudgetExhausted (read) / ResourceLimit (lookup); local reopen change allowed |
| Public output/decode | Defaults 64 items, 1 MiB output, 4096 visits, 32 MiB decoded; caps 65536 items, 262144 visits, 256 MiB bytes | Per call; bounded owning result and fold | Existing budget result, no fabricated Complete/Current; local change allowed |
| Live preparation budget | 128 / 512 MiB charged buffers plus codec object cap | Concurrent owned frame/witness/bucket/result buffers per operation | ResourceLimit before allocation/commit; local lowering can refuse, not invalidate stored data |
| MDBX map/growth | REQUIRED host policy; actual platform/environment maximum | Physical bytes/environment, not logical charge or heap | Backend map-full diagnostic, no auto-resize/retry; attach never changes geometry |

Required quotas have no hidden unlimited default. Zero forbids new consumption;
it cannot replace a nonzero persisted quota at reopen. P4-1 must
use explicit durable quota configuration, not pass large totals through
`ConversationStoreOptions` and weaken its validator. Long history grows on disk
without full-store hydration at open/per write. Seek/stream pages and bounded
folds use memory independent of total persisted history. The live preparation
budget is a buffer charge, NOT an RSS guarantee: account simultaneous copies
and containers before allocation, then measure peak heap/RSS/allocator overhead
in qualification. Wire-size preflight alone is insufficient memory evidence.
The internal slice reserves a 16 KiB fixed operation slab; AMXR preflight and
validation are temporary scratch. Source bucket scratch is reused per row,
while matched keys and witnesses remain charged. Target-lookup scratch is
released before writer preparation. New admissions reserve twelve logical
observation copies plus four codec-object slabs; replay never reserves those
future writer buffers. Object slabs account container elements separately from
wire bytes. Arrays of prepared rows move wrapped values, avoiding
initializer-list buffer copies. The note records actual replaceable-C++
allocation requests, not MDBX malloc, allocator overhead or measured peak RSS.
Initialization/bootstrap is a separate fixed operation (64 KiB), so zero local
read budgets can still open the profile.

Full replay compatibility uses local raw budget 256 MiB, live budget 512 MiB
and 262144 physical visits with the exact persisted descriptor/quotas; these
cover an admitted maximum collision bucket plus revision and metadata reads.
The smaller local defaults are an operational policy, not a claim that every
extended-profile witness can be replayed under 32 MiB of raw work. A stricter
local policy may refuse without diagnosing corruption or invalidating history.

New physical limits proposed: 32 MiB encoded changed key+value bytes per
transaction by default (cap 256 MiB), including duplicated witness/key/index
bytes, and at most 16 changed rows per observation (the layout uses fewer).
This is an additional admission limit, not a reduction of AMCE wire capacity.
A frame fitting its codec budget can exceed the aggregate physical transaction
budget and must return ResourceLimit **before** any writes. The declared
storage profile is the conjunction of those writer bounds; reader compatibility
must cover every admitted frame/witness, even when one local read request uses
a smaller budget. Zero forbids the named resource; it never disables checks.

Bucket bytes and canonical-frame bytes are charged before allocation/decoding.
Bound raw row bytes visited separately from the existing public frame decode
counters; do not mislabel index reads as complete frame decodes. Default
per-read raw scan budget 32 MiB, supported cap 256 MiB; at most 262144 visited
physical rows. Existing public defaults remain 64 items / 1 MiB output /
4096 frame visits / 32 MiB decoded; caps 65536 items / 262144 visits / 256 MiB.
No unbounded full-store allocation on open or unbounded history scan inside a
public page. A Current page may perform the explicitly budgeted full fold
below; reaching its input budget must refuse unqualified Current output.
Finite logical charges are not a peak heap/RSS bound. Map growth remains an
explicit host/backend policy; no implicit unbounded resize/retry loop.

## 5. Visibility, reopen and continuations

Durable visibility coordinate = `(profile incarnation, allocation namespace,
commit_generation)`, with exact conversation/scope qualification at reads.
Generation starts at 0, advances once per create/import/correction, never on
NoChange/Conflict and never wraps. It is not EntryId, wall time or a count
safe to expose across denied scopes. Retain generation rows and immutable
history for the profile lifetime; erase/retention/GC is a separate reviewed gate.

Exact reads validate full `R` then seek its compact physical key. History seeks
`(P,EntryId,revision)` after profile/scope checks and filters by
admission generation <= pinned frontier; corrections admitted later remain
invisible. For each Current occurrence select latest revision <= frontier,
then use the unchanged reference fold and exact retained targets. Old target
content must never be replaced by its latest corrected representation. Reject
unavailable/foreign/ahead-of-commit frontiers; never fall back to latest.

Each page opens one new bounded read transaction, validates context/profile,
scope, conversation, view/order, seek, pinned generation and current host
policy, copies owning output and closes the transaction. A public continuation
holds **no transaction/cursor/borrowed value**. Existing `ConversationContinuation`
is explicitly instance-bound, even if namespace/environment bytes match after
reopen. Keep this rule: old tokens return InvalidContinuation on a recreated
store. Reopen starts fresh reads; exact old revisions remain addressable.

This P4-0 does **not** promise serialized/cross-process continuation tokens.
P4-3 may propose an additive explicit historical-frontier request/export after
owner review, binding incarnation/profile/conversation/scope/view/policy and
validating retention. Until then test old frontiers across reopen through a
qualification-only internal selector, not an undocumented public API. This
closes the apparent conflict between durable coordinates and the accepted
instance-bound token contract without silently weakening either.

The P4-3 private history selector uses an exclusive `(EntryId, revision)` pair,
not an Entry-only seek: a page may end inside one Entry's representation history.
The normal no-token read pins the current transaction's generation. A supplied
empty/default token is invalid. The separately named qualification-only
`read_history_at` requires a nonzero retained frontier no earlier than the
conversation's creation and no later than the committed profile generation;
invalid explicit frontiers never mean latest. Reopen tests start new selector
reads; they do not resume a previous instance's token.

History filters each revision by its admission generation before testing output
item/decode caps. A later correction of an early Entry cannot stop the scan of
later Entries. Invisible revisions still consume bounded physical raw work, not
complete AMCE decode counters. Continuation progress can pass fully checked
invisible rows at its fixed frontier, but never an eligible unreturned item.
Exact/history output charge includes both canonical frame and correction reason.
All result values own their data after the transaction closes. These rules do
not select a Current strategy or introduce a public durable implementation.

The private correction implementation conservatively reserves the persisted
codec's frame ceiling before its first encode, then shrinks that charge to the
actual frame size. A stricter local live-memory budget can therefore refuse even
a small correction as ResourceLimit; it does not invalidate the storage profile
or diagnose existing bytes as corrupt. No future changed-row preparation is
charged before the NoChange decision. History reserves bounded continuation
state before any frame decode/retained output, keeping that reservation alongside
owning rows so a later live-budget stop preserves consumed-work counters and a
same-frontier retry token. These are conservative accounting policies, not
measured peak-heap/RSS guarantees.

## 6. Current alternatives and predeclared decision screen

| Strategy | Advantage | Cost / acceptance issue |
| --- | --- | --- |
| Bounded full fold | Smallest implementation, oracle-like reconstruction | Repeats full input work on every output page; no long-dialogue SLA |
| Materialized Current | Small hot-page reads | Atomic projection updates, corrections/branches, historical version retention; new measured DBI delta |
| Periodic checkpoints | Amortized prefix reconstruction | Checkpoint schema/frontier/invalidation, tail folds and retention; separate measured DBI delta |

No production strategy is selected by theoretical complexity. P4-1 may use a
conformant bounded full fold; P4-4 must choose using the
[predeclared experiment](experiments/2026-11-24-conversation-durable-current-plan.md).
All strategies must match the in-memory reference oracle before timing:
every retained revision, correction of old occurrence, sibling branch,
tombstone, edit-after-tombstone Ambiguous, explicit Restore selecting old
content, snapshots, tools/unresolved and pinned historical frontier.
On incomplete input fold return BudgetExhausted with no unqualified Current
output. A partial output page does not make a partial-input fold valid.

## 7. Deterministic failure, crash and concurrency qualification

P4-2 uses explicit private qualification hooks, absent from installed headers.
Precommit hooks may throw and identify before-transaction, before-writes,
revision/source/generation, conversation-allocator, profile-counter and
before-commit boundaries; schema initialization has its own before-commit
boundary. The post-commit hook is `noexcept` and runs only after the pinned
wrapper's `commit()` returns successfully, before the store operation returns.
It is not a hook inside native commit or between native commit and wrapper
diagnostics. A separate child-side barrier after the store operation returns
precedes receipt transmission. Neither boundary promises rollback on a later
delivery failure. The MSVC post-native-commit allocation finding remains
separate allocation-fault evidence, not a deterministic native-internal kill.

The parent acknowledges barrier arrival before release/termination; independent
processes open independent environments. Timeouts bound broken harnesses, not
prove that competing writes overlap. A writer is held before commit while an
independently opened contender must observe native `MDBX_BUSY` from a bounded
`MDBX_TXN_TRY` probe; only then does the parent release the first writer. Both
operand orders are exercised. This probe is test-only, not a second production
transaction framework. All-table checks and reference replay use
the actual committed generation order, not process launch order. Process crash
recovery does not qualify storage-device power-loss durability. See the
[P4-2 note](experiments/2026-11-25-conversation-durable-p4-2.md) for executed
versus NOT RUN coverage.

Use fresh temporary environments and independent child processes, not threads
sharing one sink. Hooked barriers acknowledge the exact step to a supervising
process; no timing sleeps as proof of a race. On reopen check all three DBIs,
key/value integrity, counters, generation log, original mapping/ref/witness,
immutable revisions/receipts, and oracle exact/history/Current results.

| Fixture boundary | Expected fresh-process observation |
| --- | --- |
| Before transaction | No new rows/counters; retry Applied at same next ID |
| After tentative allocator, source, history, generation writes (each hook) | Terminate without commit: all absent, no consumed IDs/generation |
| Immediately before commit | All-or-none reopen; no tentative row visible |
| Commit error / map full | No acknowledged Applied; reopen/retry detects actual atomic outcome, no partial mapping |
| Immediately after commit, before response | Whole admission retained; retry NoChange with original ref/generation |
| Same key, two processes, equal witness | Exactly one Applied + original NoChange; one mapping/revision/ID |
| Same key, conflicting witnesses | One Applied + Conflict; losing witness never replaces winner |
| Distinct keys, two processes | Two complete admissions, consecutive IDs in actual commit order |
| Reopen after correction | Old exact bytes/receipt retained; replay returns original@1; stale CAS still conflicts |
| Old frontier + later correction/append | Same pinned pages; recreated-instance token rejected, no latest fallback |
| Readers full, invalid schema/flags/capacity/keysize/profile | Explicit refusal; no tables stamped or counters advanced |
| Forced digest collision / long AMXR | Full-key disambiguation, bounded ResourceLimit, no accidental dedupe |

Exercise all eight authored 3B scenarios, plus absent composite call -> unresolved
result -> reopen -> late call -> frozen original replay -> separate resolving
result. Include namespace/type/parent collisions, 00/FF bytes, absent/empty,
zero/exact/one-over budgets, unavailable artifact pre-admission, denied reads,
failure-injected allocation at each throwing preparation and post-commit result
delivery. Compare sequential cases with oracle under the same admission order;
race cases replay actual committed order, not process start order.

### Synchronization and what the fixtures do not prove

Proposed durable profile requires synchronous MDBX commit: no NOMETASYNC,
SAFE_NOSYNC, UTTERLY_NOSYNC or per-transaction NOSYNC override. At the pinned
libmdbx API `MDBX_SYNC_DURABLE` is **zero**, so checking a positive durable bit
is incorrect; validate absence of weaker flags and transaction overrides.
Default `mdbxc::Config.sync_durable=true` is supporting source evidence, not
proof of an attached host environment's actual flags. Validate actual flags
on open and before writes; host must not relax synchronization concurrently.
Use normal non-WRITEMAP configuration for the first slice. Read-only mode cannot
create/stamp tables and inherits host settings without changing them.

Process termination tests qualify transaction atomicity and reopen under that
setup. They do **not** establish power-loss survival, firmware flush behavior,
filesystem guarantees, network storage support or disaster recovery. Record
MDBX version, OS/filesystem, device/cache/sync configuration, explicit flags and
failure hook. Separate power-cut/storage-fault qualification is NOT RUN and
outside P4-0/P4-1. Do not advertise arbitrary storage-device durability.

## 8. Compatibility, minimal implementation boundary and owner decisions

AMCE and standalone AMXR bytes are unchanged. The proposed physical key/value,
witness and profile versions are separate; unknown version, incomplete marker,
wrong flags/namespace/codec profile or stale environment refuses before writes.
Never rewrite legacy DBIs or infer a journal from episode payloads. A future
migration needs separate owner authorization, immutable old/new fixtures,
backup validation, quiescence/fencing, rollback/read-compatibility policy,
disk/map/DBI peak budget and explicit version markers. No automatic destructive
open-time upgrade, retention or database deployment is authorized here.

New key/value/witness codecs return `ByteBuffer` and borrow `ByteView`; fixed
digests/incarnations use byte arrays. Keep strings for text and explicitly
compatible legacy boundaries only. An MDBX wrapper requiring `std::string`
gets one named infrastructure adapter, not a string-based domain codec.
Nested AMXR uses its additive byte API; legacy entry points and all AMXR/AMCE
goldens/digest preimages are unchanged. Null/nonzero input rejects; nullptr/0
is an empty view, still malformed where a complete frame is required. Borrowed
MDBX/input views cannot escape the transaction/call; returned DTOs own memory.
See the [foundation gate](audits/2026-11-foundation-closure.md).

| Disposition | Boundary |
| --- | --- |
| REUSE | Conversation/Entry/Actor identities, AMXR, immutable artifact/source bindings, AMCE, reference fold, source DTO/equality, access owner, optional context/transactions |
| EXTEND | Optional concrete durable import sink + const history view, exact reopen/profile checks, backend diagnostics and durability qualification |
| NEW proposed | Three DBIs, private versioned witness/value/key framing, durable allocator/generation and collision-verifying source buckets |
| DEFER | Portable cursor/export API, selected Current projection/checkpoints, migration/retention/erase, sync, SQLite, provider SDKs, runtime execution, generic knowledge/Managed Memory Blocks |

Deferred public direction: optional concrete `MdbxConversationImportSink`
implementing existing `IConversationImportSink`, constructed from existing
`MdbxStorageContext` plus bounded options, returning a lifetime-bound const
`IConversationStore&`. This is a design name, **not a published signature**.
Do not expose handles, DBI names or transactions through ingestion/domain APIs;
do not allow public mutable append to bypass the source allocator/map.

P4-1 acceptance is create -> source message import -> exact/history -> close ->
fresh-process reopen -> identical replay / changed-witness Conflict, preserving
AMCE bytes/ref/generation with no ID gap. Include unknown-schema/profile refusal,
key maximum/collision literals, default core build, optional-MDBX C++17 installed
and relocated consumer, and one shared owned/attached context. Crash/race/CAS
frontier qualification remains ordered P4-2/P4-3 work, not claimed by reopen.

Existing `IConversationStore` has Current/correct methods and no generic read
Unsupported outcome. Therefore do not expose a half-implemented conforming
store that silently returns empty Current or drops correction semantics.
Either implement a bounded oracle-conformant baseline for the advertised
interface or keep the first durable vertical internal until it is conformant;
review this concrete choice in P4-1. P4-0 adds no API enum or runtime stub.

Owner review has approved the three-DBI/witness/profile design, collision
budgets and instance-bound continuation decision for P4-1. The complete
codec descriptor field list and exact optional constructor/error surface are
pre-implementation checks, not permission to freeze/deploy this draft. Current
strategy, portable historical request and migration remain explicit later gates.
