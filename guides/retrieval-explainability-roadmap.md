# Retrieval Explainability Roadmap

Status: `Proposal`; explainability is an optional diagnostic path and must not
slow the normal retrieval path unless explicitly requested.

## Stable diagnostic shape

An explain record may contain:

```text
unit_id, influence_stage, final_rank, final_score,
retriever/source ranks and scores,
matched terms or vector/codec arm,
graph path or projection kind,
filters applied and rejected candidates,
source/provenance references and evidence ancestry status,
candidate depth, reranker, and tie-break policy
```

The record identifies what happened; it does not claim that a score is a
causal explanation of relevance.

## Stage lineage and omission semantics

Diagnostics should distinguish the following influence stages when the
corresponding evidence is available:

```text
persisted
  -> published / visible
  -> eligible
  -> candidate
  -> selected
  -> included in ContextPack
  -> host-approved provider request
  -> effective provider context (host-reported only)
```

An explain record may end at any stage. It must not collapse a persisted but
ineligible unit, an eligible unit not returned by a route, a candidate removed
by fusion, and a selected item omitted by the context budget into one generic
"not used" result. A diagnostic can use implementation-specific fields, but
its omission reason should preserve the relevant distinction, for example:
`not_published`, `outside_read_frontier`, `ineligible_filter`,
`route_miss_or_budget`, `deduplicated`, `fusion_or_rerank_budget`,
`context_token_budget`, `provenance_incomplete`, `host_policy_denial`,
`provider_compaction` or `host_substitution`.

The last two stages are external host observations. A library explain record
must mark them as unavailable when the host did not report them rather than
inferring them from a `ContextFingerprint` or prompt shape. The fingerprint
identifies the finished provider-neutral `Context`; the host-owned
effective-context artifact is specified in
[`knowledge-base-roadmap.md#831-effective-context-and-omission-provenance-m2`](knowledge-base-roadmap.md#831-effective-context-and-omission-provenance-m2).

Evidence binding is an explicit host/runtime event with its own provenance,
purpose and relevant revision/frontier. Prompt inclusion, rank, model access
or a provider request do not by themselves establish that a model used an item
as evidence. When ancestry is available, diagnostics may report immediate
inputs and a policy/versioned independence assessment (`Independent`,
`Dependent` or `Unknown`) without fixing a C++ enum, wire format or storage
schema. `Unknown` ancestry is neither corroboration nor proof of dependence.

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
