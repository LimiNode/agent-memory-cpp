# Verified artifact hydration

`MdbxArtifactMaterializer` is the Raw-only first-release bridge from an
`ArtifactBindingId` to retained bytes. It is an integrity adapter, not an
authorizer, retriever, or source-revision allocator.

The request carries the exact `SourceRevisionId`, binding identity, expected
artifact/digest (when the caller has them), and a bounded byte range. A binding
attached to another revision is `Stale`; a missing binding is `Missing`; a
failed CAS digest or forged identity is `Corrupt`; and an unavailable CAS is
`Unavailable` (an unsupported optional backend is `Unsupported`). Bytes are
returned only with `Materialized` and are never included in failure results.
An unfinished receipt is `Missing`, not `SummaryOnly`: this adapter has no
summary evidence and must not imply that one exists.

This API intentionally does not accept caller-provided scope strings or an
optional current revision. It cannot prove authorization or currentness from
those values. An authoritative `Hydrated` path remains a separate future seam
that must consume a genuine snapshot/admission context from the existing MDBX
frontier contracts.

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
