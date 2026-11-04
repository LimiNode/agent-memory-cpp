# MDBX snapshot-consistent final admission

`MdbxSnapshotRevisionBoundRetriever` is the MDBX-specific conformance slice
for the KB-0 revision-bound retrieval contract. It is intentionally additive
and is not the production retrieval default.

## Boundary

Candidate production and ranking use the existing bounded
`ProjectionAwareLexicalRetriever`. After that producer-owned candidate window
has been formed, final admission opens one caller-owned MDBX read transaction
and reads the following values from that same snapshot:

1. the `KnowledgeUnitEnvelope`;
2. the revision-matching `Original` `SearchProjection`;
3. the active root `ResourceManifest` and its ordered publication bindings.

The adapter returns `Current` only when all three values match the candidate's
scope, unit revision, projection identity, source revision and manifest
schema. A candidate that does not match is returned as `Stale` or
`Unavailable` with no text body. Historical data is never substituted for a
current request.

The `MdbxProjectionStore::list_in_transaction()` overload is deliberately
narrow: it borrows the transaction and does not coordinate, commit or own any
other adapter. `MdbxKnowledgeUnitStore::find_in_transaction()` and
`MdbxResourceManifestStorage::find_manifest_in_transaction()` follow the same
caller-owned lifetime rule.

## Guarantees and non-goals

- A writer that commits after the read transaction starts cannot create a
  torn unit/projection/manifest admission; the reader observes either the
  prior complete snapshot or, on the next read, the new state.
- Reopen, scope isolation, missing units/roots, stale revisions and malformed
  projection identities fail closed in the focused MDBX conformance test.
- `SummaryOnly` remains the only materialization level. This slice does not
  read retained source bytes and does not claim byte hydration.
- Ranking and candidate production are not snapshot-consistent. The slice
  does not add a generic transaction coordinator, a graph backend, a new DBI,
  or production retrieval activation.

Callers that need a single-candidate check may use `admit_candidate()`. The
query entry point keeps the existing bounded query and unsupported-feature
semantics while applying the shared final snapshot to every emitted candidate.
