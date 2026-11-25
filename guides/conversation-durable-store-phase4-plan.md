# Conversation Core Phase 4 implementation brief

- Status: P4-0 physical design OWNER-APPROVED; durable runtime NOT STARTED at
  this integration gate. P4-1 separate Draft authorized after qualified
  #700/#699 integration; no automatic P4-1 merge.
- Date: 2026-11-24. Owner: conversation storage/ingestion.
- Entry gate: qualified combined Phase 2/3A/3B tree and separate owner decision.
- Contract owners: [events](conversation-event-contract.md),
  [reference oracle](conversation-reference-store.md),
  [source import](conversation-source-import.md),
  [backend integration](storage-backend-integration-roadmap.md),
  [physical manifest](dbi-manifest.yaml) and
  [artifact provenance](artifact-provenance-roadmap.md).

This is an implementation plan, not an additional persisted contract. It
does not deploy DBIs or authorize migration/release. The owner's 2026-11-25
assignment authorizes P4-1 implementation in a separate Draft after this gate. The
in-memory oracle stays unchanged. Full Conversation Core remains Planned;
provider SDKs, execution, generic knowledge storage, ontology SPI and Managed
Memory Blocks are outside this vertical.

The combined Phase 2/3A/3B stack is integrated through PR #698 at
`main@77a278bef338074227efa05365e83e967334f429`, accepted tree
`00cbb85105cb57a5a455dbb0e380238edef74cfa`. The
[integration receipt](audits/2026-11-24-conversation-main-and-p4-0.md) records
post-main checks and source-PR disposition. P4-0's concrete
[physical contract proposal](conversation-durable-physical-contract.md) now
defines three optional DBIs, bytewise keys, versioned values/witnesses,
transactions, durability flags, frontier/token boundaries and test fixtures.
Its [Current decision screen](experiments/2026-11-24-conversation-durable-current-plan.md)
is predeclared, NOT RUN. The YAML delta allocates/deploys nothing.

The [Foundation Closure Gate](audits/2026-11-foundation-closure.md) now precedes
P4-1 authorization. It separates profile-qualified compact physical keys from
full logical references, durable quotas from in-memory ceilings, and typed
byte codecs from legacy string adapters. Owner approval is now recorded;
#700 byte APIs are integrated at `main@7a9be667b4f5657c1c50590dde6b0a4888acdd4b`.

## Transaction and authority decisions

Use one backend context/Connection for the logical environment, not a new
connection per table/store. Keep MDBX dependencies behind the optional adapter
implementation and preserve backend-neutral public seams and C++17. A narrow
durable import sink owns coordination; do not expose mutable history append
that bypasses its mapping/ID boundary.

One observation is one write transaction over conversation/scope binding,
source-key lookup and original witness, next EntryId, canonical AMCE history,
commit generation and required indexes. Check replay/conflict inside that same
writer transaction, including after a competing process commits. Prepare all
potentially throwing result copies before commit; publish owning results only
after commit. An error before commit aborts the entire unit. No callbacks or
artifact I/O inside the write transaction. NoChange/Conflict consume no ID,
revision or generation. Aborted IDs may be retried because never committed;
committed identities must never be rebound/reused after retention or reopen.

A process dying after commit but before acknowledging success leaves an
uncertain caller outcome, not an incomplete transaction. Identical retry must
return the durable original ref/generation; changed witness conflicts. This is
idempotent admission, not exactly-once tool execution or storage-device
power-loss qualification. Qualify explicit MDBX durability flags, filesystem
and sync policy; do not claim power-failure guarantees from process termination.

Persist exact namespace-qualified ConversationRef, admitted scope, source
recipe/key and original witness/ref/generation. Reuse AMXR identity and exact
ArtifactBindingId/SourceRevisionId; no content/token/timestamp dedupe. Source
resolution freezes at original admission, including unresolved fallback;
correction cannot rewrite that witness. Imported canonical bytes must remain
readable under the declared storage codec profile after reopen.

Host authorization remains mandatory for every read/write/resume. Scope,
actor, observer and cursor are not capabilities. Reuse existing access and
artifact owners; artifact presence/authenticity is a pre-admission host
obligation, not a second registry inside the journal. Retained references do
not authorize hydration. Do not silently erase evidence when a source becomes
unavailable or access is revoked.

## Physical design decision gate

Before coding, review a manifest delta in `dbi-manifest.yaml` and its normative
physical guide. `conversation_episode_payloads` stores derived KnowledgeUnit
episodes; it is not the canonical event journal and must not be repurposed.
The following are logical record families, NOT allocated DBI names/counts:

| Family | Proposed addressing / requirement |
| --- | --- |
| Conversation metadata | Full allocation namespace + ConversationId; immutable scope, durable allocator/generation |
| Event revisions | Conversation + EntryId + revision; immutable AMCE and admission generation |
| Source mappings | Conversation/scope + structured 3B occurrence key; original witness/ref/generation |
| Correction receipts | Exact new revision -> prior exact revision + bounded reason + generation |
| Generation visibility/index | Frontier -> admitted revisions; supports pinned historical selection |
| Current/checkpoints (optional) | Derived, versioned, frontier-qualified, rebuildable from history |

