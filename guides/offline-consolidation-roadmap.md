# Offline Consolidation and Replay Roadmap

This guide defines the research and planning contract for offline consolidation
of agent memory. It uses sleep and replay in the biological literature as a
design analogy; it does not introduce a literal sleep subsystem, a new memory
hierarchy, or an agent scheduler.

Current status: **Docs/tests only**. This contract does not claim that an
offline consolidator is implemented.

## 1. Purpose and boundary

Online ingestion should remain small and durable. It accepts observations,
source revisions, events, decisions, tasks and other canonical records without
waiting for a global reorganisation of the corpus.

Offline consolidation is a host-scheduled, bounded job that may inspect a
pinned corpus frontier and publish derived projections:

- summaries and summary trees;
- DomainMaps and navigation projections;
- query variants and activation hints;
- graph or associative projections;
- compiled handoffs and decision-relevant context views.

The memory library owns the durable records, projection contracts, provenance,
frontier binding and publication semantics. A host or runtime owns scheduling,
LLM/model calls, resource limits, priority policy and action authority.
Consolidation must not execute tools, make decisions, reserve resources or
change host permissions.

The word "sleep" is an explanatory label for an offline phase. It must not
become a public API or imply that a provider, model or process is continuously
running.

## Research basis and limits

Sleep-consolidation research is used here only as a biological analogy for
offline replay, transformation and integration of prior experience. The
systems-consolidation literature, including the
[Nature Neuroscience review](https://www.nature.com/articles/s41593-019-0467-3),
does not establish these software phases, receipts, retention rules or
production quality. It motivates a research question: whether bounded replay
and targeted reactivation can improve derived projections while preserving
source and conflict semantics.

The software contract below is therefore an independent engineering proposal.
Its claims require the OC-0 experiments and independent decision probes; a
biological result is not evidence that a particular consolidation policy is
correct for an agent memory system.

## 2. Wake and offline phases

The phases have different guarantees:

| Phase | Primary purpose | Allowed mutation |
|---|---|---|
| Online / wake | accept new material with low latency | canonical append, revision, usage and runtime state |
| Offline / consolidation | compare material across a pinned frontier | derived projections through normal admission/publication |
| Recovery | resume an interrupted job | idempotent receipt/reconciliation only |

The online path must not silently replace a source with a summary. The offline
path must not silently promote a hypothesis into an observation.

A consolidation run records:

- input corpus and logical scope;
- source read frontier and per-context frontiers where applicable;
- selected input identities and revisions;
- policy and model revisions;
- assumptions and unknowns;
- output projection identities and digests;
- publication status and receipt;
- coverage, omissions and failure reasons.

A digest verifies identity or integrity. It does not replace the replay material
required to reproduce a run.

## 3. Replay selection

Replay is a bounded selection of existing material for comparison and
reorganisation. It is not a licence to read the whole corpus without a budget.

The selection policy may consider:

- unresolved or conflicting evidence;
- corrections to previously used material;
- decision relevance and pending commitments;
- newly connected concepts or domains;
- stale or incomplete projections;
- rare constraints and low-frequency but protected facets;
- a bounded sample for coverage and regression control.

Frequency, novelty, use count or recency alone are insufficient. A low-frequency
record may be a correction, independent confirmation, audit record or safety
constraint. Such records are protected by lifecycle and retention policy.

The policy must record why an item was selected, deferred or excluded. Unknown
utility results in defer/unknown, not automatic deletion or authority loss.

## 4. Consolidation outputs

A derived output is published only with enough lineage to identify:

- the canonical source identities and revisions;
- the source frontier used by the run;
- the derivation policy, model and prompt revision when applicable;
- assumptions, coverage and unresolved conflicts;
- generation, content digest and publication receipt.

A projection may improve routing or context assembly. It does not by itself:

- become independent evidence;
- upgrade source trust or authority;
- resolve a contradiction;
- change lifecycle state;
- increase epistemic corroboration through duplication;
- create a Task, Decision, Expectation or host reservation.

If a summary, map or graph projection is rejected, its canonical inputs remain
addressable. If a projection is stale or its replay material is unavailable, the
implementation must report an unavailable or dirty state and fail closed for
operations that require reproducibility.

## 5. Schema formation and source protection

Offline consolidation may discover recurring structure and create a schema,
concept, relation, DomainMap or playbook projection. It must preserve the
distinction between:

- source observation;
- derived explanation;
- routing metadata;
- unresolved hypothesis;
- validated procedure.

A derived summary can point to several sources, but those sources remain
separately retrievable. Contradictory sources remain visible as contradictions.
A late correction is appended or versioned; it is not erased by rewriting an
older summary.

The existing `SummaryPromotionJob` remains scoped to its summary/`CompiledArticlePayload`
contract. It is not a universal publisher for DomainMap, graph or other
navigation projections; those outputs use their applicable derived-projection
publication and admission contracts. The Decision-Relevant Retention Gate is
required when a consolidation result proposes retirement, supersession or loss
of ordinary source availability. It is not automatically required for every
derived output. Creating a projection never makes an input `Superseded` automatically.

## 6. Publication and recovery

Publication is append-only or versioned and must be idempotent. A job may use:

1. pinned source frontier;
2. deterministic materialisation of inputs;
3. derived output construction;
4. validation against source and policy;
5. publication of a new projection revision;
6. receipt recording.

A crash may leave an unknown publication outcome. Recovery must inspect the
receipt and projection generation before retrying. It must not regenerate a
different result from the current moving baseline and present it as the same
run.

When multiple contexts are involved, per-context frontiers and publication
receipts remain explicit. Offline consolidation does not create cross-context
ACID.

## 7. Research gate OC-0

The first experiment is an **Offline Consolidation and Replay Gate**.

Comparison arms should include:

- no-op baseline: retain canonical records and existing projections;
- bounded extractive consolidation;
- bounded abstractive consolidation;
- navigation/map refresh;
- optional combined summary plus navigation refresh.

All arms use the same source corpus, scope, read frontiers, decision probes and
resource budgets. Calibration, validation and held-out test partitions are
disjoint. Final probes and oracle answers must not influence replay selection,
projection admission, model/policy tuning or thresholds.

Required measurements include:

- decision-relevant detail retention;
- chronology, exception and conflict preservation;
- provenance and evidence-root retention;
- retrieval and final ContextPack quality;
- rare-facet and correction recall;
- stale-map and stale-summary rate;
- update, delete and rebuild consistency;
- write cost, storage cost, decode cost and read latency;
- replayability and receipt completeness.

At least part of the gate must use exact or key-based probes. An LLM judge is
not a required source of truth.

## 8. Acceptance fixtures

The initial fixture set should cover:

- a new observation connected to an existing domain;
- a correction that conflicts with an older summary;
- a rare safety or operational constraint;
- two independent sources that must not become one proof;
- a deleted or superseded source;
- an interrupted publication and retry;
- a stale projection after a source revision;
- missing replay inputs;
- a cross-context corpus with separate frontiers;
- a navigation map that points to the wrong or forbidden scope.

The fixture must assert that rejected or unavailable derived outputs do not erase
or downgrade canonical source material.

## 9. Related owners

This contract extends, by reference:

- `compaction-roadmap.md` for compaction jobs and source retention;
- `evaluation-roadmap.md` for decision-relevant retention and independent
  evaluation;
- `knowledge-activation-roadmap.md` for DomainMap and activation;
- `agent-runtime-integration-roadmap.md` for host/runtime boundaries;
- `source-trust-roadmap.md` for evidence ancestry and non-laundering.

It does not create a new `KnowledgeUnitKind`, public consolidation API,
scheduler or authority system.
