# Projection-aware lexical retrieval

The M0 `ProjectionAwareLexicalRetriever` is a bounded correctness slice over
the durable `Original` projection profile. It is intentionally an execution
adapter, not a second storage engine or a persistent lexical index.

For each query it reads units in one scope, admits only an `Original` projection
whose `unit_revision_at_build` and `projection_revision` equal the current
envelope revision, and requires the active resource manifest to contain the
exact `original_projection:<unit_id>` custom binding. The resource generation
and body digest in the source summary must match the manifest. Historical
projection rows therefore cannot become visible merely because they are the
latest value returned by `IProjectionStore::list()`.

The lexical index is rebuilt in memory for the bounded query. The profile has
explicit `max_scanned_units` and `max_indexed_projection_bytes` admission
budgets in addition to `max_candidates`. Exceeding either admission budget
throws `ProjectionAwareLexicalRetrievalBudgetExceeded`; a partial scan is not
reported as a complete top-K result. Within the budgets, at most
`max_candidates` scored hits are retained, and each survivor is re-read from
the canonical knowledge-unit store and revalidated against the publication
frontier before hydration. Manifest rows are cached by `ResourceId` during the
indexing phase, then reused for final validation within the same retrieval
snapshot. Revision changes,
erased units, unpublished manifests, and stale projections are fail-closed
misses.

This first slice accepts text queries without metadata filters and hydrates the
canonical envelope `primary_text`. Its `RetrievedChunk` uses the legacy
synthetic `knowledge-unit:<id>` identity and a zero-based full-text range; it is
not a revision-bound citation. SourceRef/EvidenceAnchor hydration remains a
follow-up capability. Metadata filtering, locator-backed body hydration,
persistent postings, and batched candidate readers remain follow-up work. No
MDBX DBI or canonical identity format is changed.
