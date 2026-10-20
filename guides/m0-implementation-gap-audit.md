# M0 implementation-gap audit: durable lexical document memory

This short audit compares the M0 requirements in `guides/milestones.md` with
`guides/retrieval-roadmap-coverage.md`. The coverage audit classifies canonical
raw/plain MDBX bodies, the lexical importer path and the narrow durable Chunk
envelope store as implemented. The full normative M0 resource and knowledge-
unit gates remain incomplete.

| M0 gate | State before this PR | Result of this PR |
| --- | --- | --- |
| Raw resource ingest | Contracts and canonical C1 storage exist; no executable UTF-8 ingest/reopen path | Narrow `.md`/`.txt`/log-compatible UTF-8 text import with stable resource identity, manifest binding, durable intent recovery and reopen |
| Chunk/Note units | Contract only | Narrow durable `Chunk` envelope store with content-key dedupe, reopen, scope-aware keys and mutable updates; the importer can optionally publish UTF-8-safe revision-bound raw Chunk units; multi-kind payload stores remain open |
| Immutable knowledge-unit identity | Value contracts only | `KnowledgeUnitKey` is persisted separately from monotonic `KnowledgeUnitId`; no in-place identity mutation API is exposed |
| Source summaries | Value contracts only | Inline revision-bound `SourceRefSummary` values are persisted; the M0 store admits exactly one such source per Chunk (the general envelope budget is at most three); full `source_refs` storage remains deferred |
| Original projection | Implemented (revision-aware M0 publisher/store) | Bounded envelope-seed projection is durable and reopen-tested; importer wiring, full ChunkPayload hydration, projection-aware BM25 postings and lifecycle reclamation remain open |
| Lexical retrieval | In-memory tokenizer/index primitives exist; coverage says roadmap only for the product path | Deterministic BM25 product path over durable canonical bodies, exercised through the importer |
| Scope isolation | Contract and some storage slices exist | Covered for ingest, reopen and search in this profile |
| Retrieval trace | Missing product path | Not addressed |

## Narrow gap selected for the first implementation PR

The smallest useful production surface is one vertical slice:

`scope/resource -> durable canonical UTF-8 body -> deterministic BM25 index -> reopen -> search`

The `IResourceImporter`/`MdbxResourceImporter` surface composes the existing
durable lexical memory with `MdbxResourceManifestStorage`. It accepts one
host-observed UTF-8 resource, records a SHA-256 body digest and text-origin
provenance, publishes document/chunk-shaped manifest records, and exposes
manifest lookup plus lexical search. When wired to an `IKnowledgeUnitStore`, it
also splits the body only at UTF-8 code-point boundaries (1024-byte maximum),
creates revision-bound raw Chunk envelopes before manifest activation, returns
their ids, and records `Custom` manifest bindings named
`knowledge_unit:<numeric-id>`. Replays deduplicate by the recipe-v2 identity;
the lexical-only profile remains unchanged. A durable import intent makes the
lexical-first publication recoverable: an interruption leaves the previous
manifest visible and is completed on reopen or by a later importer after the
lease expires. Resource IDs are durably bound to one scope within this manifest
profile. Lease-expired recovery first completes the durable pending payload
before a new observed payload for the same ResourceId is claimed; reusing an ID
from another scope fails closed. Search hides resources with a pending import
intent, so an
uncommitted lexical publication is not visible through the importer. Intent
acquisition and release use compare-and-check transactions. Canonical, lexical,
and manifest publication each recheck the exact fencing intent in their own
write transaction, so a stale executor cannot advance the active canonical
head after losing ownership. Same-body
pipeline/provenance changes are rejected until an independent
projection-generation contract exists.

The importer has two explicit durable publication profiles: `LexicalOnly` and
`LexicalWithRawChunks`. The profile is encoded in v4 intents and in the
manifest schema id; v3 intents are read as the historical lexical-only
profile. Raw manifests include the sanitized table namespace in their schema
identity. A raw importer owns the `MdbxKnowledgeUnitStore` constructed from the
same MDBX context and table prefix, so an arbitrary external store cannot be
attached. Profile changes for an existing resource or pending intent fail
closed and require an explicit migration. Raw admission is checked before an
intent is written (16 MiB and 16,384 chunks by default; both are configurable
within the uint32 manifest-ordinal bound), and is rechecked by a recovery
executor before any durable rewrite so a tighter recovery limit fails closed.

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
all six fault points, pending-visibility filtering, scope binding, SHA-256,
UTF-8 validation and derived-text provenance. The underlying lexical-memory
tests still cover scope isolation, shared-context visibility, tokenless text
and hydration.

## Deliberate boundaries

This PR does not claim that all M0 gates are complete. Locator-backed
`ChunkPayload` hydration, full `source_refs`, a
projection-to-importer orchestration, full-body Original hydration, retrieval traces, lineage orchestration,
complete MemoryStack profile integration, reindex protocols beyond this
publication intent, general recovery orchestration, compaction and maintenance
are follow-up M0 work. Interrupted-publication recovery for this narrow slice
is implemented and covered by its importer tests. Raw units created before a
manifest commit are durable staging history: they are not listed in a
manifest and must not be treated as published by a retrieval path. Replaced generations retain
their old raw units until a later manifest-aware reclaim/compaction pass; the
current slice never erases provenance eagerly.
Dense, hybrid, graph, temporal and other M1/M2 capabilities are explicitly
excluded.

The lexical index is a rebuildable derived view, not a second source of truth.
The durable source of truth for imported text remains the existing canonical
content store. This slice adds only the narrow envelope-store contract; it does
not add a background queue, external parser, embedding backend or general
projection contract.

## Implemented M0 store slice

The narrow M0 implementation slice adds `IKnowledgeUnitStore` with an MDBX
profile for durable `Chunk` envelopes. `create_or_get_unit` allocates a
monotonic `KnowledgeUnitId` and deduplicates by the immutable tuple
`(KnowledgeUnitKind, ScopeId, ContentHash)`. The envelope and inline
revision-bound `SourceRefSummary` values survive close/reopen. Mutable display,
source, lifecycle, observation and priority fields are updated transactionally;
identity fields are not updateable. The M0 admission requires exactly one
revision-bound source summary. Full source references, semantic multi-chunk
segmentation, locator-backed payload hydration, supersedence lineage,
projections and retrieval traces remain separate M0 follow-up gates.
