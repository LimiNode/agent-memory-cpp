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

The three status axes are the sole contract; the unreleased single-axis
`RevisionBoundHydrationOutcome` and `hydration` field have been removed.
Returned values obey these invariants (the public DTO is a value type, not an
admission authority for caller-constructed data):

| Freshness | Materialization | `text` | `source` / `root_digest` |
|---|---|---|---|
| `Current` | `SummaryOnly` | Canonical unit text | Both required; exact source revision and active manifest digest checked |
| `Current` | `Unavailable` | Empty | Both absent; unit/projection admitted but the supported single-source summary is unavailable |
| `Stale` | `Unavailable` | Empty | Both absent, or both present as validated stored diagnostic provenance; any retained pair names the old source revision, not a current citation |
| `Unavailable` | `Unavailable` | Empty | Both absent; no admitted identity/provenance |

`Hydrated` is reserved and is never emitted by this generic adapter. No other
combination is returned. A present provenance pair also requires matching
`root_resource_id` and `root_generation`; absent provenance leaves these empty
and zero. Full citation bytes are never implied by `SummaryOnly`. The Stale
diagnostic case validates the stored summary but deliberately does not claim
that its root still matches the active manifest.

### Query budgets and candidate pairing

For supported queries, `limit > max_candidates` returns `BudgetExceeded`
before tokenization, scope scanning or store reads, even if the corpus might
contain fewer matches. Unit-scan and indexed-projection-byte budget failures
have the same outcome. `retrieve_detailed()` returns no partial results and a
diagnostic; `retrieve()` throws `ProjectionAwareLexicalRetrievalBudgetExceeded`
with that diagnostic. Limits from zero through the cap remain accepted.
Unsupported filters take precedence and retain their explicit diagnostic.
The legacy producer's capped/empty-result behavior is unchanged.

`ProjectionAwareLexicalRetrieval` carries one typed candidate per legacy hit in
the same rank/score order. The producer constructs both channels together; an
allocation failure throws rather than returning a half-filled result. The
adapter calls `validate_candidate_pairing()` before indexing the channels.
Unequal sizes throw `std::logic_error` from both adapter entry points, with no
results returned. Negative fixtures exercise both directions of size mismatch.

`Complete` means the supported bounded candidate operation completed without a
budget failure. It does not imply source-byte hydration, a coherent cross-store
snapshot, or top-K refill when final admission rejects a candidate.

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
