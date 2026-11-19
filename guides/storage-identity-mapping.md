# Opt-in Numeric Storage Identities

`DocumentId` and `ContentBlockId` are public, textual domain identities.  The
optional `DocumentStorageId` and `BlockStorageId` values in
`storage/StorageIdentityMapping.hpp` are a separate physical-key optimization;
they do not replace, derive, or reinterpret the public identifiers.

`StorageIdentityMapping` is a backend-neutral state machine.  A caller opts in
explicitly, allocates a mapping, and persists `StorageIdentityRecord` values
using the versioned AMSI v1 codec.  The library does not wire this map into
canonical content, MDBX, indexing, or retrieval defaults.  A future backend
adapter may store the records transactionally and restore them on reopen.

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
