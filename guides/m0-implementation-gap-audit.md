# M0 implementation-gap audit: durable lexical document memory

This short audit compares the M0 requirements in `guides/milestones.md` with
`guides/retrieval-roadmap-coverage.md`. The coverage audit classifies canonical
raw/plain MDBX bodies, the lexical importer path and the narrow durable Chunk
envelope store as implemented. The full normative M0 resource and knowledge-
unit gates remain incomplete.

| M0 gate | State before this PR | Result of this PR |
| --- | --- | --- |
| Raw resource ingest | Contracts and canonical C1 storage exist; no executable UTF-8 ingest/reopen path | Narrow `.md`/`.txt`/log-compatible UTF-8 text import with stable resource identity, manifest binding, durable intent recovery and reopen |
| Chunk/Note units | Contract only | Narrow durable `Chunk` envelope store with content-key dedupe, reopen, scope-aware keys and mutable updates; multi-kind payload stores remain open |
| Immutable knowledge-unit identity | Value contracts only | `KnowledgeUnitKey` is persisted separately from monotonic `KnowledgeUnitId`; no in-place identity mutation API is exposed |
| Source summaries | Value contracts only | Inline revision-bound `SourceRefSummary` values are persisted (maximum three); full `source_refs` storage remains deferred |
| Original projection | Contract only | Rebuildable BM25 view only; no general `IProjectionStore` implementation |
| Lexical retrieval | In-memory tokenizer/index primitives exist; coverage says roadmap only for the product path | Deterministic BM25 product path over durable canonical bodies, exercised through the importer |
| Scope isolation | Contract and some storage slices exist | Covered for ingest, reopen and search in this profile |
| Retrieval trace | Missing product path | Not addressed |

## Narrow gap selected for the first implementation PR

The smallest useful production surface is one vertical slice:

`scope/resource -> durable canonical UTF-8 body -> deterministic BM25 index -> reopen -> search`

The new `IResourceImporter`/`MdbxResourceImporter` surface composes the
existing durable lexical memory with `MdbxResourceManifestStorage`. It accepts
one host-observed UTF-8 resource, records a SHA-256 body digest and text-origin
provenance, publishes document/chunk-shaped manifest records, and exposes
manifest lookup plus lexical search. A durable import intent makes the
lexical-first publication recoverable: an interruption leaves the previous
manifest visible and is completed on reopen or by a later importer after the
lease expires. Resource IDs are durably bound to
lease-expired recovery first completes the durable pending payload before a
new observed payload for the same ResourceId is claimed. Resource IDs are
durably bound to one scope within this manifest profile; reusing an ID from
another scope fails
closed. Search hides resources with a pending import intent, so an
uncommitted lexical publication is not visible through the importer. Intent
acquisition and release use compare-and-check transactions. Canonical, lexical,
and manifest publication each recheck the exact fencing intent in their own
write transaction, so a stale executor cannot advance the active canonical
head after losing ownership. Same-body
pipeline/provenance changes are rejected until an independent
projection-generation contract exists.

Normal upserts replace only the affected in-memory lexical record. Full scope
rebuild remains a reopen and stale-reader refresh strategy for this first
profile. When another instance publishes in the same scope during an upsert,
the writer detects the intervening scope generation and performs a complete
scope refresh before returning. A stale-reader refresh likewise scans the
catalog and rebuilds the affected scope. This cross-instance scan/rebuild is a
temporary cost of the narrow M0 profile; incremental deltas or a change log are
performance-gated follow-up work, not a generic synchronization framework,
background queue, or replication system in this PR. Text with no lexical
tokens remains durable but is omitted from BM25.

The checked importer example and focused CTest cover ingest, manifest binding,
lexical search, metadata-only update, interrupted-publication recovery, reopen,
all five fault points, pending-visibility filtering, scope binding, SHA-256,
UTF-8 validation and derived-text provenance. The underlying lexical-memory
tests still cover scope isolation, shared-context visibility, tokenless text
and hydration.

## Deliberate boundaries

This PR does not claim that all M0 gates are complete. Multi-chunk
segmentation, `KnowledgeUnitStore`, `SourceRefSummary` persistence, a general
Original projection store, retrieval traces, reindex protocols beyond this
publication intent, recovery/compaction and maintenance are follow-up M0 work.
Dense, hybrid, graph, temporal and other M1/M2 capabilities are explicitly
excluded.

The lexical index is a rebuildable derived view, not a second source of truth.
The durable source of truth for imported text remains the existing canonical
content store. This slice adds only the narrow envelope-store contract; it does
not add a background queue, external parser, embedding backend or general
projection contract.

## Implemented M0 store slice

The next M0 implementation slice adds `IKnowledgeUnitStore` with an MDBX
profile for durable `Chunk` envelopes. `create_or_get_unit` allocates a
monotonic `KnowledgeUnitId` and deduplicates by the immutable tuple
`(KnowledgeUnitKind, ScopeId, ContentHash)`. The envelope and inline
revision-bound `SourceRefSummary` values survive close/reopen. Mutable display,
source, lifecycle, observation and priority fields are updated transactionally;
identity fields are not updateable. Full source references, supersedence
lineage, projections and retrieval traces remain separate M0 follow-up gates.
