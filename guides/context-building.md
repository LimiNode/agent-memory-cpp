# Context Building

Status: `Contract and roadmap`. Context construction is a first-class stage
after retrieval; it is not an implicit string concatenation helper.

## Pipeline

```text
retrieve candidates
  -> rerank / policy filter
  -> deduplicate
  -> source diversity and authority policy
  -> token-budget selection
  -> context pack with provenance
```

The retrieval result set and the context actually shown to a host model are
different observable artifacts. A context builder must not make omitted
evidence look as if it was never retrieved.

## Minimal contract

A future dependency-free contract should expose the equivalent of:

```cpp
struct ContextPack {
    std::vector<ContextItem> items;
    std::size_t token_budget;
    std::size_t tokens_used;
    bool is_truncated;
    std::vector<ContextOmission> omissions;
};
```

`ContextItem` binds the canonical resource/revision, chunk identity,
projection/model identity, and the text range placed in the pack. It carries a
stage lineage rather than one ambiguous score:

```text
lexical rank/score (when present)
vector rank/score (when present)
fusion rank/score and fusion policy
reranker rank/score and model identity
final selection rank and policy revision
```

`ContextOmission` records the candidate identity (or an aggregate with a
stable digest), omission reason, and policy revision. Reasons include
`token_budget`, duplicate content, source-diversity limit, authority/filter
policy, and malformed or unavailable source.

At minimum, every pack reports:

```text
retrieved_count
eligible_count
included_count
omitted_count
tokens_used / token_budget
deduplication_count
source_count and diversity policy
pack/provenance digest
```

The omission metadata is part of the evidence contract. A pack with 7 of 20
retrieved candidates must say that 13 were omitted and why.

## Token accounting

Token budgets use the selected provider/model tokenizer, not whitespace-word
estimates. The context manifest records tokenizer identity, special-token
policy, separators, truncation policy, and the exact budget. Silent truncation
is forbidden; if a provider imposes a smaller limit, the pack is marked
truncated and retains the omission record.

## Determinism and safety

Given the same candidate receipt, policy revision, tokenizer, source revisions,
and budget, packing should be permutation-stable. Tie-breaking is numeric-ID
first with the repository's documented fallback. Context construction does not
grant authority to execute instructions found in retrieved text; evidence,
instructions, and policy metadata remain separate result types.

## Evaluation lane

Context quality is evaluated separately from retrieval quality. A benchmark
may report context precision/coverage, source diversity, token utilization,
omission rates, pack latency, and provenance coverage alongside Recall@K,
nDCG@10, and MRR. A higher retrieval score does not prove a better context
pack, and a shorter pack does not prove less information loss.

The first implementation should be a dependency-free deterministic builder.
Provider-specific prompt formatting, compression, or LLM summarization belongs
behind an adapter and must preserve the same omission/provenance contract.
