# Persistent Lexical Index Foundation

Status: Proposed architecture contract. This document is intentionally
documentation-only; it does not add a DBI, public C++ API, codec, or scorer.

The contract is the next step after the bounded projection-aware retrieval
slice in PR #533. It defines the boundary for a durable lexical `IndexStore`
without turning the current in-memory indexes or the M0 durable lexical
document profile into a second canonical storage system.

## Decision summary

The durable retrieval path is:

```text
Canonical ResourceBody / KnowledgeUnit
        -> revision-bound SearchProjection
        -> lexical IndexStore
        -> bounded candidate reader
        -> canonical revision and manifest validation
        -> exact source hydration
```

The canonical body, `KnowledgeUnitId`, `ChunkId`, `SegmentSet` identity,
`ContentHash`, and source revision identities remain unchanged. Lexical rows
are rebuildable derived data. A lexical index is visible only through an
active snapshot that is compatible with the manifest and projection frontier
used by the reader.

This proposal deliberately does not choose WAND/BMW, BM25F, positional
postings, a compression codec, or a new physical MDBX layout. Those choices
must be evaluated against the shared logical contract and independent scorer
oracles first.

## Scope and non-goals

In scope:

- a stable identity for one lexical index incarnation;
- projection- and revision-aware posting/statistics records;
- collection statistics that can be read consistently with postings;
- atomic active-snapshot publication and crash/replay rules;
- bounded posting iteration and selective canonical hydration;
- lifecycle and conformance gates for the first persistent implementation.

Out of scope:

- changing canonical content, envelope identity, or importer publication
  guarantees;
- replacing `ILexicalIndex`, `ExactLexicalIndex`, or the M0 durable lexical
  document-memory profile in this architecture PR;
- adding a universal migration manager;
- implementing BM25F, WAND/BMW, phrase positions, compressed blocks, or
  background compaction;
- claiming that independent unit, projection, manifest, and index calls form a
  linearizable read without a shared frontier implementation.

## Current state

| Area | Current state | Contract status |
| --- | --- | --- |
| Lexical value types and `ILexicalIndex` | Implemented dependency-free M0 baseline | Chunk-oriented; not a durable projection index |
| `ExactLexicalIndex` / `KeywordOverlapIndex` | Implemented in-memory indexes | Oracles and small-corpus baselines |
| `MdbxDurableLexicalDocumentMemory` | Implemented M0 durable catalog and guarded importer path | Durable document-memory slice, not the projection-aware IndexStore |
| `ResourceManifest` and `Original` projections | Implemented and revision-checked | Publication and provenance inputs |
| Projection-aware bounded retrieval | Implemented by PR #533 | Rebuilds a bounded lexical view in memory |
| Descriptor, snapshot and read-frontier value contracts | Implemented dependency-free baseline | No persistent rows or pinned storage transaction yet |
| Flat lexical postings/statistics baseline | Implemented dependency-free logical backend | No MDBX durability or publication transaction yet |
| Epoch-keyed postings, token/stat rows, active snapshot | Roadmap and DBI-manifest reservations exist | The bounded MDBX adapter persists a versioned flat logical payload; segmented/compressed rows remain deferred |
| Shared unit/projection/manifest/index read frontier | Missing | Required before a strict snapshot claim |

The DBI manifest contains roadmap entries for lexical tables and the reserved
`lexical_posting_segments` capability. Those entries are capacity and logical
planning records, not permission to open new tables in this PR. An
implementation PR must validate the selected profile, DBI budget, wire-sync
semantics, and migration policy before changing the manifest.

