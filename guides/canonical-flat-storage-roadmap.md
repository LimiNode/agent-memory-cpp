# Numeric flat canonical storage

**Status:** Implemented experimental adapter; not a production default.
The normative semantics and validation complexity are owned by
[Canonical content storage](canonical-content-storage-roadmap.md#10-acceptance-gates).
This note records only the adapter's physical schema.

## Physical contract

All names below include the configured table prefix. The adapter shares the
persistent identity dictionary described in
[Storage identity mapping](storage-identity-mapping.md).

| DBI suffix | Key and comparator | Value |
| --- | --- | --- |
| `canonical_flat_meta_v2` | text, flags 0 | `@schema_version` = `canonical_flat_storage.v2.be64x2.envelope_sha256` |
| `canonical_flat_heads_v2` | native uint64, `MDBX_INTEGERKEY` | 8-byte little-endian document revision |
| `canonical_flat_structures_v2` | BE64 document ID + BE64 revision, flags 0 | bound structure envelope |
| `canonical_flat_blocks_v2` | BE64 block ID + BE64 revision, flags 0 | bound block envelope |

The 16-byte composite keys sort lexicographically by the two unsigned numeric
fields. Only these composite keys are portable encodings; native integer
MDBX tables retain the environment's platform compatibility constraints.

An `AMF2` envelope binds its type, physical key, document identity and inner
`AMFS`/`AMFB` v1 payload with SHA-256. Each structure also binds block storage
IDs, text sizes, block payload digests and the historical no-reuse ledger.
Existing immutable rows permit identical-only idempotence.

Mappings, selected new block versions, structure and head are published in one
write transaction. Expected-revision admission uses that same transaction.
Aborted allocations roll back; committed historical IDs cannot be reused.
Unchanged block payloads are not rewritten, but commit still reads the current
revision to use the shared domain edit engine.

## Compatibility and evidence

Experimental v1 DBIs under the selected prefix are rejected. There is no
implicit in-place migration: export/rebuild into a fresh namespace is required.
Unknown markers, markerless state, incompatible comparators and corrupt
bindings fail closed.

The focused regression exercises multistep history, render, reopen, concurrent
writers, pre-head abort, no-reuse, selected-write counts and rehashed corruption.
This is not production admission. Fresh-process upgrade/consumer qualification
and measured C1 comparison have separate receipts; an unexecuted gate must not
be reported as PASS. No default backend activation is implied.
