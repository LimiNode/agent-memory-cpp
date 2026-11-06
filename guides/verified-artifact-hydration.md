# Verified artifact hydration

`MdbxVerifiedArtifactHydration` is the Raw first-release bridge from an
`MdbxArtifactCatalog` binding to citation bytes. It is deliberately a narrow
adapter: it does not publish artifacts, select revisions, or activate
production retrieval.

## Admission contract

Hydration checks the caller's exact `SourceRevisionId` and
`ArtifactBindingId`, the caller-provided scope agreement, optional current
revision, and optional expected `ArtifactId`/digest before reading any body.
`Hydrated` is returned only after the catalog reports a finalized materialized
body and the Raw CAS verifies the complete content-addressed byte stream while
serving the requested bounded range. A newer revision is never substituted for
the requested historical revision.

`SummaryOnly` means the catalog binding is known but its receipt is not
finalized or its body is not materialized. `Missing`, `Corrupt`, `Stale`,
`WrongScope`, `Unavailable`, and `InvalidArgument` are failure results and
never contain bytes.

## MDBX/CAS race boundary

MDBX metadata and the file CAS are separate durability domains. A hydration
call does not claim an atomic snapshot spanning both. The catalog read first
loads the binding; the CAS read then streams and hashes the immutable artifact
path before returning the requested range. Content-addressed publication does
not overwrite an existing path. If a body is removed, replaced by an invalid
file, or an I/O error occurs between those operations, the call fails closed as
`Missing`, `Corrupt`, or `Unavailable`; it never exposes unverified bytes.

Reopening the catalog replays its durable ingest intent/receipt recovery. This
preserves exact historical bindings, but it does not turn the filesystem and
MDBX operations into one transaction. Deployments requiring stronger
cross-domain durability must add an external publication protocol.

Raw is the only supported physical representation in this slice. Zstd and
other codecs must preserve the same decoded `ArtifactId` and are separate
adapters.
