# M0 implementation-gap audit: durable lexical document memory

This short audit compares the M0 requirements in `guides/milestones.md` with
`guides/retrieval-roadmap-coverage.md`. The coverage audit classifies canonical
raw/plain MDBX bodies and the core lexical primitives as implemented. This PR
adds a narrow lexical-first importer and manifest publication path; the full
normative M0 resource and knowledge-unit gates remain incomplete.

| M0 gate | State before this PR | Result of this PR |
| --- | --- | --- |
| Raw resource ingest | Contracts and canonical C1 storage exist; no executable UTF-8 ingest/reopen path | Narrow `.md`/`.txt`/log-compatible UTF-8 text import with stable resource identity, manifest binding, durable intent recovery and reopen |
| Chunk/Note units | Contract only | Not addressed; this slice maps one resource to one lexical chunk without claiming a knowledge-unit store |
| Immutable knowledge-unit identity | Value contracts only | Not addressed |
| Source summaries | Value contracts only | URI and original body are hydrated, but no `SourceRefSummary` persistence is claimed |
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
manifest visible and is completed on reopen. Resource IDs are globally unique
within this manifest profile; scope remains part of lexical isolation.

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
lexical search, metadata-only update, interrupted-publication recovery, reopen
and invalid derived-text provenance. The underlying lexical-memory tests still
cover scope isolation, shared-context visibility, tokenless text and hydration.

## Deliberate boundaries

This PR does not claim that all M0 gates are complete. Multi-chunk
segmentation, `KnowledgeUnitStore`, `SourceRefSummary` persistence, a general
Original projection store, retrieval traces, reindex protocols beyond this
publication intent, recovery/compaction and maintenance are follow-up M0 work.
Dense, hybrid, graph, temporal and other M1/M2 capabilities are explicitly
excluded.

The lexical index is a rebuildable derived view, not a second source of truth.
The durable source of truth is the existing canonical content store. No
background queue, external parser, embedding backend or new public storage
contract is introduced here.
