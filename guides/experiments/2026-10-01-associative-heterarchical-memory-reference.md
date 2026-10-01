# Associative and heterarchical memory reference (2026-10-01)

This note records transferable design patterns observed in
[AH-MemoryHub](https://github.com/Hausmaster333/AH-MemoryHub) and in the
associated associative-heterarchical memory material. It is a reference and
roadmap input, not a proposal to add AH-MemoryHub, Neo4j, or an ignition runtime
as a dependency of `agent-memory-cpp`.

## What is relevant to this project

### Typed n-ary facts

A fact may have a predicate/schema and several typed role bindings instead of
being forced into binary triples:

```text
RelationalFact
  predicate_or_schema
  typed role bindings[]
  source references[]
  temporal and provenance components
```

This fits the existing `KnowledgeUnitEnvelope + components` direction and the
Relation-unit graph projection. It should remain an optional structured-memory
profile; ordinary notes and chunks do not need to become graph nodes.

### Candidate extraction is not admission

The useful pipeline boundary is:

```text
provider/model proposal
  -> typed candidate IR
  -> deterministic grounding and normalization
  -> validation and policy decision
  -> atomic admission
  -> committed successor
```

Providers must not allocate final canonical IDs, publish revisions, or write
directly to the canonical store. A rejected, quarantined, or superseded
candidate remains observable as a decision outcome with its provenance.

### Source-grounded structured facts

An admitted structured fact must be traceable beyond a document identifier:

```text
fact
  -> source revision
  -> segment/span or other evidence anchor
  -> exact bytes/text hash
  -> extraction/provider revision
```

This is an acceptance criterion for future entity/fact extraction. It extends,
but does not replace, the existing `SourceRef` and `EvidenceAnchor` contracts.

### Retrieval, activation, and truth are different

The layers remain separate:

```text
typed retrieval
  -> bounded candidate snapshot
  -> optional activation/expansion
  -> working-set admission
  -> diagnostic trace
```

Activation, salience, and ranking are not semantic truth, authority, or proof.
When activation is implemented, its trace should distinguish the seed reason,
each expansion step, the source path, the score/reason, and working-set
admission. A minimal path used to support an answer is not necessarily the
complete activation path.

### Identity and occurrence are separate

The same entity can participate in multiple episodes, claims, or procedures.
Graph references therefore need stable identity plus occurrence/context
qualification. A shared target does not imply a shared event, source span, or
authority context. This is consistent with immutable revisions and
origin-qualified `SourceRef` values already used by the project.

## Roadmap entries

| Milestone | Planned addition | Dependencies | Minimum benchmark / acceptance | Risk |
|---|---|---|---|---|
| M1b/M2 research | Optional `RelationalFact` payload with typed role bindings and occurrence-qualified references | Relation unit, component validation, `SourceRef`/`EvidenceAnchor`, graph projection | Round-trip 1k mixed-arity facts; deterministic IDs/order; every admitted fact resolves to an evidence anchor | Premature graph schema or envelope bloat |
| M1b/M2 research | Candidate IR to deterministic mutation plan boundary | provider adapter, validation, atomic publication and quarantine contracts | Replaying the same candidate set yields byte-identical decisions and one atomic successor; malformed/unauthorized candidates are rejected | Provider leakage into canonical state |
| M1b/M2 research | Activation trace contract (`seed`, `step`, `reason`, `source path`, `admission`) | knowledge activation, bounded graph expansion, retrieval trace | Same snapshot and plan produce the same trace; answer-support path is separately identifiable; access filters remain fail-closed | Treating activation as proof or hidden authorization |
| M2+ research | Structured graph/activation evaluation profile | evaluation roadmap, qrels and exact oracle | Compare lexical+dense hybrid, typed graph expansion, and optional activation on the same corpus; report Recall@K, nDCG@10, MRR, latency, expansion and explanation coverage | Attractive graph output without downstream retrieval lift |

These are planned profiles, not completed implementation or quality claims.
The current compressed-native and serving gates retain priority.

## Explicit non-goals

This reference does not promote:

- Neo4j or any graph database to canonical storage;
- a universal `S/C/P/H/L` model for every memory item;
- Hebbian updates, global ignition clocks, or autonomous background loops;
- garbage collection that bypasses retention, tombstone, and provenance rules;
- a solver or formal theorem prover in the core library.

Any future formalization/solver integration must remain an optional projection
and keep its derivation trace attached to exact source scope. A proof trace is
evidence about a derivation, not truth or authority by itself.

## References and evidence limits

- [AH-MemoryHub](https://github.com/Hausmaster333/AH-MemoryHub) — executable
  semantic reference for typed symbols, hypernodes, candidate extraction and
  activation-oriented traces.
- The `AG_Memory` material mentioned in the discussion was not independently
  identified as a public repository; it is therefore not treated as evidence.

Reported retrieval comparisons in external projects are preliminary and are
not transferred into `agent-memory-cpp` acceptance thresholds. Any graph or
activation promotion requires a fresh, same-corpus evaluation with fixed
qrels, exact-oracle recall, latency boundaries, and provenance coverage.

See also [`knowledge-base-roadmap.md`](../knowledge-base-roadmap.md),
[`knowledge-activation-roadmap.md`](../knowledge-activation-roadmap.md),
[`knowledge-units-roadmap.md`](../knowledge-units-roadmap.md), and
[`artifact-provenance-roadmap.md`](../artifact-provenance-roadmap.md).
