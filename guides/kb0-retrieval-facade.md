# KB-0 Retrieval Facade

**Status:** Experimental, storage-backed conformance slice; M0/KB-0 release
gate remains `HOLD`.

`MdbxKb0RetrievalFacade` is a thin host-facing composition over
`MdbxSnapshotRevisionBoundRetriever`. It does not add another admission
algorithm, DBI, graph index, runtime coordinator or production default. The
existing bounded lexical producer ranks candidates; the existing snapshot
adapter re-reads the unit, `Original` projection and root manifest in one MDBX
read transaction and rejects stale or malformed candidates.

## Result and trace contract

`retrieve_with_trace()` returns the existing `RevisionBoundRetrievalResponse`
and a `RevisionBoundRetrievalTrace`. The trace records:

- a deterministic versioned query identity and SHA-256 digest;
- scope, limit, candidate budget and projection kind;
- candidate/admitted/stale/unavailable counts and completeness;
- per-hit unit revision, root resource generation/digest, freshness and
  materialization state;
- whether final admission used one snapshot (`true`) and whether ranking was
  snapshot-consistent (`false`).

The query identity includes text, limit, embedding scalar bit patterns and a
canonical ordering of metadata filters. It is an evidence key, not an
authorization token.

The current M0 source contract emits `Current + SummaryOnly` only when the
inline `SourceRefSummary` and active root manifest agree. A stale or
unavailable result has no body text. `SummaryOnly` does not claim byte/range
hydration: a future source-backed reader must validate the canonical body,
`TextRange` and digest in the same read view before `Hydrated` can be emitted.

## Qualification boundary

The facade is not a release or activation gate. Import, update and reopen
conformance use the existing `MdbxResourceImporter` and durable stores. Tests
cover exact `A@1` provenance, mutable-unit and resource revision invalidation,
candidate-hook races, custom prefixes, wrong scope, missing/malformed
projection or manifest, legacy previews, unsupported metadata filters and
budget failures. No production retrieval default is changed.
