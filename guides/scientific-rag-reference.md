# Scientific RAG Reference Workload

Status: `Reference application and evaluation lane`; this is not a core
scientific-ingestion dependency.

Scientific corpora are a useful realistic workload for the generic ingestion,
lexical, vector, graph, provenance, and context-building primitives. The
reference application should remain in examples/tools or a separate research
workspace so the C++ core stays domain-independent.

## Canonical ingestion pipeline

```text
source manifest and fetch metadata
  -> structured HTML/LaTeXML when available
  -> PDF fallback
  -> OCR only for scanned/unextractable documents
  -> canonical normalized text with formulas preserved as LaTeX
  -> tokenizer-aware structural chunks
  -> lexical, vector, ANN, and graph projections
```

The canonical source keeps a stable provider identity (for example an arXiv
identifier), source URL/version, retrieval timestamp, content digest,
normalization revision, and license/attribution metadata. HTML/LaTeXML is
preferred over PDF extraction because it preserves headings, references, and
formula structure. PDF and OCR outputs are recorded as fallbacks, not silently
substituted canonical content.

Ingestion is idempotent. Unchanged resources are skipped by a cryptographic
content hash, while changed source revisions append a new canonical revision
and create successor derived projections. Raw source, normalized canonical
text, and every derived index have separate lifecycle and provenance records.

## Canonical data versus projections

Store the canonical normalized document once. Derive, with explicit manifests:

```text
lexical postings / BM25 statistics
FP32 or compressed vectors
ANN/THQ/codec payloads
citation and concept graph edges
```

Duplicating full text into multiple FTS tables or vector databases is an
explicit storage trade-off that must be measured, not the default architecture.
Projection manifests bind source revision, chunker/tokenizer identity, model
identity, projection schema, and build digest. A stale or mismatched projection
fails closed and is rebuilt from canonical data.

## Retrieval and context reference flow

```text
lexical BM25 + vector ANN
  -> explicit fusion (RRF or measured alternative)
  -> rerank
  -> citation/metadata expansion
  -> deduplication and source diversity
  -> token-budget ContextPack
```

The baseline comparison is SQLite/FTS plus exact FP32 retrieval. The compact
ANN path is compared on the same corpus, chunks, qrels, query order, and
hardware. Report corpus/index bytes, ingestion/build throughput, query
p50/p95/p99, Recall@K, nDCG@10, MRR, context precision/coverage, and provenance
coverage. Advertised scores from external projects are not acceptance gates.

Chunking changes the judgment universe. A controlled comparison may keep one
common tokenizer-bounded chunk set, while a best-native comparison must use
canonical document/resource qrels or publish a deterministic judgment-to-chunk
projection receipt for each chunker. Chunk-level qrels from one chunking policy
must not be silently reused for another.

## Scientific graph (deferred)

A first graph projection can use reliable structural signals:

```text
Paper -cites-> Paper
Paper -authored_by-> Author
Paper -category-> Topic
Paper -mentions-> Concept
Paper -uses-> Method
Paper -evaluates_on-> Dataset
```

Citation metadata and explicit links are suitable initial edges. LLM-extracted
theorems, propositions, or semantic relations are uncertain derived evidence
and must carry extractor/model provenance; automatic theorem extraction is not
promised by this reference lane.

## External references and boundaries

`ShmidtS/RAG` is a reference ingestion/use-case shape, not a dependency or a
claim that its public corpus is a complete arXiv mirror. Its public project
currently states `All rights reserved`; code reuse requires explicit
permission. `ShmidtS/context-mode-rust` is a reference for hybrid retrieval
and observable context packing, not a performance baseline; its implementation
is `Elastic-2.0` and must not be transplanted into this MIT project without an
explicit licensing decision. `Niko1221/Strata` is MIT, but remains optional
local-inference and tiered-runtime inspiration rather than a RAG engine
dependency.

The reference workload must not turn into an arXiv-specific API in the core
library. It exists to demonstrate that the same source-grounded corpus can be
served by a simple SQLite/FP32 baseline and by the project's compact,
provenance-bound retrieval/storage path.
