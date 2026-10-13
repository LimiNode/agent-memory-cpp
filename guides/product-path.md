# Product path: a growing knowledge base

This document records the product-oriented path for `agent-memory-cpp`. It is
a prioritisation and sequencing guide, not a replacement for the normative
milestones in [`milestones.md`](milestones.md). When the two disagree about
scope or a required gate, `milestones.md` wins.

## Product objective

The first practical consumer is a self-growing knowledge base. It receives
material from external source connectors, preserves source identity and
revisions, extracts candidate knowledge, and makes every published statement
traceable to the material that supports it. The library is the durable,
inspectable substrate; connectors, schedulers, extraction models and admission
policies remain application-owned.

The success criterion is operational rather than algorithmic:

> After a source is collected and processed, a later question returns useful
> knowledge together with a path to the relevant source revision, document
> region, page, or time interval.

This objective keeps the project focused on a complete source-to-knowledge
path instead of treating every retrieval experiment as a separate product.

## Dependency graph

The roadmap is a graph, not a single queue:

```text
                         +--> M1a: dense and hybrid retrieval --+
M0: lexical document memory +--> M1b: structured knowledge --------+--> M2: advanced profiles --> M2+: optional research
                         +--> M1c: bounded maintenance ---------+

Orthogonal lane: runtime adapters and host integration
```

M0 is the shared product foundation. M1a, M1b and M1c are independent unless
a concrete profile requires a combined capability. M2 depends on the selected
M1 capabilities; it does not require every M1 feature to be implemented at
once. Runtime integration is orthogonal and must not pull orchestration,
scheduling or action authority into the memory library.

Research may explore a later node early, but an experiment does not promote a
milestone to `Implemented` and does not create a new dependency by itself.

## What the library owns

The boundary is deliberately narrow:

- durable source, resource and revision identity;
- canonical text and retained artifact representations where their profile
  is enabled;
- immutable evidence anchors and provenance metadata;
- knowledge-unit identity, lifecycle and supersession;
- rebuildable lexical, vector and other derived indexes;
- scope isolation, retrieval and citation hydration;
- bounded storage and recovery contracts.

External applications own acquisition credentials, connector scheduling,
HTML/PDF/media extraction, OCR/ASR, LLM inference, candidate review and the
decision to publish or supersede a knowledge unit. The library must not become
a crawler, model runtime, workflow engine or autonomous agent.

## Source-neutral ingestion model

The source type changes the evidence locator, not the durable identity model.

| Source family | Retain | Evidence locator |
| --- | --- | --- |
| Messages and discussions | selected messages, links, attachments and revisions | source/channel identity, message identity, thread context and timestamp |
| Video or audio material | available transcript segments and optional informative frames | media identity plus a time interval or frame region |
| Web and technical documents | normalized text, section structure, code, tables, figures and links | URI/revision plus section or text range |
| Scientific and archival documents | bibliographic identity, accessible text, figures and formulas | document/version plus page or region |

An archive of a discussion is not required when the product goal is to retain
selected knowledge from it. Conversely, a media transcript without stable
time intervals is not sufficient evidence for a durable note. Retention and
access policies must be explicit; unavailable raw material must remain marked
as unavailable rather than reconstructed from a summary.

## Self-growing knowledge lifecycle

Every connector-driven update follows the same conceptual stages:

1. **Discover** a new source or a new revision of a known source.
2. **Acquire and normalize** the allowed bytes or text, preserving source and
   representation identity.
3. **Extract candidates** such as claims, definitions, methods, limitations,
   links and draft notes. Candidates are not established facts.
4. **Reconcile** candidates with existing knowledge: duplicate, refine,
   contradict or supersede, while retaining the supporting evidence.
5. **Publish** reviewed or policy-approved units and rebuild only the affected
   projections and indexes.
6. **Consolidate** bounded summaries and topic maps without losing the path
   from each claim to its evidence.

Candidate, reviewed and canonical states must remain distinguishable. Multiple
summaries of one source are not independent confirmation. A model may propose
a change, but publication policy and provenance determine whether it becomes a
canonical fact.

## Product stages

These stages translate the dependency graph into a practical order. They are
not additional milestone gates.

| Stage | Product capability | Depends on | Acceptance signal |
| --- | --- | --- | --- |
| KB-0 | Durable text source, revision-bound note, lexical retrieval, citation hydration and reopen | M0 | Import, update, search, close/reopen and recover the source binding |
| KB-1 | Candidate extraction, versioned notes, duplicate detection and idempotent refresh | M0 plus selected M1b contracts | Reprocessing a source is stable; changes supersede rather than erase evidence |
| KB-2 | Time, page and figure anchors; retained representations and optional image-derived views | Artifact-provenance profile | A statement opens the exact document region, page or time interval |
| KB-3 | Subscriptions, bounded queues, targeted reindex, recovery and admission policy | M1c | Restart and partial failure do not lose or duplicate publication work |
| KB-4 | External consumer API, scopes, access policy and operational observability | Stable M0/M1 substrate | Multiple applications can use the same knowledge base without cross-scope leakage |

The current narrow implementation closes part of KB-0: durable canonical text,
deterministic lexical search, scope isolation, update and reopen. It does not
yet complete the M0 table in `milestones.md`; resource manifests/importers,
knowledge-unit stores, source summaries, projections and retrieval traces remain
explicit gaps.

## Research policy

Research is selected by a failing product gate, not by the number of available
algorithms. The common evaluation dimensions are:

| Product pressure | Compare | Measure |
| --- | --- | --- |
| Storage cost and partial reads | raw text, framed compression and dictionaries | bytes, read latency and update amplification |
| Metadata and tenancy | typed fields and secondary indexes | filter latency and scope isolation |
| Mutable sources | block edits, chunk changes and metadata-only updates | stale-result rate, affected work and write amplification |
| Exact terminology | BM25, BM25F, morphology and phrase/proximity variants | Recall, nDCG and identifier/quote fidelity |
| Vector recall at a budget | exact flat oracle and bounded ANN routes | recall, p95, memory, build and update cost |

The exact flat route is the quality oracle for approximate vector indexes. Any
comparison must include corpus and query hashes, chunking/tokenizer settings,
candidate depth, cache state, hardware and write costs. A faster index that
cannot preserve source revisions or update safely is not a product win.

## Near-term work order

1. Finish the remaining M0 contracts around resource manifests, knowledge units,
   source summaries, projections and retrieval traces.
2. Add the minimum M1b identity/provenance needed for candidate notes and
   revision-bound citations.
3. Add a small exact dense baseline and hybrid fusion only when KB-0 lexical
   quality exposes a measured need.
4. Add bounded maintenance and recovery before enabling automatic source
   subscriptions.
5. Use the growing knowledge base as a regression corpus for compression,
   incremental updates, lexical improvements and ANN comparisons.

This order keeps the first product usable while allowing independent M1 lanes
and tightly scoped research to proceed in parallel.

## Related normative documents

- [`milestones.md`](milestones.md) — scope resolver and dependency DAG;
- [`retrieval-roadmap-coverage.md`](retrieval-roadmap-coverage.md) — current
  implementation gaps;
- [`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md) — source,
  representation and evidence-anchor contracts;
- [`canonical-content-storage-roadmap.md`](canonical-content-storage-roadmap.md)
  — canonical text and revision storage;
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) and
  [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) — durable units,
  citations and structured knowledge;
- [`resource-reindexing.md`](resource-reindexing.md) — targeted updates and
  reindexing;
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) — lexical retrieval
  ladder and acceptance gates.
