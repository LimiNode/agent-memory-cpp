# MDBX shared publication coordinator protocol

**Date:** 2026-10-24

**Status:** bounded implementation and conformance protocol
**Scope:** the coordinator/read-scope implementation covers one shared MDBX
context and evidence-validated publications. It is not a global writer lock.

## Purpose and evidence baseline

The durable lexical index is a rebuildable derived view. Canonical resource
content, knowledge-unit identity, projection versions, and the resource
manifest remain authoritative. A publication coordinator is needed because the
current MDBX importer can update those authorities, projections, and lexical
rows only through separate transactions. Separate calls are useful building
blocks, but they do not constitute one atomic publication or one linearizable
read.

This protocol specializes the logical contracts in the [persistent lexical
index foundation](../persistent-lexical-index-foundation.md), the [storage
backend integration roadmap](../storage-backend-integration-roadmap.md), and
the [M0 fencing-token proposal](../m0-importer-fencing-token-proposal.md).
The [implementation gap audit](../m0-implementation-gap-audit.md) describes
the current importer profiles and their intentionally weaker boundaries.

The protocol applies to one MDBX storage environment and one publication
scope. It does not imply that two environments sharing a filesystem path share
transactions.

## Design invariants

1. **One writable owner.** Exactly one coordinator-owned MDBX write
   transaction performs a publication. Nested stores may contribute rows, but
   they do not start, commit, abort, or retain an independent write
   transaction.
2. **One future publication.** A committed publication advances one active
   frontier containing the canonical/unit rows, `Original` projection rows,
   lexical index rows and statistics, immutable descriptor/snapshot binding,
   and manifest binding that the publication declares. A reader observes the
   previous complete frontier or the new complete frontier.
3. **Derived data cannot publish itself.** Tokenized rows, postings, and
   statistics may be staged and validated before commit. Staging is not
   visibility. Only the coordinator may advance the active snapshot and
   publication sequence.
4. **Admission is fenced in the transaction.** Expected revision,
   ownership/fencing token, resource generation, and manifest preconditions are
   read and checked inside the same write transaction that mutates rows.
5. **No fabricated exactness.** A missing descriptor, incomplete posting
   partition, inconsistent manifest, or unavailable frontier is reported as
   `Partial` or `Unavailable`; it is never represented as an `Exact` empty
   result.
6. **Replay is safe.** A retry with the same publication identity is an
   idempotent no-op or completes the same publication. A retry with conflicting
   content or stale predicates fails closed.
7. **Thread ownership is explicit.** MDBX transactions are thread-bound. The
   thread that begins the coordinator transaction must execute all row writes
   and the commit/abort. Prepared data may cross threads; a live transaction
   handle may not.
8. **Coordinated profiles exclude bypass writers.** Once a scope is admitted
   to the coordinated profile, every writer for that scope must present the
   profile marker and coordinator admission evidence. A legacy or independent
   store API must fail closed rather than advance canonical, projection,
   lexical, or manifest state behind the coordinator's frontier.

## Current implementation audit

The current code provides useful guarded operations, but no caller-owned
transaction spanning them:

