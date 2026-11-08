# Article-to-citation vertical slice

The article vertical is an executable qualification path for the text-first
KB-0 boundary:

```text
normalized Markdown
  -> retained Raw CAS + ArtifactCatalog
  -> StructuralSegmentSet
  -> Original projection publication
  -> snapshot-consistent lexical admission
  -> exact source-range materialization
```

The path is covered by
`agent_memory_mdbx_article_to_citation_vertical_test`. It is an experimental
conformance slice and does not activate production retrieval.

## Typed revision join

Lexical envelopes carry `ResourceRevisionRef` (`ResourceId`, generation and
body digest), while the artifact catalog deliberately owns
`SourceRevisionId`/`ArtifactBindingId`. The two domains are not interchangeable.
`ResourceArtifactBinding` is the narrow persisted join between them:

- the MDBX key contains the complete resource revision, including its digest;
- the value contains the typed source-revision and artifact-binding IDs;
- replaying the same mapping is idempotent;
- attempting to attach another artifact to the same exact revision fails closed;
- a later generation has a distinct key and cannot overwrite historical
  provenance.

The join is not a second source of truth. Resource manifests remain the
authority for active resource publication; `MdbxArtifactCatalog` remains the
authority for artifact identity, retention receipt and Raw CAS bytes. The join
only resolves a retrieved resource revision to the catalog binding needed for
verified materialization.

## Freshness and hydration

`MdbxSnapshotRevisionBoundRetriever` admits a lexical candidate only when the
unit, Original projection and active resource manifest agree in one MDBX read
snapshot. A saved candidate from `A@1` becomes `Stale` after `A@2` is
published; its text is empty and it is never re-filled from the new revision.

After admission, the join resolves the exact artifact binding and
`MdbxArtifactMaterializer` verifies the catalog record, digest and requested
range. Missing or removed CAS bytes return `Missing`/`Corrupt`; no newer body is
substituted.

This slice intentionally leaves the following separate: atomic coordination
between resource-manifest and artifact-catalog publication, compact scorer
adapters, Zstd catalog integration, multimodal extraction and production
retrieval activation.
