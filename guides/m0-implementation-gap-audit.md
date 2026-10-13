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
form the deliberately small public surface. It keeps a compact catalog of
scope, resource, document and URI metadata in MDBX, stores one canonical
`body` block through `MdbxCanonicalContentStore`, and rebuilds an in-memory
`ExactLexicalIndex` when opened. Updates retain the logical resource identifier
and expose a one-based body generation. Search results hydrate the URI and
current text.

The checked example and focused CTest cover ingest, scope isolation, lexical
search, reopen, update, hydration and invalid-input handling.

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
