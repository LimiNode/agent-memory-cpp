# Installed MDBX/Raw CAS consumer smoke

This experiment exercises the installed `agent_memory` package from an
external C++17 consumer. It is an integration smoke test, not a release gate
for retrieval quality and not a claim that the package provides hydrated
knowledge units.

The consumer opens an installed MDBX `MdbxArtifactCatalog` backed by a private
`FileRawBlobStore`, then verifies:

- publication of normalized UTF-8 body bytes and an artifact binding;
- idempotent duplicate ingest and rejection of a conflicting duplicate;
- bounded full-body materialization and a successful bounded range read;
- MDBX/CAS reopen visibility;
- SHA-256 verification rejecting a deliberately tampered CAS body.

The existing `cmake packaging` workflow builds the package, installs its MDBX
dependencies, and runs this consumer as the `MDBX installed consumer` job.
The test intentionally stops at Raw artifact/catalog semantics. It does not
publish a lexical projection, perform retrieval, or report `Hydrated` without
an actual source materializer.
