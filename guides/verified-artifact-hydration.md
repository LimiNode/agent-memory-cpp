# Verified artifact hydration

`MdbxVerifiedArtifactHydration` is the Raw-only first-release bridge from an
`ArtifactBindingId` to retained bytes. It is an infrastructure adapter, not a
retriever and not a source-revision allocator.

The request carries the exact `SourceRevisionId`, binding identity, expected
artifact/digest (when the caller has them), a bounded byte range, and the
scope admission result. The adapter rejects an empty or mismatched scope
before reading the catalog. A binding attached to another revision is
`Stale`; a missing binding is `Missing`; a failed CAS digest or forged
identity is `Corrupt`; and an unavailable CAS is `Unavailable` (an unsupported
optional backend is `Unsupported`).
Bytes are returned only with `Hydrated` and are never included in failure
results. `SummaryOnly` is used only when a catalog record is known but its
retention receipt does not authorize materialization.

The catalog and file CAS are separate durability domains. The adapter does not
claim an atomic MDBX-plus-filesystem snapshot: `MdbxArtifactCatalog` first
checks the binding and its receipt, while `FileRawBlobStore::read_range()`
re-reads and hashes the complete file before returning the requested range.
Therefore a concurrent replacement or deletion fails closed as `Corrupt`,
`Missing`, or `Unavailable`; it must not silently substitute bytes from a
newer revision. Raw is the initial path. Framed Zstd and other codecs remain
separate optional adapters.

This slice deliberately does not activate production retrieval, perform
destructive GC, or infer a scope from `ResourceId` or `DocumentId`.
