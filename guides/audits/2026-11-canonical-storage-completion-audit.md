# CanonicalContent Storage Completion Audit

**Audit baseline:** `main@caaacc67d367a13717c2da96a1e2ce6da9bd6524`.

**Status:** audit-only. This document does not change the C1 persisted format,
open DBIs, production defaults, or the public domain contracts.

## Result

The current MDBX adapter is a coherent C1 raw/plain profile. It is not yet the
flat block-addressable profile described by the storage-completion plan. The
boundary is important: the adapter already preserves the logical revision and
body-generation invariants, so a new physical profile must be versioned and
introduced beside C1 rather than by reinterpreting existing rows.

No storage rewrite is authorized by this audit. The next implementation slice
is a separate, opt-in profile with typed records and a compatibility fixture;
lazy navigation and migration remain subsequent slices.

## Observed C1 model

`MdbxCanonicalContentStore` currently owns four DBIs:

| DBI | Record represented | Current behaviour |
| --- | --- | --- |
| `*_canonical_heads` | document ID → current document revision | mutable head pointer |
| `*_canonical_revisions` | `(document ID, document revision)` → revision metadata and ordered block descriptors | immutable revision rows |
| `*_canonical_bodies` | `(document ID, body revision, encoding generation)` → one encoded body | immutable raw body rows |
| `*_canonical_ledgers` | document ID → historical block-ID no-reuse ledger | mutable monotonic ledger |

The revision row carries block identity, kind, parent relation, order and block
revision. The body row carries the corresponding block text and repeats the
structural fields for binding validation. A read decodes the complete body and
then binds it to the revision row. `read_block()` therefore has correct C1
semantics but is not a physical random block read.

The adapter uses explicit version markers, bounded length-prefixed decoding,
logical SHA-256 digests, physical checksums, and a single MDBX transaction for
accepted edits. These are existing properties, not claims about the planned
flat profile.

## Invariants that a new profile must preserve

- `DocumentRevision` identifies the published document state.
- `BlockRevision` is logical block history and may advance when placement or
  other canonical block state changes.
- `BodyRevision` identifies the decoded canonical body bound to a revision;
  metadata-only edits may reuse it.
- `PhysicalEncodingGeneration` identifies a physical representation and must
  not alter logical document, block, body, or projection identities.
- Historical block IDs remain reserved after publication and are not silently
  assigned to a different logical block.
- All related reads for one result use one MDBX snapshot; no MDBX-owned view is
  allowed to escape the read operation.

## Gap classification

| Planned capability | Current evidence | Classification | Safe next step |
| --- | --- | --- | --- |
| Typed storage records | Local `DecodedRevision`, `DecodedBody`, and codec helpers inside the MDBX implementation | partial | Extract versioned storage records/codecs without changing the C1 DBIs |
| Independent block records | One body payload contains all block text | absent | Add a new schema generation and opt-in profile; do not reinterpret C1 rows |
| Numeric storage identities | Public string IDs are used directly in C1 keys and payloads | absent | Introduce monotonic mapping records with reopen/no-reuse tests |
| Structural index | Parent links are decoded from the full revision/body | absent | Store an immutable per-revision structure record and define its key ordering |
| Lazy outline/children/subtree reads | `CanonicalNavigation` is an in-memory reference implementation; MDBX reads a full revision | contract only | Implement snapshot-scoped backend reads after the structural index exists |
| C1 migration | No migration writer or active-generation switch | absent | Build a read-only C1 compatibility fixture before writing migration code |

## Required profile boundary

The flat profile must use a new schema marker and DBI names. It must not treat
`canonical_revisions` or `canonical_bodies` as if they already contained flat
records. A profile selector must make the choice explicit, and an unopened
profile must not create its DBIs as a side effect of opening C1.

The minimum typed-record set is:

- document head and document revision record;
- immutable structure record with deterministic preorder/sibling order;
- block identity mapping (`ContentBlockId` ↔ non-zero `BlockStorageId`);
- immutable block payload/version record;
- historical ID ledger and schema-generation marker.

Each record needs an explicit version, bounded decoder, canonical byte order for
numeric fields, and fail-closed validation. C++ object layout, padding,
`memcpy`, and STL iteration order are not persistence formats.

## Acceptance sequence

1. Add typed records/codecs and unit tests without changing C1 reads or writes.
2. Add an opt-in flat profile with independent DBIs and deterministic numeric
   mappings. Verify create, reopen, historical read, delete/no-reuse, overflow,
   and foreign-document rejection.
3. Add snapshot-scoped outline, children, block, bounded subtree, and full
   materialization operations. Verify that outline reads do not decode payload
   records and that malformed links, cycles, missing records, and budgets fail
   closed.
4. Create compatibility fixtures from real C1 databases. Migrate into a new
   generation, compare logical content/digests/metadata/history, then publish
   the generation atomically. Exercise interruption and rollback before any
   default selection.
5. Benchmark C1 against the flat profile at declared block counts. Separate
   domain validation, structural mutation, codec, MDBX I/O, and materialization
   costs; do not infer a performance gain from the DTO extraction alone.

Until steps 1–4 are complete, the flat profile remains experimental and C1
remains the only default canonical-content profile.
