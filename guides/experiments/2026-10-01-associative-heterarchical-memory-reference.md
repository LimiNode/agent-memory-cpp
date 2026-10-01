# Associative and heterarchical memory reference

Date: 2026-10-01  
Status: research-only experiment note; not a normative architecture contract  
Related work: AH-MemoryHub and AG_Memory review material

## Question and hypothesis

Can associative, heterarchical memory prototypes contribute useful patterns to
agent-memory-cpp without importing an external ontology or making activation a
source of truth?

Hypothesis: several representation and evaluation patterns transfer well when
they remain bounded, provenance-bearing projections. They must not replace the
project's existing ownership rules for canonical records, admission, evidence,
or authority.

## Source, setup, and limitations

The comparison is based on the supplied reviews and source descriptions of
AH-MemoryHub and AG_Memory. Exact upstream revisions, licenses, and executable
reproduction inputs were not supplied, so this note is a qualitative reference,
not a reproducible benchmark or a provenance claim about a pinned release.

The source projects are treated as research references. Their terminology,
storage choices, benchmark results, and ontology are not automatically adopted
by agent-memory-cpp.

## Expected and actual result

Expected: identify transferable memory/reasoning patterns and explicit
non-adoptions.

Actual: the useful layer is a set of typed records, bounded projections,
provenance links, and isolated evaluation fixtures. The prototypes do not by
themselves establish a canonical reasoner, an authority ledger, or a universal
runtime ontology for this project.

## Transferable patterns

- **N-ary typed facts and role bindings.** A fact may involve more than two
  participants; explicit roles preserve which entity occupies which position.
- **Occurrence/reference identity.** A mention, event occurrence, and enduring
  entity should not be collapsed merely because their labels look alike.
- **Extraction is not admission.** A parser, model, or provider may emit a
  candidate. A separate governed path decides whether a durable memory record
  is admitted.
- **Source-span provenance.** Derived claims should retain the source artifact,
  document revision, and source span or equivalent grounding evidence where
  available.
- **Retrieval snapshot is not activation.** A retrieved set is a bounded query
  result; an activation trace is a runtime projection explaining why items were
  attended or propagated. Neither is automatically canonical truth.
- **Activation seed reasons and traces.** A seed can record a typed reason such
  as query match, relation expansion, recency, or explicit user focus. The trace
  is useful observability, not proof.
- **Support/proof versus materialization.** Supporting evidence and a derived
  conclusion should remain distinguishable from the act of materializing a
  durable record.
- **Evaluation isolation and replay.** Counterfactual, ablation, and scenario
  runs need isolated state, explicit inputs, and replayable configuration so
  activation cannot leak into factual memory.

## Deliberately non-adopted patterns

The following are not canonical decisions for agent-memory-cpp:

- a universal `C/P/H` or `S/C/P/H/L` ontology;
- excitation or Hebbian state inside canonical memory records;
- a global pacemaker or universal tick as the time model;
- a model-owned reasoner, memory writer, or authority mechanism;
- automatic materialization of every derived truth;
- mandatory Neo4j, MDBX, ANN, Python, or provider dependencies imported from a
  research prototype;
- benchmark scores treated as proof of semantic correctness or production
  readiness.

## Follow-up checks

Before any implementation claim is based on this reference, the project should:

1. pin exact external commits and licenses;
2. add a relational-fact fixture only when a concrete contract requires it;
3. evaluate activation seed reasons, support paths, and scenario isolation;
4. compare BM25+dense retrieval with graph-only and graph-plus-activation
   variants under the same dataset and budget;
5. test cycle handling, fan-out bounds, decay, quiescence, and long-session
   saturation.

## Relationship to project architecture

This note informs experiments and future evaluation design. It does not change
the canonical specification in `guides/memory-stacks-roadmap.md`, does not grant
authority to activation, and does not make a provider or model the owner of
memory admission. Any future adoption requires a separate contract/roadmap
decision with tests and provenance.