Choose shared typed tables versus dedicated DBIs with measured key sizes,
fanout, max_dbs/free-slot reserve and migration-peak budget. Encode numeric
ordering explicitly (e.g. fixed-width big-endian); never host-endian structs,
delimited concatenation or truncated AMXR hashes as authoritative identity.
Full keys must stay collision-free, versioned and bounded by MDBX key limits.
If an admissible canonical key exceeds the physical key limit, decide a
collision-verifying indirection before implementation, never silently narrow
the public identity contract. Test signed/unsigned/string distinctions and
parent/object/namespace collisions. Choose required indexes only for actual
exact/history/source-key/frontier queries, not speculative graph search.

Record application schema and codec/storage profile separately from AMCE
envelope/payload versions. Unknown physical schema refuses writes/open
explicitly; no automatic destructive upgrade. Migration requires separate
owner authorization, versioned fixtures, backup/rollback/reopen and temporary
DBI/disk capacity accounting. This stage must not modify standalone AMXR.

## Historical reads, projections and bounds

Persist immutable representation revisions and correction receipts. A pinned
frontier is a durable visibility coordinate, not just maximum EntryId. Select
latest representation at or before that frontier, then apply the reference
fold: immutable chain membership, explicit sibling ambiguity, edit after
tombstone Ambiguous, Restore to exact retained content. No latest-revision
substitution or implicit target reconciliation.

Do not keep MDBX read transactions alive in public continuations. Resume with
a fresh bounded read transaction and recheck environment identity, scope,
conversation, view/order, seek, frontier and current host policy. Define whether
cross-process/reopen tokens are supported; default opaque reference tokens
must not accidentally acquire portable guarantees. If history needed for a
frontier is unavailable, refuse explicitly, never resume latest.

Compare full fold, materialized Current and periodic checkpoints against the
unchanged oracle at every tested frontier, including corrections of old
occurrences after pages were issued. Measure 1/32/128-item pages, long edit
chains, branching, restore-heavy and source-result histories; capture decoded
frames/bytes, transaction duration, allocations, peak RSS, median/p95/p99 read
latency, write amplification and map growth. The existing 2048-root pagination
experiment establishes repeated-fold cost, not an accepted serving budget.
Pick a strategy only after semantic parity and a predeclared workload/budget
gate; otherwise retain a bounded baseline without an SLA claim.

Bound input/preflight, key/witness/frame bytes, rows visited/decoded, output,
transaction time/work and peak-memory amplification separately. Zero remains
zero; finite implementation caps must cover writer/reader storage profile.
Map-full/readers-full/codec-budget/oversized-item are explicit refusals with
retry behavior, not corruption diagnoses or automatic unlimited retries.
No archive-sized write transaction. Return owning values, no MDBX views beyond
transaction lifetime. Retention/reclamation policy must preserve committed
identity tombstones, mappings, receipts and pinned evidence or explicitly
invalidate unavailable frontiers. Actual retention/erase is a separate reviewed
gate, not implied by this plan.

## Ordered slices and concrete acceptance fixtures

| Slice | Deliverable | Acceptance gate |
| --- | --- | --- |
| P4-0 physical contract | Reviewed key/schema/DBI delta, context boundary and failure model | Key-limit/collision fixtures, manifest validator + self-test, profile/unknown-schema refusal; owner approves before production code |
| P4-1 first durable vertical | Create conversation -> import one source message -> exact/history -> close/reopen -> replay | Same canonical bytes/ref/generation; no duplicate or ID gap, changed witness Conflict; independent fresh process and installed optional-MDBX consumer |
| P4-2 crash/concurrent admission | Atomic mapping/allocator/history/index commit | Kill child before each write/commit, after commit before acknowledgment; reopen sees all or none; two processes same key yield one Applied + original NoChange, distinct keys consecutive IDs |
| P4-3 correction/frontier reads | CAS receipts/history/pinned pages across reopen | Stale CAS before NoChange, original replay after correction, old-frontier pages unchanged, foreign/policy-revoked cursor fails closed |
| P4-4 Current strategy | Measured bounded fold/projection/checkpoint choice | Oracle parity on every prefix/frontier and randomized histories; explicit exhausted-input failure, edit/delete/restore/branch cases and long-history cost report |
| P4-5 qualification | Full matrix and handoff | Windows/Linux optional-MDBX + default core, ASan/UBSan relevant paths, crash/process races, installed/relocated consumers, exact-head CI/Packaging and independent review |

Crash fixtures use fresh temporary environments and child-process termination
at deterministic boundaries: before transaction, after tentative mapping,
allocator/history/index writes, before commit, commit error, and immediately
after successful commit before response. Inject replaceable allocation faults
and MDBX map/reader/write failures separately; verify complete table consistency
on fresh-process reopen, retry and no invisible partial mapping. Do not call a
successful abort fixture evidence for a torn durable commit.

Run the eight authored Phase 3B semantic scenarios through the durable sink,
plus absent composite call -> unresolved result -> reopen -> late call -> frozen
replay -> separate resolving result. Test independent namespaces, same tokens
in different runs, conflicting source witnesses, correction receipts, zero and
exact capacity boundaries, old frontier after correction, unsupported schemas
and unavailable artifacts. Compare every exact/history/Current result and
canonical byte sequence with the reference oracle using the same admission
order; concurrency tests compare actual committed order, not thread start time.

P4-1 is the first authorized implementation boundary, contingent on qualified
integration of P4-0 and its AMXR correction. It must not be shipped
as a fully durable Conversation Core before P4-2/P4-3 recovery/frontier gates.
No production Phase 4 code is part of the current integration rehearsal.