The first adapter slice uses the already reserved `lexical_posting_segments`
DBI with a single versioned key/value payload per `(scope, projection)`. The
payload stores descriptor identity, active epoch/generation, publication
sequence, the complete historical token dictionary/high-water allocator, and
revision-bound unit inputs; the dependency-free
`FlatLexicalIndex` reconstructs postings and aggregate statistics on reopen.
The physical key is explicitly a `flat-v2` baseline representation, not a
segment-row encoding of the manifest's future
`(ScopeId, ProjectionKind, LexicalStatisticsEpoch, FieldId, TokenId,
SegmentId)` key. Segment readers must reject this value rather than decode it
as immutable posting rows. This representation has a deliberate migration
boundary: changing its codec or moving to segment rows requires a quiesced
rebuild into a new payload/layout version and epoch; no rolling migration is
claimed.
This is an intentionally bounded durable baseline: it proves atomic
publication, rollback, replay, scope isolation, descriptor admission, and one
read transaction for a coherent frontier, but it does not claim compressed
segments, scorer execution, canonical hydration, or the final row-per-posting
layout. A future segmented layout must allocate a new posting layout/epoch and
must not reinterpret this payload in place. Its read frontier is deliberately
lexical-only: because this adapter does not own canonical units, projections,
or manifests, it reports `NonLinearizable` and cannot claim an `Exact` route
until a later coordinator pins those DBIs in the same transaction. Payload
version `v2` is admission-checked and fail-closed; unknown payloads, descriptor
mismatches, and incompatible layouts are not interpreted as legacy rows. There
An existing `flat-v1` key for the same scope/projection is detected explicitly
and opening the adapter fails with a quiesced-rebuild requirement. There is no
rolling migration path in this slice: changing the payload codec or
physical row meaning requires a quiesced rebuild into a new epoch and layout
version.

The flat baseline is deliberately bounded. Configurable admissions cap the
serialized payload bytes, active unit count, active token occurrences,
historical dictionary entries, and one normalized-token byte length. Limits
are checked before rebuilding the flat oracle, before decoding untrusted
collection counts, and again against the complete post-state before the MDBX
write. The implementation is an O(N)
read-modify-write prototype; it must not be described as an incremental or
scalable persistent backend until segmented storage replaces the value rewrite.

The dependency-free baseline also exposes a bounded posting partition helper.
For one token it emits deterministic, unit-id-ordered segments with contiguous
local segment ids and binds every segment to the active statistics epoch,
generation, and posting layout. This is a preparation/conformance surface only:
it copies logical postings in memory, has no durable segment rows, no codec, and
does not change the flat MDBX payload or its migration policy. A durable adapter
must validate the same complete membership and generation binding before
publishing segment rows.

The read-integrity slice adds a dependency-free reconstruction admission
helper. A caller supplies the expected epoch/generation/layout, segment and
posting counts, segment bound, and an independent ordered posting witness. The
helper accepts only contiguous segment ids, matching metadata, bounded and
strictly ordered unit ids, complete counts, and exact witness membership. It
returns `ConfirmedEmpty` only for an explicitly expected empty partition; an
absent non-empty partition remains a non-complete result. This validation is
separate from `LexicalRouteCompletion` and `LexicalReadConsistency`: it does
not manufacture a storage snapshot or claim canonical/manifest linearizability.

## Index identity

Every persisted lexical index is identified by the complete tuple below. A
cache, posting block, statistics row, or active pointer that omits one of
these fields is not safely reusable.

```text
LexicalIndexIdentity = {
    scope_id,
    projection_kind,
    projection_schema_id,
    projection_schema_version,
    tokenizer_fingerprint,
    normalization_fingerprint,
    statistics_epoch,
    statistics_generation,
    posting_layout_version
}
```

`statistics_epoch` names one non-reused index incarnation. A full rebuild,
restore over potentially stale rows, or incompatible posting-layout migration
allocates a new epoch. `statistics_generation` advances within that epoch for
an active replacement. `posting_layout_version` describes decoding, not
canonical identity.

The epoch is not self-describing. Every epoch must have one immutable durable
`IndexDescriptor` containing the projection schema, tokenizer and normalization
fingerprints, statistics epoch, supported posting-layout versions, and other
scorer-independent decoding metadata. The active snapshot is a mutable
reference to that descriptor plus its current statistics generation. Opening a
snapshot resolves and validates the descriptor before reading any dictionary,
posting, statistics, or segment row. A missing descriptor or a
fingerprint/layout mismatch is an explicit compatibility failure, never a
reason to score rows because their epoch key happens to exist.

