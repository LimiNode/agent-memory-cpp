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
| Epoch-keyed postings, token/stat rows, active snapshot | Roadmap and DBI-manifest reservations exist | No production implementation yet |
| Shared unit/projection/manifest/index read frontier | Missing | Required before a strict snapshot claim |

The DBI manifest contains roadmap entries for lexical tables and the reserved
`lexical_posting_segments` capability. Those entries are capacity and logical
planning records, not permission to open new tables in this PR. An
implementation PR must validate the selected profile, DBI budget, wire-sync
semantics, and migration policy before changing the manifest.

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
| Active snapshot | `(scope, projection) -> epoch, generation, layout` | Single readable pointer to a compatible aggregate view |

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
4. Publish the replacement active snapshot and manifest binding in the same
   transaction. A reader must see either the previous complete view or the new
   complete view, never a mixture.
5. Record superseded physical rows for delayed reclaim. Reclaim is idempotent,
   generation-aware, and must not be required for query correctness.

If the transaction aborts, the old active snapshot remains authoritative and
no partial aggregate is readable. Recovery may replay a pending operation only
after rechecking the expected generation, projection versions, epoch, and
fencing token. A replay that cannot prove those predicates fails closed.

The contract is compatible with the existing importer guarantees: raw units,
`Original` projections, body/revision bindings, and the manifest remain the
publication boundary. The lexical index cannot make an unpublished staging row
visible.

## Read frontier

Strict retrieval requires a future shared read-frontier object with at least:

```text
ReadFrontier = {
    scope_id,
    resource_manifest_generation,
    lexical_index_identity,
    compatible unit/projection read snapshot
}
```

The candidate reader obtains the active lexical snapshot first, then reads
postings, unit statistics, manifests, projections, and canonical envelopes
against that compatible frontier. A mismatched epoch, generation, projection
version, resource generation, lifecycle state, or manifest binding is a stale
miss, not a best-effort result.

Until storage adapters expose this shared frontier, an adapter may perform
authoritative rereads as PR #533 does, but it must document that the request is
not linearizable. A collection of independent `find()` calls is not a pinned
snapshot.

## Erase, update, and rebuild

- Updating one resource replaces only its active lexical contribution and
  manifest bindings; unrelated resources remain addressable.
- Erasing a unit removes one logical active contribution. Lookup may be
  O(N) in the first flat implementation; mass reclaim and compressed-block
  rewrite are separate bounded jobs.
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
| Statistics | TF/DF, collection frequency, unit count, token count, and `avgdl` agree with the active set |
| Publication | Commit/reopen sees one complete snapshot; aborted publication leaves the prior snapshot intact |
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
6. Only after the flat implementation is correct, evaluate positions,
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
