# KB-0 Revision-Bound Retrieval Slice

This slice is a small product-facing adapter over the existing bounded
`ProjectionAwareLexicalRetriever`. It preserves the legacy `RetrievedChunk`
contract and adds an explicit result carrying the opaque `KnowledgeUnitId`,
scope, `Original` projection kind, unit revision, root resource revision,
typed `SourceRefSummary`, score and a hydration outcome.

## Admission rules

The adapter accepts a lexical hit only when the producer supplied an explicit
unit identity and the canonical stores still agree on:

- scope and active unit lifecycle;
- unit revision and an active `Original` projection with matching body;
- one valid `RevisionBoundQuote` source summary;
- active root manifest resource identity, generation and SHA-256 digest.

No `ChunkId` is interpreted as a `KnowledgeUnitId`, and no numeric identity is
reconstructed from a legacy chunk string. A stale or unavailable candidate is
never returned with canonical unit text.

`Partial` means that the revision-bound source summary was verified but M0
only stores an inline preview. It is not proof that the complete canonical
source bytes were hydrated. A future byte-addressable source adapter may
upgrade this outcome to a fully hydrated state without changing result
identity.

## Consistency boundary

The generic adapter composes the existing storage contracts and therefore
performs its validation through those contracts. It does not claim an MDBX
transaction snapshot. An MDBX product path must provide one shared read
transaction spanning candidate, unit, projection and manifest reads before it
can advertise snapshot-consistent hydration. The missing host-facing snapshot
composition is intentional and remains a follow-up; this slice does not add a
universal coordinator or change production retrieval defaults.

The conformance fixture covers current admission, stale manifest rejection and
scope isolation. Full import/update/reindex/reopen composition and interrupted
publication remain host-composed integration work.