Per-unit rows additionally carry:

```text
KnowledgeUnitId
ProjectionVersionRef
ResourceId
resource_generation
```

`ProjectionVersionRef` proves which unit revision and derivation produced the
text. `resource_generation` binds the row to the active resource revision.
Neither field is replaced by the collection epoch, and the epoch is never
used to manufacture or change a unit ID.

## Logical persisted model

The first durable implementation must be expressible using these logical row
families. The physical representation may use flat key/value rows or immutable
segments, but it must preserve the same meaning:

| Logical rows | Required fields | Purpose |
| --- | --- | --- |
| Token dictionary | `(scope, projection, epoch, normalized_token) <-> TokenId` | Stable index-local token allocation |
| Posting rows | token, field, unit, `ProjectionVersionRef`, resource generation, TF, optional positions | Candidate membership and term frequency |
| Unit statistics | unit, projection version, resource generation, token count and per-field lengths | Length normalization and freshness checks |
| Token statistics | token, document frequency, collection frequency | IDF and term-frequency accounting |
| Collection statistics | epoch, generation, unit count, total token count | `avgdl` and scorer denominator |
| Index descriptor | immutable epoch descriptor, schema and tokenizer/normalization fingerprints, layout compatibility | Reconstructs and validates one index incarnation |
| Active snapshot | `(scope, projection) -> epoch, generation, layout` | Mutable pointer to one compatible aggregate view; DBI key supplies scope/projection |

The first implementation may omit positions, but it must reserve a versioned
way to add them later. Aggregate statistics must describe the same active unit
set as postings; a token cache must never combine rows from different epochs or
generations.

## Publication contract

Heavy tokenization and block construction happen outside the write transaction.
Publication is short, fenced, and idempotent:

1. Read the current resource manifest, projection versions, and active lexical
   snapshot. Prepare candidate rows for a specific resource generation and
   `ProjectionVersionRef`.
2. In one writable publication transaction, verify the expected resource
   generation and ownership/fencing token. Reject stale workers before changing
   lexical rows.
3. Remove the old active contribution from token and collection statistics,
   apply the replacement contribution, and write the new per-unit rows.
4. Advance the collection-wide `publication_sequence` and publish the
   replacement active snapshot and manifest binding in the same transaction.
   A reader must see either the previous complete view or the new complete
   view, never a mixture.
5. Record superseded physical rows for delayed reclaim. Reclaim is idempotent,
   generation-aware, and must not be required for query correctness.

If the transaction aborts, the old active snapshot remains authoritative and
no partial aggregate is readable. Recovery may replay a pending operation only
after rechecking the expected generation, projection versions, epoch, and
fencing token. A replay that cannot prove those predicates fails closed.

The current importer does not yet own this combined transaction: it currently
publishes durable lexical data, raw-unit batches, staged projections, and the
guarded manifest through separate operations. The first persistent IndexStore
implementation must therefore extend the publication boundary rather than add
another `upsert_manifest()` call after index writes. For the MDBX slice, a
coordinator using the shared `MdbxStorageContext` must own one writable
transaction covering canonical/unit rows, projection rows, lexical rows,
statistics, the active snapshot, and the manifest binding. Existing fencing
tokens and expected-manifest checks are admission predicates inside that
transaction, not post-commit checks.

Backends without a shared transaction may use a durable two-phase intent, but
their visibility contract is weaker: the old complete manifest/frontier remains
active until the derived index is ready, or the reader returns
`Partial`/`Unavailable` when a newer manifest is visible without complete index
membership. Such a backend must not claim atomic publication or silently return
an incomplete exact result. Recovery and retry must clear or replay the intent
idempotently before advancing the active frontier.

The contract is compatible with the existing importer guarantees: raw units,
`Original` projections, body/revision bindings, and the manifest remain the
publication boundary. The lexical index cannot make an unpublished staging row
visible.

