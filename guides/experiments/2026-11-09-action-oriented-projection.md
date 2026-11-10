# Action-Oriented Semantic Projections

Status: research proposal. No implementation or production capability is
authorized by this note.

Date: 2026-11-09

This note records a possible future experiment inspired by action-oriented
knowledge representations and structured matching of document meaning. The
external articles are references for a hypothesis, not evidence that the
proposed model is universal:

- [Meaning of text or knowledge representation in an action-based system](https://habr.com/ru/articles/554738/)
- [Examples of representing document meaning through actions](https://habr.com/ru/articles/575838/)

## Question and hypothesis

Can a provenance-bound projection of actions, events, claims, roles,
modalities, conditions, and outcomes improve answers to structured questions
without weakening the source-of-truth and freshness guarantees of the memory
layer?

The working hypothesis is deliberately narrow:

> For questions about roles, obligations, events, conditions, and outcomes, a
> typed action-oriented projection may improve answer precision and recall over
> lexical retrieval alone, provided that every returned proposition is checked
> against its source evidence.

This is a retrieval hypothesis, not a claim that actions are a universal model
for every knowledge unit or that a structured projection can replace an LLM.

## Proposed representation boundary

The projection would sit above the existing evidence-bearing path:

```text
SourceRevision / Raw Artifact
    -> KnowledgeUnit / Original Projection
    -> Action / Event / Claim Projection
    -> structural query and candidate matching
    -> source-evidence validation
    -> answer or citation
```

`Action`, `Event`, and `Claim` are distinct semantic records even when they
share fields. An action description is not an executable operation. A sentence
such as `delete the file` must never become a call to `delete_file()` merely
because an extractor emitted an action-shaped record. Execution authorization,
policy checks, and orchestration remain outside `agent-memory-cpp`.

The experiment must keep these distinctions explicit:

| Record | Meaning | Minimum evidence rule |
|---|---|---|
| `ActionDescriptor` | A proposed or described operation, including actor, object, conditions and modality | The source quote supports the description; it grants no execution authority |
| `Event` | An occurrence asserted by a source or observation | The occurrence and its time/source revision are represented; an extracted sentence alone is not an observation receipt |
| `Claim` | A proposition that may be supported, refuted or unresolved | Supporting and refuting evidence are retained independently; absence of a supporting record is not refutation |
| Executable action | A host/runtime operation that may cause an external effect | Must be authorized and executed by a separate policy/runtime boundary; it is never inferred from a memory projection |

Action state is also not event truth. A descriptor may be `Proposed` or
`Intended`; execution may later be `Completed`, `Failed`, or `Cancelled`; an
`Observed` event requires an observation or receipt bound to its source
revision. `Completed` must not be inferred from `Intended`, and `Observed`
must not be inferred from a plan. These labels are experimental vocabulary and
must map to existing runtime/epistemic contracts before any public API is
proposed.

An initial projection schema may include:

- action or event predicate;
- actor, object, recipient, and other typed participants;
- modality (`required`, `allowed`, `possible`, `forbidden`, or unknown);
- temporal and conditional qualifiers;
- expected and observed outcomes, including an explicit discrepancy;
- evidence locator(s) into the exact source revision;
- producer identity and version;
- recipe or extraction fingerprint;
- freshness and invalidation policy;
- confidence as an extraction signal, never as proof of truth.

The fields are a research shape, not a public C++ API. The first experiment
may use an external JSON fixture and a validator rather than adding a graph
backend, a new scorer, or a production ingestion path.

## Invariants inherited from the memory architecture

The experiment must preserve the following boundaries:

1. **Canonical evidence remains authoritative.** An action-oriented record is
   a derived projection. It cannot replace `SourceRevision`, raw artifact
   bytes, `KnowledgeUnit`, `Original`, or the publication manifest.
2. **Exact revision binding is mandatory.** A projection extracted from
   `Source@1` cannot silently validate against `Source@2`. A changed source
   either invalidates the projection or leaves it available only as historical
   evidence according to policy.
3. **Unknown is not false.** At minimum, query results distinguish
   `Confirmed`, `Refuted`, and `Unknown`. Missing structure in an incomplete
   corpus is not evidence of negation. Conflicting sources and stale records
   require an explicit diagnostic state or policy. A conflict retains both
   supporting and refuting evidence and is not silently collapsed into
   `Refuted` or `Confirmed`. Where the runtime
   integration profile is enabled, reuse its existing epistemic-status
   contract rather than introducing a second status vocabulary.
4. **Provenance is carried through retrieval.** Structural matching must return
   the source revision, producer, recipe fingerprint, and evidence locator used
   for the answer. An unvalidated projection must not be presented as a cited
   fact.
5. **Graph-free first.** A graph index may be a later acceleration view, but
   the canonical publication/provenance substrate must work without one.
6. **No production activation.** This proposal does not change the Raw
   Markdown release profile, default retrieval route, coordinator, or runtime
   boundaries.

These rules align the experiment with the existing derived-projection and
artifact-provenance roadmaps rather than introducing a second source of truth.

## Planned experiment

### Corpus and conditions

Prepare 30--50 short UTF-8 texts containing a balanced mix of:

- positive and negative statements;
- obligations, permissions, and prohibitions;
- actors and objects with repeated names;
- temporal and conditional clauses;
- events followed by expected or observed outcomes;
- revisions that change one fact or obligation.

Use one frozen corpus and one frozen question set for both conditions:

1. **Lexical baseline:** ordinary lexical retrieval followed by answer
   extraction from the retrieved source text.
2. **Structured condition:** extraction of typed action/event/claim records,
   structural candidate matching, and mandatory source-evidence validation.

The extractor may be an LLM for the first study. Every emitted record must
pass a typed schema validator and retain an exact quote or evidence locator.
Extraction output is test data, not an instruction to execute an operation.

### Measurements

Report at least:

- answer precision and recall for role, obligation, event, condition, and
  outcome questions;
- unsupported positive claims (false assertions);
- unjustified `No` answers where the correct state is `Unknown`;
- missed facts and incorrect actor/object binding;
- revision and stale-source errors;
- citation/provenance preservation rate;
- extraction, structural-matching, and evidence-validation errors separately;
- latency and token/cost overhead of the structured path.

The report must retain per-question outcomes and source references. Aggregate
accuracy alone is insufficient because a method that answers `Unknown` too
often can look safe while failing recall, and a method that returns plausible
claims without evidence is not acceptable for this library.

### Acceptance shape

The experiment can support a follow-up implementation only if:

- the structured condition improves the pre-registered target question class
  over the lexical baseline;
- unsupported claims and unjustified negatives do not regress beyond a
  pre-registered tolerance;
- source revision and citation checks remain exact;
- failures can be attributed to extraction, matching, or validation rather
  than hidden manual repair.

An inconclusive or negative result is useful: it should close the proposal or
limit it to a narrow domain instead of justifying a general semantic graph.

## Scope and follow-up

This is a future research lane, separate from the current source-backed Raw
Markdown ingestion and hydration work. The next engineering step, if the
experiment is approved, is a small fixture-and-evaluator PR. It should not
include:

- a new graph database or mandatory graph runtime;
- a general coordinator or agent executor;
- multimodal, OCR, ASR, or table hydration;
- a new lexical scorer;
- automatic production retrieval activation;
- changes to canonical admission or publication contracts.

Potential later consumers include semantic retrieval in ADELIA and a fast
behavior/planning contour in Efi. Those consumers may exchange structured
events or action descriptors, but execution authority remains their own
boundary and is never inferred from a memory projection.

## Limitations and open questions

- A 30--50 document fixture cannot establish domain-independent language
  coverage or ontology scalability.
- LLM extraction quality may dominate the result; it must be measured rather
  than hidden behind structural matching scores.
- The right schema for actions, events, and claims may differ by domain.
- A graph representation may be useful for navigation, but its value is an
  implementation question after the graph-free contract is validated.
- The experiment does not evaluate planning quality or authorize action
  execution.

Follow-up checks should freeze the dataset and qrels, publish the extraction
schema and validator, run the lexical and structured conditions from the same
source revisions, and record raw per-question results before any roadmap
promotion.
