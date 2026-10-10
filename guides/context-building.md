# Context Building

Status: `Contract and roadmap`. Context construction is a first-class stage
after retrieval; it is not an implicit string concatenation helper.

## Pipeline

```text
retrieve candidates
  -> rerank / policy filter
  -> deduplicate
  -> source admissibility, provenance and diversity policy
  -> token-budget selection
  -> context pack with provenance
```

The retrieval result set and the context actually shown to a host model are
different observable artifacts. A context builder must not make omitted
evidence look as if it was never retrieved.

## Influence lanes and authority boundaries

Memory influence is a staged observation, not one boolean `used` or
`authoritative` flag. The core retrieval/context lane is:

```text
persisted
  -> published / visible
  -> eligible
  -> candidate
  -> returned
  -> selected for context
  -> included in ContextPack
  -> provider egress allowed by host
  -> effective provider context, if the host reports it
```

Here `candidate` means an admitted route/fusion candidate; `returned` means a
member of the final ordered `RetrievalResult`; and `selected for context`
means that the host or context planner chose a returned item for packing.

These are distinct logical observations, not a new lifecycle FSM or a required
provider workflow. An ordinary search application may stop at returned hits
without building a context or invoking a provider. Eligibility is resolved
against the existing `FilterFrontier` and `ReadFrontier` in
[`retrieval-execution-roadmap.md`](retrieval-execution-roadmap.md); this lane
does not defer hard authorization until after candidate ranking.

The first stages are library/retrieval artifacts. The final two stages belong
to the external host boundary: the library may hand off a `ContextPack`, but it
cannot infer that a provider request was approved or what a provider actually
received. Egress permission alone does not prove that a request was sent or
consumed. A record can stop at any stage, and reaching a later stage does not
retroactively change the earlier evidence or provenance state.

An optional runtime/action continuation is owned by the
[runtime integration guide](agent-runtime-integration-roadmap.md#memory-influence-and-action-admission):

```text
explicit evidence binding
  -> action intent
  -> live authority / policy admission
  -> execution attempt
  -> outcome
```

Retrieval visibility, rank, `ContextPack` inclusion or prompt inclusion do not
imply model use, explicit evidence binding, current action authority, action
admission or execution success. An evidence binding is an explicit
host/runtime event that records what was bound, for which purpose and under
which relevant revision/frontier; it is never inferred from prompt position.

In this document, **source admissibility/provenance policy** means the policy
that decides whether a source may participate in retrieval or context. It is
not runtime action authority. Historical authority evidence and current live
permission remain owned by the external runtime boundary; see
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).

`ContextFingerprint` is the provider-neutral fingerprint of the finished
library `Context`, as defined by
[`runtime-services-roadmap.md`](runtime-services-roadmap.md). It does not
claim to fingerprint the final provider payload. Provider compaction, system
instructions, tool schemas, host substitutions and other transformations are
represented only by the separate host-owned effective-context artifact
described in [`knowledge-base-roadmap.md#831-effective-context-and-omission-provenance-m2`](knowledge-base-roadmap.md#831-effective-context-and-omission-provenance-m2).

When a host must resume context construction after a crash, the durable
working-context and unknown-provider-outcome rules are owned by the W0 contract
in [`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md#durable-working-context-and-crash-recovery-w0-m2). A `ContextPack` remains
the provider-neutral library artifact; a resumed runtime operation must
revalidate its frontier, revisions, policy and idempotency binding before it
uses the pack again.

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
`token_budget`, duplicate content, source-diversity limit, source-admissibility
policy, and malformed or unavailable source. Per-item omissions apply only to
candidates authorized for disclosure. Authorization denials use the existing
aggregate-only `PolicyDecisionTrace`; omissions must not expose denied unit
IDs, text, citations or metadata.

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

Acceptance fixtures must distinguish a returned candidate omitted by budget,
an included item denied provider egress, an authorized request that was never
sent, and a host-reported compaction that omits an item. Missing host reports
remain unknown. None of these observations alone establishes evidence binding
or action admission. Source ancestry and corroboration follow
[`source-trust-roadmap.md`](source-trust-roadmap.md), independently of packing.

## Evaluation lane

Context quality is evaluated separately from retrieval quality. A benchmark
may report context precision/coverage, source diversity, token utilization,
omission rates, pack latency, and provenance coverage alongside Recall@K,
nDCG@10, and MRR. A higher retrieval score does not prove a better context
pack, and a shorter pack does not prove less information loss.

The first implementation should be a dependency-free deterministic builder.
Provider-specific prompt formatting, compression, or LLM summarization belongs
behind an adapter and must preserve the same omission/provenance contract.