The dependency-free `FlatLexicalIndex` is the first logical baseline for these
row families. It validates projection/revision identity and maintains flat
postings, token statistics, collection statistics, and the active generation in
one process. It is intentionally not a durable adapter: it has no MDBX rows,
reopen protocol, crash recovery, or publication transaction. Those guarantees
belong to the subsequent storage slice and must be tested against the same
logical state and oracle.

The flat baseline is deliberately body-only and accepts `ProjectionKind::Original`
only. It does not claim field-aware or BM25F semantics; unsupported projection
kinds are rejected at construction. Replacing an active unit is guarded by the
complete expected current identity (`unit_revision`, `resource_generation`, and
`ProjectionVersionRef`). An identical identity and identical normalized token
sequence is an idempotent replay, while an identical identity with different
content, a stale identity, or an unguarded replacement fails closed. This
in-memory admission rule is a logical conformance guard, not a substitute for
the durable fencing token required by the MDBX publication adapter.
The derivation fingerprint is opaque identity data; it is never ordered as a
version. A fingerprint change without an explicit numeric revision or
generation transition is rejected.

All tokenization, dictionary allocation, and posting construction are staged
before the active state is changed. A failed commit restores the previous unit,
statistics, postings, and generation; deterministic failure hooks in the
conformance test exercise both the preparation and partial-posting paths. The
hook is test-only and does not change the future durable transaction contract.

## Read frontier

Strict retrieval requires a future shared read-frontier object with at least:

```text
ReadFrontier = {
    scope_id,
    publication_sequence,
    lexical_active_snapshot,
    immutable_index_descriptor,
    pinned_read_transaction,
    per-resource manifest/revision observations observed in that transaction
}
```

`publication_sequence` is a collection-wide frontier identifier, not the
generation of one arbitrary resource. Per-resource `resource_generation` and
manifest bindings remain row-level evidence inside the frontier; they cannot be
collapsed into one resource generation. For the first MDBX adapter, the
preferred mechanism is one pinned read-only transaction over every required
DBI in the same storage environment. An adapter spanning multiple environments
must provide an equivalent durable publication sequence or return an explicit
non-linearizable/unavailable outcome; it must not pretend that independent
reads form one snapshot.

The candidate reader obtains the active lexical snapshot (whose scope and
projection come from the authoritative DBI key) and its descriptor
inside that frontier, then reads postings, unit statistics, manifests,
projections, and canonical envelopes against the same frontier. A mismatched
epoch, generation, projection version, resource generation, lifecycle state,
or manifest binding is a stale miss, not a best-effort result.

Until storage adapters expose this shared frontier, an adapter may perform
authoritative rereads as PR #533 does, but it must document that the request is
not linearizable. A collection of independent `find()` calls is not a pinned
snapshot.

## Route completion and incomplete membership

The lexical route must distinguish a stale candidate from an incomplete active
posting partition. The existing roadmap contract uses
`LexicalRouteCompletion::{Exact, Partial, Unavailable}` and explicit covered and
unavailable token partitions:

- `Exact` is allowed only when every requested token partition was reconstructed
  against one active snapshot and scored with complete active membership.
- A stale posting or stale unit may be rejected as an individual candidate when
  the requested partition is otherwise complete.
- Missing postings, an absent active snapshot, an epoch/layout mismatch, or an
  overlay that cannot reconstruct every added/removed/replaced posting makes
  that token partition unavailable. The route returns `Partial` only when the
  unavailable partitions are explicitly reported and policy permits partial
  fusion; it returns `Unavailable` when no safe partition remains.
- An incomplete partition must never be silently omitted and returned as an
  `Exact` empty or partial top-K. No scorer, conservative bound, or disabled
  pruning setting can turn incomplete membership into an exact result.

The C++ `LexicalUnitRevisionObservation` is structural evidence collected by
the adapter; its validation does not itself read a `KnowledgeUnitEnvelope` or
manifest. The adapter must set both active-binding flags only after comparing
unit scope/revision, projection version, resource generation, and manifest
bindings in the same pinned transaction. This completion status is part of the
reader result and retrieval trace. It is not inferred later from the number of
returned hits.

## Erase, update, and rebuild

- Updating one resource replaces only its active lexical contribution and
  manifest bindings; unrelated resources remain addressable.