| Component | Current boundary | Coordinator consequence |
| --- | --- | --- |
| `MdbxStorageContext` | Owns/attaches a shared `mdbxc::Connection`; MDBX transactions are thread-bound | The coordinator must be rooted in this context and own the one write transaction |
| `MdbxResourceImporter` | Lexical publication, raw-unit batches, projection staging, manifest publication, and fencing release use separate transactions | Existing sequencing is recoverable, not one atomic publication |
| `MdbxKnowledgeUnitStore` | Guarded unit batch is atomic only in its own transaction | Needs transaction-participant operations, not a second transaction |
| `MdbxProjectionStore` | `put()` starts its own transaction | Needs staged rows plus coordinator-owned apply |
| `MdbxResourceManifestStorage` | Guarded upsert checks expected manifest/token in its own transaction | Preconditions must be reused inside coordinator transaction |
| `MdbxDurableLexicalDocumentMemory` | Guarded lexical upsert has its own transaction | Must become a participant or remain an explicitly weaker profile |
| `MdbxPersistentLexicalIndex` (#540) | `flat-v2` bounded monolithic payload, historical dictionary/high-water, and its own lexical-only transaction; its read frontier is not canonical/manifest-pinned | Must remain an explicitly weaker lexical-only profile until it gains a coordinator participant boundary; it is not segmented posting storage |
| `MdbxCanonicalContentStore` | Guarded canonical operation is atomic across its own DBIs | Must participate in the same future transaction for atomic publication |

The existing intent and fencing token remain valuable recovery/ownership
mechanisms. They are not evidence that the current sequence is a single
transaction.

### Coordinated-profile admission and legacy writers

The coordinator's atomic frontier is meaningful only after a durable,
scope-bound admission transition. Activation is quiesced: existing writers
finish or are stopped, the profile/schema marker is committed, and recovery
verifies that every participating writer understands the marker. After
activation, a writer that does not understand the coordinated profile, uses a
different `MdbxStorageContext`, or calls a transaction-owning legacy operation
for the admitted scope must be rejected before it mutates any row. A shared
filesystem path or fencing token alone is not sufficient admission evidence.

The legacy operations remain usable for scopes that have not entered the
coordinated profile, and they may be retained as explicitly weaker profiles.
They must not be silently mixed with coordinated writes. If the marker is
missing, unknown, incompatible, or downgraded while a coordinated writer may
still exist, recovery fails closed. A reader that observes a coordinated scope
without a valid active frontier reports `Unavailable` (or the explicitly
weaker `NonLinearizable` contract where policy allows it), never a fabricated
`Exact` result.

The bounded implementation now enforces the part that can be proven locally:
`Complete` receipts require one participant for each authoritative family and
validate the rows written through the coordinator transaction; pinned readers
also validate the durable schema/profile/fencing metadata. This does not
quiesce arbitrary legacy writers that hold the same MDBX context. An
independent post-commit mutation is therefore detected by the read-side
evidence checks and downgrades completion, but it is not prevented globally.
Production activation still requires an external quiescence/registration gate
for every writer before advertising the coordinated profile.

## Target publication protocol

### Publication identity and preconditions

Before opening a write transaction, the caller prepares a deterministic
`PublicationPlan` for one resource and scope. The plan records:

- an idempotency/publication identity and target `publication_sequence`;
- the expected current scope-wide `publication_sequence` observed during
  preparation; the target is not authoritative until it is allocated in the
  write transaction;
- expected canonical revision and expected `ProjectionVersionRef` values;
- expected `resource_generation` and target generation;
- the ownership/fencing token and its schema/digest;
- the expected manifest revision, schema/profile, and binding set;
- the target lexical epoch/generation and immutable descriptor fingerprint;
- additions, removals, and replacements for every affected token partition;
- canonical/unit, projection, lexical, statistics, snapshot, and manifest rows
  to apply.

Preparation may tokenize, build posting blocks, compute statistics, and stage
derived rows outside a transaction. It must not allocate an active snapshot or
claim that the manifest is published.

### Single-writer sequence

The future coordinator executes the following on one thread:

1. Begin one writable MDBX transaction from the shared
   `MdbxStorageContext`.
2. Re-read the fencing token and ownership intent; reject missing, expired,
   mismatched, or superseded ownership.
3. Re-read the current scope-wide publication sequence, canonical revision,
   resource generation, projection versions, active descriptor/snapshot, and
   manifest revision/bindings.
4. Require the stored sequence to equal the plan's expected current sequence.
   A mismatch is a stale plan and aborts without changing the active frontier.
   Otherwise allocate `new_sequence = current_sequence + 1` in this same
   transaction; two plans cannot publish the same sequence.
5. Apply canonical and knowledge-unit rows, then `Original` projection rows.
   These rows become visible only if the transaction commits.
6. Apply staged lexical dictionary, posting, unit-statistics, token-statistics,
   and collection-statistics deltas. Old active contributions are removed or
   replaced in the same transaction; superseded physical rows may be retained
   only as unreachable reclaim candidates.
7. Write or validate the immutable index descriptor and active lexical
   snapshot. The snapshot must reference one compatible epoch, generation,
   schema, tokenizer, normalization, and posting layout.
8. Write the manifest binding and publication record with `new_sequence`. The
   active snapshot and manifest must point at the same target frontier.
9. Commit once. Only after a successful commit may the caller acknowledge the
   publication or release the fencing intent.

The coordinator must not call a participant API that begins or commits a
second write transaction. If any step fails, abort leaves the previous active
frontier authoritative. A post-commit release is conditional on the same
fencing token and is itself idempotent.

### Staged rows versus allowed publication

Staged derived rows are addressable only by a non-active publication identity,
epoch/generation, or intent key. Readers must ignore them. A staged row may be
promoted only when:

- all required partitions and statistics are present;
- canonical/unit and projection revisions match the plan;
- descriptor and layout validation succeeds;
- manifest preconditions and fencing checks still hold; and
- the coordinator advances the active snapshot and publication sequence in the
  same transaction as the row changes.

An implementation must not infer readiness from row count alone. Missing
partitions, a duplicate/conflicting row, or a failed validation leaves the
staging set unpublished and recoverable or discardable.

## Active frontier and crash recovery

`publication_sequence` is a scope-wide monotonically increasing frontier, not
one resource's generation. The active frontier references:

```text
ActiveFrontier = {
  scope_id,
  publication_sequence,
  canonical_revision_observations,
  projection_version_observations,
  resource_generation_observations,
  lexical_active_snapshot,
  immutable_index_descriptor,
  manifest_revision_and_bindings
}
```

The sequence is advanced only by a successful coordinator commit. Reclaim,
compaction, and deletion of superseded physical rows are not publication
steps.

Crash behavior is defined by the last durable boundary:

| Crash point | Required result after reopen |
| --- | --- |
| Before write transaction | No staging or active frontier change |
| During transaction / before commit | MDBX abort semantics expose the old frontier; staged rows remain non-active or are rolled back |
| After commit, before intent release | New frontier is authoritative; replay recognizes the publication identity and does not duplicate statistics |
| During release | Either the old intent is retained for idempotent cleanup or the matching release completes; a newer token cannot be deleted |
| Orphaned staging rows | They are ignored by readers and reclaimed only after identity/generation checks |
| Takeover after lease expiry | The new owner revalidates every predicate; stale work cannot advance the frontier |

Recovery must be idempotent and fail closed on token/schema mismatch, missing
descriptor, conflicting publication identity, or a manifest that has moved past
the expected revision. A durable intent is a recovery record, not a license to
publish rows without current fencing checks.

## Thread-bound MDBX rules

- `begin_write`, all cursor/DBI mutations, `commit`, and `abort` execute on one
  coordinator thread.
- A transaction object, cursor, or MDBX-owned buffer is never passed to a
  worker thread or retained after commit/abort.
- Workers may prepare immutable plans, encoded keys, and checksummed blocks.
  The coordinator copies or consumes those values while its transaction is
  alive.
- Participant stores receive a coordinator-owned transaction context (or an
  equivalent internal write scope) in the future implementation. Public
  convenience methods that own their own transaction remain valid only outside
  the atomic publication path.
- A shared filesystem path, shared connection configuration, or shared mutex
  is not sufficient evidence of one transaction boundary.

## Read-frontier protocol

Strict retrieval opens one pinned read-only transaction from the same
`MdbxStorageContext` and resolves the active snapshot and immutable descriptor
before reading postings. Within that transaction it reads:

1. publication sequence and active snapshot;
2. descriptor, dictionary, postings, and collection statistics;
3. unit/projection rows and their revision/generation fields;
4. canonical envelope/body and manifest bindings used for hydration.

The route records the observed frontier in its trace. A candidate is accepted
only when all identity, epoch, projection, resource-generation, lifecycle, and
manifest predicates match that frontier. A second independent read after the
transaction is released is an authoritative recheck, not a linearizable
continuation.

If the backend cannot pin all required DBIs in one read transaction, it must
declare the result `NonLinearizable` (or return `Unavailable` when no safe
result can be formed). It must not describe a sequence of independent
`find()` calls as a shared snapshot.

### Independent completion and consistency axes

Route completeness and read consistency are independent dimensions. The
dependency-free contract already models the latter as
`LexicalReadConsistency::{PinnedSnapshot, NonLinearizable}`; it must not be
folded into `LexicalRouteCompletion`.

```text
LexicalRouteCompletion: Exact | Partial | Unavailable
LexicalReadConsistency: PinnedSnapshot | NonLinearizable
```

The strict retrieval policy permits `Exact` only for the pair
`(Exact, PinnedSnapshot)`. A `(Partial, PinnedSnapshot)` result may be useful
when explicitly covered partitions are complete and policy permits partial
fusion. A `NonLinearizable` result is an explicitly weaker contract and must
remain marked as such even if the observed candidate set happens to be
complete; it is never silently upgraded to strict `Exact`.

### Route completion outcomes

- **`Exact`**: every requested token partition is complete for one active
  snapshot; dictionary/posting/statistics rows, descriptor, canonical/unit,
  projection, and manifest observations all agree in the pinned frontier; no
  required partition is unavailable or merely staged.
- **`Partial`**: at least one safe partition and candidate set is available,
  but one or more explicitly reported partitions are unavailable, stale, or
  incomplete and policy allows partial fusion. The response includes the
  frontier and the missing partitions.
- **`Unavailable`**: no safe partition can support the requested route, or the
  snapshot/descriptor/frontier is absent or incompatible.

`Exact` is forbidden for a zero-hit result when membership itself is
incomplete. A complete partition with no matching postings may legitimately
produce an exact empty result.

## Conformance and fault matrix

An implementation PR must add focused tests for the following matrix. Each
negative row must assert both the returned status and that the active frontier
did not advance.

| Fault or scenario | Required assertion |
| --- | --- |
| Wrong fencing token / takeover race | Write fails closed; no canonical, lexical, or manifest mutation becomes active |
| Coordinated profile versus legacy writer | After quiesced activation, the legacy writer is rejected before mutation; an unmarked or incompatible profile cannot advance the active frontier |
| Expected revision or generation changed | Conflict is rejected; old frontier remains readable |
| Scope-wide sequence race | Two plans prepared from one sequence cannot both commit; one observes the CAS conflict and the winner allocates exactly `current + 1` |
| Manifest schema/revision/binding mismatch | No publication; mismatch is diagnosable |
| Missing projection or incomplete token partition | `Partial`/`Unavailable`, never `Exact` |
| Staged rows without snapshot promotion | Readers ignore rows; active sequence is unchanged |
| Duplicate/conflicting publication identity | Identical replay is a no-op; different content is rejected |
| Crash before commit | Reopen exposes the prior complete frontier |
| Crash after commit before release | Reopen exposes the new frontier exactly once; release/replay is idempotent |
| Descriptor/tokenizer/layout mismatch | Snapshot admission fails before scoring |
| Shared-token DF/CF replacement | Old statistics are restored or replaced atomically; no mixed generation |
| Erase/update | Active contribution changes exactly once; unrelated resources remain visible |
| Worker-thread transaction misuse | Test or API guard rejects cross-thread handle use |
| Read transaction closes early | Consistency is `NonLinearizable` or the request fails; completion is not silently upgraded to strict `Exact` |
| Completion/consistency combination | Strict `Exact` is emitted only for `(LexicalRouteCompletion::Exact, LexicalReadConsistency::PinnedSnapshot)` |
| Reopen with orphan intent/staging | Recovery validates token and identity; stale or corrupt state fails closed |
| Scope isolation | Same token in two scopes cannot cross-query |
| Oracle parity | IDs, scores, tie order, stale filtering, and empty/budget outcomes match the dependency-free oracle |

Fault injection must cover every boundary in the sequence, not only the final
manifest write. A passing happy-path import is insufficient evidence of atomic
publication.

## Current capabilities and missing prerequisites

### Available now

- Shared `MdbxStorageContext` and thread-bound MDBX transaction semantics.
- Guarded fencing-token and expected-manifest checks in existing importer
  profiles.
- Durable canonical, unit, projection, manifest, and lexical slices with
  profile-specific recovery behavior.
- Dependency-free lexical identity, statistics, active-snapshot, and
  `ExactLexicalIndex`/BM25 oracle contracts.
- Explicit `Exact`/`Partial`/`Unavailable` completion concepts in the retrieval
  roadmap.
- Coordinator-owned metadata, authoritative participants, durable receipts,
  evidence-validated complete publications, and a pinned read scope for the
  shared MDBX context.

### Not yet available

- A scope-bound coordinated-profile marker, quiesced activation, and legacy
  writer rejection path (the external activation gate remains outstanding).
- Full crash/fault-injection coverage for every participant boundary and
  production recovery orchestration around orphaned staging.
- Proof that the current independent importer transactions are linearizable;
  they are not claimed to be.

## Implemented backend-specific ownership shape

The implementation uses an internal, backend-specific transaction participant
boundary rather than exposing a generic transaction type in the public storage
contracts:

- a coordinator-owned write scope created from `MdbxStorageContext`;
- a scope/profile admission check that rejects legacy writers after coordinated
  activation;
- participant methods that apply prepared canonical, unit, projection,
  lexical/statistics, snapshot, and manifest deltas to that scope;
- one coordinator commit/abort and a commit receipt containing publication
  sequence and idempotency identity;
- a read-frontier scope that pins one read transaction and exposes validated
  observations;
- explicit rejection when a participant is attached to another context,
  thread, profile, or DBI manifest.

This section intentionally proposes ownership and acceptance requirements, not
the names or signatures of a new API. Public convenience operations may remain
transaction-owning for non-coordinated callers.

## Bounded implementation sequence

1. Add a transaction test double and value-level `PublicationPlan`/frontier
   conformance tests; no DBI or public API changes.
2. Add an internal MDBX coordinator scope and convert one participant at a time,
   starting with canonical/unit rows and manifest preconditions.
3. Add `Original` projection and lexical/statistics participants, then publish
   the active snapshot and publication sequence atomically.
4. Add reopen, crash/fault, takeover, replay, erase, and shared-token statistic
   tests; compare every result with the dependency-free oracle.
5. Add the pinned read-frontier and explicit completion statuses to the bounded
   reader, including `NonLinearizable` fallback behavior.
6. Only after these gates pass, measure batch size, write amplification,
   reclaim, and query cost. Compression, positions, BM25F, WAND/BMW, and
   compaction remain separate implementation decisions.

## Explicit non-goals

This protocol does not:

- provide global quiescence for every legacy writer or make independent
  importer calls atomic by documentation alone;
- turn the lexical index into canonical authority;
- define a distributed transaction, replication protocol, or multi-environment
  consensus mechanism;
- choose a posting codec, block layout, scorer variant, pruning algorithm, or
  reclaim scheduler;
- promise zero-copy cross-thread transaction use;
- make incomplete or independently-read data `Exact`;
- replace the existing fencing-token migration/recovery rules;
- authorize merging or changing PR #540.

## References

- [Persistent lexical index foundation](../persistent-lexical-index-foundation.md)
- [Storage backend integration roadmap](../storage-backend-integration-roadmap.md)
- [M0 fencing-token proposal](../m0-importer-fencing-token-proposal.md)
- [M0 implementation-gap audit](../m0-implementation-gap-audit.md)
- [M0 resource importer raw publication](2026-10-18-m0-resource-importer-raw-publication.md)
- [M0 fencing-token experiment](2026-10-19-m0-fencing-token.md)
- [Experiment-note policy](README.md)
