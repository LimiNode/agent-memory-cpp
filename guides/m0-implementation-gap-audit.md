# M0 implementation-gap audit: durable lexical document memory

This short audit compares the M0 requirements in `guides/milestones.md` with
`guides/retrieval-roadmap-coverage.md`. The coverage audit classifies canonical
raw/plain MDBX bodies and the core lexical primitives as implemented, while
durable importing, a resource catalog and a rebuildable lexical projection
remain incomplete.

| M0 gate | State before this PR | Result of this PR |
| --- | --- | --- |
| Raw resource ingest | Contracts and canonical C1 storage exist; no executable UTF-8 ingest/reopen path | Narrow `.md`/`.txt`/log-compatible UTF-8 text upsert with stable resource identity and reopen |
| Chunk/Note units | Contract only | Not addressed; this slice maps one resource to one lexical chunk without claiming a knowledge-unit store |
| Immutable knowledge-unit identity | Value contracts only | Not addressed |
| Source summaries | Value contracts only | URI and original body are hydrated, but no `SourceRefSummary` persistence is claimed |
| Original projection | Contract only | Rebuildable BM25 view only; no general `IProjectionStore` implementation |
| Lexical retrieval | In-memory tokenizer/index primitives exist; coverage says roadmap only for the product path | Deterministic BM25 product path over durable canonical bodies |
| Scope isolation | Contract and some storage slices exist | Covered for ingest, reopen and search in this profile |
| Retrieval trace | Missing product path | Not addressed |

## Narrow gap selected for the first implementation PR

The smallest useful production surface is one vertical slice:

`scope/resource -> durable canonical UTF-8 body -> deterministic BM25 index -> reopen -> search`

`MdbxDurableLexicalDocumentMemory` is the only new production API needed for
this first slice. Its options, `upsert_text`, `search` and hydrated hit value
form the deliberately small public surface. It keeps independent per-resource
catalog records in MDBX, stores one canonical `body` block through
`MdbxCanonicalContentStore`, and rebuilds an in-memory `ExactLexicalIndex` when
opened. A durable intent makes publication recoverable across the canonical
and catalog transactions, while the published record binds URI and search to
one exact canonical revision. Updates retain the logical resource identifier
and expose a one-based body generation. Search results hydrate that published
URI and revision-bound text.

Normal upserts replace only the affected in-memory lexical record. Full scope
rebuild remains a reopen and stale-reader refresh strategy for this first
profile. Text with no lexical tokens remains durable but is omitted from BM25.

The checked example and focused CTest cover ingest, scope isolation, lexical
search, targeted update, shared-context visibility, interrupted-publication
recovery, tokenless text, reopen, hydration and invalid-input handling.

## Deliberate boundaries

This PR does not claim that all M0 gates are complete. Resource manifests and
reindex protocols remain contract work; importer interfaces, multi-chunk
segmentation, knowledge-unit persistence, projections, recovery/compaction and
maintenance are follow-up M0 work. Dense, hybrid, graph, temporal and other
M1/M2 capabilities are explicitly excluded.

The lexical index is a rebuildable derived view, not a second source of truth.
The durable source of truth is the existing canonical content store. No
background queue, external parser, embedding backend or new public storage
contract is introduced here.