- Erasing a unit removes one logical active contribution. Lookup may be O(N) in
  the first flat implementation; this allowance applies to erase or reindex
  bookkeeping, not to query traversal. Query execution remains posting-driven
  and bounded by token partitions/candidate limits. Mass reclaim and
  compressed-block rewrite are separate bounded jobs.
- A full rebuild writes a new epoch namespace, validates parity, and atomically
  advances the active pointer. Old epoch rows are unreachable before delayed
  reclaim.
- A stale posting is never scored merely because the unit still exists. It must
  match the active `ProjectionVersionRef`, resource generation, epoch, and
  manifest publication.

## Conformance matrix

The first implementation PR must provide focused tests for:

| Gate | Required evidence |
| --- | --- |
| Identity | Same unit ID with a new projection revision creates a replacement row, not a second active contribution |
| Scope isolation | Identical tokens in two scopes cannot cross-query |
| Freshness | Stale unit revision, projection version, resource generation, and manifest are rejected |
| Descriptor admission | Active snapshot resolves one immutable descriptor; incompatible schema, tokenizer, normalization, or layout is rejected before scoring |
| Read frontier | Unit, projection, manifest, lexical snapshot, and descriptor are read from one pinned frontier or the route is explicitly non-linearizable/unavailable |
| Statistics | TF/DF, collection frequency, unit count, token count, and `avgdl` agree with the active set |
| Publication | Commit/reopen sees one complete snapshot; aborted publication leaves the prior snapshot intact |
| Publication lag | A manifest/index mismatch is reported as `Partial`/`Unavailable`, never as an exact incomplete result |
| Replay | Retry after crash or takeover is idempotent and does not duplicate IDs or statistics |
| Rebuild | New epoch is isolated from old dictionary, postings, stats, caches, and blocks |
| Erase | Active contribution disappears; delayed physical reclaim cannot expose it again |
| Ordering | Equal scores have deterministic `(KnowledgeUnitId, revision)` tie order |
| Hydration | Candidate rows hydrate canonical text only after exact frontier validation |
| Corruption | Malformed keys, duplicate active rows, epoch mismatch, and inconsistent stats fail closed |

Golden parity must compare the persistent reader with the dependency-free
`ExactLexicalIndex`/BM25 oracle on the same token stream. It must compare IDs,
scores within a declared tolerance, tie ordering, stale filtering, and empty
or budget-exceeded outcomes—not only wall-clock time.

## Implementation sequence

1. Freeze dependency-free identity/value contracts and a read-frontier test
   double. No DBI changes.
2. Implement a flat persistent posting/statistics backend using the logical row
   families above. Keep the codec uncompressed and versioned.
3. Add atomic publication, recovery, reopen, erase, and targeted reindex tests
   against MDBX. Validate the DBI manifest and profile budget in that PR.
4. Add a bounded candidate reader and canonical hydration path. Compare against
   the in-memory oracle before measuring performance.
5. Add repeated benchmark runs for build, update, query CPU, bytes read/written,
   stale rejection, and reclaim. Preserve a small deterministic fixture in the
   repository; keep large reports in experiment artifacts.
6. Use the dependency-free bounded partition helper as the parity fixture for a
   durable segment-row adapter; prove reopen, generation mismatch, incomplete
   membership, and atomic publication before introducing a codec.
7. Only after the flat implementation is correct, evaluate positions,
   compressed posting blocks, BM25F, and WAND/BMW as separate changes.

## Explicit decisions deferred

The following are intentionally not decided here:

- whether the first physical layout uses one posting DBI or several logical
  tables;
- duplicate-sorted versus encoded key/value postings;
- block size, compression codec, skip data, and score-bound encoding;
- BM25 variant parameters beyond the existing scorer oracle;
- whether a shared read frontier is implemented as one MDBX read transaction,
  an adapter-owned snapshot token, or a higher-level coordinator;
- reclaim scheduling and retention windows.

Any implementation PR that changes one of these decisions must update this
contract, the DBI manifest, migration notes, and its conformance evidence in
the same review.
