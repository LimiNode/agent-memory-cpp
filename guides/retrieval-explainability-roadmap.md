# Retrieval Explainability Roadmap

Status: `Proposal`; explainability is an optional diagnostic path and must not
slow the normal retrieval path unless explicitly requested.

## Stable diagnostic shape

An explain record may contain:

```text
unit_id, final_rank, final_score,
retriever/source ranks and scores,
matched terms or vector/codec arm,
graph path or projection kind,
filters applied and rejected candidates,
source/provenance references,
candidate depth, reranker, and tie-break policy
```

The record identifies what happened; it does not claim that a score is a
causal explanation of relevance.

## Delivery stages

1. lexical explain: matched fields, terms, BM25 components and filters;
2. dense explain: vector model identity, metric, candidate depth and exact
   rerank status;
3. hybrid explain: per-source rank, RRF contribution, and dropped candidates;
4. graph explain: entity/relation/path evidence;
5. compression explain: retained source spans, dropped spans, and budget;
6. experiment explain: runner/artifact/input hashes and evidence status.

Each stage has a deterministic fixture and a disabled fast-path test. Explain
records are diagnostic artifacts, not a second source of truth.
