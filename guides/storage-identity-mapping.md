# Opt-in Numeric Storage Identities

`DocumentId` and `ContentBlockId` are public, textual domain identities.  The
optional `DocumentStorageId` and `BlockStorageId` values in
`storage/StorageIdentityMapping.hpp` are a separate physical-key optimization;
they do not replace, derive, or reinterpret the public identifiers.

`StorageIdentityMapping` is a backend-neutral state machine.  A caller opts in
explicitly, allocates a mapping, and persists `StorageIdentityRecord` values
using the versioned AMSI v1 codec. The library does not wire this map into
canonical content, indexing or retrieval defaults. The optional
`MdbxStorageIdentityMapping` adapter stores the records transactionally and
restores them on reopen without changing production defaults.

## MDBX initial import and transaction provenance

The opt-in `MdbxStorageIdentityMapping` adapter persists AMSI v1 records and
independent sequence frontiers. Its `load_records()` is an **initial import**,
not a live replacement: all forward/reverse tables must be empty and neither
sequence frontier may have been established. Every replacement of a used
namespace is rejected, including identical, partial, empty/deletion and retired
snapshots. Preserving high-water values alone cannot prevent an old numeric ID
from being rebound to another logical identity. An explicitly remapped snapshot
may instead be imported into a fresh empty namespace; its active and retired
assignments must be unique and parent-consistent. Validation and publication
are atomic; rejected imports do not change historical bindings or frontiers.
The backend-neutral in-memory loader remains a snapshot reconstruction utility
and does not establish permission to replace a live MDBX namespace.

All transaction-scoped mapping reads and writes require an active transaction
from the mapping context's connection/environment; writes also require writable
mode. Foreign, inactive and blocked transactions are rejected before any DBI
access, even for empty lookup identities. The caller owns commit/rollback and
keeps the transaction on its owning thread. Read methods retain access to retired
mappings for exact historical references.
Transaction-scoped writes rely on MDBX's writer serialization rather than taking
the instance mutex after a caller-owned writer. This avoids inversion against
ordinary operations that hold that mutex while acquiring their own writer.

Allocation admission uses MDBX's table-statistics count, not an O(N) record scan
or a second persisted counter. Point mapping operations retain B-tree lookup
cost. Snapshots check the count against `max_records` before allocating or
decoding and traverse at most that many records, then sort in O(N log N).
Document retirement applies the same hard count check before collecting its
bounded block range. Snapshots also reject mismatched physical/logical keys.
This refinement changes neither the AMSI codec nor the four MDBX DBI schemas.

Numeric document and block sequences are independent, start at one, and never
reuse an assigned value.  Retiring a document also retires its block records;
the records remain in the durable snapshot as tombstones.  Reusing a retired
public string identity is rejected, which prevents a historical physical key
from silently changing meaning.

The codec is binary-safe and fail-closed.  It checks the magic, version, kind,
flags, fixed-width numeric id, bounded length-prefixed identifiers, and exact
record length before constructing decoded strings.  Unknown versions,
truncation, overflow-sized fields, invalid flags, and trailing bytes are
corruption rather than a missing mapping.
