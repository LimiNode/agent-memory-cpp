# KB-0 Revision-Bound Retrieval Slice

This slice is a small product-facing adapter over the existing bounded
`ProjectionAwareLexicalRetriever`. It preserves the legacy `RetrievedChunk`
contract and adds an explicit result carrying the opaque `KnowledgeUnitId`,
scope, `Original` projection kind, unit revision, root resource revision,
typed `SourceRefSummary`, score and independent freshness/materialization
outcomes. `RevisionBoundRetrievalResponse` also reports whether the query was
complete, budget-limited, or outside the supported M0 profile.

## Admission rules

The producer supplies a typed candidate identity. Legacy string metadata is
retained only for compatibility and is not parsed by the revision-bound
adapter. The adapter accepts a lexical hit only when that identity is valid and
the canonical stores still agree on:

- scope and active unit lifecycle;
- unit revision and an active `Original` projection with matching body;
- one valid `RevisionBoundQuote` source summary;
- active root manifest resource identity, generation and SHA-256 digest.

No `ChunkId` is interpreted as a `KnowledgeUnitId`, and no numeric identity is
reconstructed from a legacy chunk string. A stale or unavailable candidate is
never returned with canonical unit text.

Freshness, materialization, and query completeness are deliberately separate:

- `Current` means the active unit and its published Original projection still
  match the candidate revision and manifest binding.
- `SummaryOnly` means the revision-bound source summary was verified, while M0
  stores only an inline preview. It is not proof that complete canonical source
  bytes were hydrated. `Hydrated` is reserved for a future byte-addressable
  source adapter.
- `Complete` describes the query operation, not the citation strength of one
  result. Unsupported metadata filters return an explicit
  `UnsupportedQuery` diagnostic (and `retrieve()` throws
  `RevisionBoundUnsupportedQuery`) instead of silently looking like no matches.

Canonical unit text is returned only for a `Current` result. A source summary
is returned only after its revision and active manifest digest are verified;
full citation bytes are never implied by `Partial`/`SummaryOnly`.

The shared `has_current_original_projection()` seam is also used by the
adapter and the lexical producer. It preserves manifest schema/bindings,
derivation fingerprint, lifecycle, generation and body checks in one admission
algorithm.

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
