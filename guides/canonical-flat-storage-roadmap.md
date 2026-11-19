# Numeric flat canonical storage

**Status:** Draft opt-in C1 profile. This document describes the experimental
`MdbxCanonicalFlatStorage` adapter; it does not replace
`MdbxCanonicalContentStore` and it does not change a production default.

## Physical contract

The adapter uses the persistent `MdbxStorageIdentityMapping` dictionary for
numeric `DocumentStorageId` and `BlockStorageId` values. The following
profile-local DBIs are owned by the adapter (with the configured table
prefix):

| DBI | Key | Value |
| --- | --- | --- |
| `canonical_flat_meta_v1` | text marker | schema marker |
| `canonical_flat_heads_v1` | numeric document id | current revision |
| `canonical_flat_structures_v1` | `document-id/revision` | versioned `AMFS` structure row |
| `canonical_flat_blocks_v1` | `block-id/revision` | versioned `AMFB` block row |

The `AMFB` and `AMFS` codecs carry an explicit version, logical SHA-256
digest, and physical checksum. A structure row contains the canonical preorder
and metadata; block rows contain text and immutable block revision. A point
read decodes one structure row and one selected block row. It does not
materialize unrelated block text.

Creation and edit publication allocate mappings and write block versions,
structure, and head in one caller-owned MDBX write transaction. If the write
fails, the transaction is rolled back. Allocated numeric identities are
monotonic and are never reused; an aborted allocation may remain reserved.

Unknown, markerless, or corrupt profile state is rejected. `max_records` and
the codec limits are checked before unbounded reservation or decoding. The
profile is deliberately separate from the legacy textual canonical tables;
no implicit migration is performed.

## Validation boundary

The focused MDBX test covers create/reopen, historical revision retention,
numeric mapping, point block reads, optimistic edit publication, and
markerless-state rejection. It is a conformance slice, not a production
acceptance result. Before any activation, add a fresh-process OLD/NEW gate,
corruption and fault-injection replay, and compare full materialization with
the canonical reference adapter.

The benchmark gate is separate from correctness. It must report fixed-seed
large-document runs for point block, bounded subtree, single-block update,
and full materialization, including physical bytes, allocations, CPU, and
latency. No performance claim is made by this Draft PR.
