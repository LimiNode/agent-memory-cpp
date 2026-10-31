# B3 Current-Read Revalidation

The B2 fenced publisher makes a derived publication durable, but its active
marker is a storage fact rather than an authoritative freshness claim. B3 adds
an opt-in reader that checks the marker, publication manifest, KnowledgeUnits,
and root ResourceManifests in one MDBX read transaction.

## Status contract

`MdbxCurrentDerivedPublicationReader::read_current()` returns:

- `Current` when every stored unit revision, source generation/digest, and
  active root manifest agrees with the publication bindings;
- `Stale` when a retained unit or root exists but its lifecycle or revision no
  longer matches;
- `Unavailable` when a required canonical row is missing, malformed, inactive
  in a way that prevents proof, or fails integrity validation;
- `NotFound` when none of the three publication rows exists.

The body is never returned for `Stale` or `Unavailable`. The separate
`read_historical()` path returns `Historical` only after publication body,
manifest, and active-marker integrity checks. It does not claim current
freshness and never substitutes a newer revision.

## Snapshot and bounds

Publication rows and canonical dependencies are read through the same MDBX
read transaction. The reader performs one bounded lookup per input unit and its
root resource; it does not scan the publication table and does not start a
background invalidation worker. Existing B1/B2 input and serialized payload
limits remain in force.

The adapter is storage infrastructure only. It does not add a coordinator,
graph backend, production search route, or automatic fallback to stale body
bytes. Query-time policy and retrieval activation remain separate work.

## Evidence boundary

The conformance tests cover current, stale-root, historical, missing-publication
and integrity paths. They do not yet prove a production retrieval policy,
historical retention guarantees, or cross-process scheduling semantics.
