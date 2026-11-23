# Memory foundations: corrective audit and follow-up work order

- Date: 2026-11-23. Status: active audit brief; comparative research and new
  implementation are pending. Owner: memory-domain and context contracts.
- Source baseline: main `aea7d89f657c04b7b6388353530d9418a6a6ad81`.
  Implementation statements below are source/fixture inspection, not fresh
  execution or production qualification.
- Scope: temporal semantics, epistemic awareness, prompt/keyed memory, extensible
  knowledge processing/presentation, ontology-neutral structures and maintained
  views. Revisit before freezing
  their new public interfaces or persistent representations, or when a
  counterexample invalidates the proposed reuse boundary.
- This document owns the audit questions, acceptance work and disposition, not
  replacement wire grammars, DBIs, component layouts or a second knowledge model.

## 1. Reuse baseline and normative owners

Memory Blocks, bitemporal history and perspective-aware knowledge are not grounds
to create parallel storage systems. First reconcile existing implementation,
contracts and genuine gaps. The following distinctions constrain the follow-up:

| Area | Existing evidence and owner | Actual maturity / missing work |
| --- | --- | --- |
| Canonical blocks | [CanonicalContent](../../src/agent_memory/domain/CanonicalContent.hpp), [CanonicalContentStore](../../src/agent_memory/storage/CanonicalContentStore.hpp); [canonical owner](../canonical-content-storage-roadmap.md) | Stable IDs, hierarchy, revisions, exact block reads and expected-revision edits are implemented; a named-memory facade is not supplied by those primitives |
| Prompt composition | [types](../../src/agent_memory/domain/PromptComposition.hpp), [compiler](../../src/agent_memory/domain/PromptComposition.cpp), [fixture](../../tests/domain/prompt_composition_test.cpp); [prompt owner](../prompt-composition-roadmap.md) | Pure compiler, conditions/variants, subtree suppression, bounded typed templates and manifest digests are implemented; host activation is separate |
| Profile history | [MDBX adapter](../../src/agent_memory/infrastructure/mdbx/MdbxPromptAssemblyProfileStorage.hpp), [fixture](../../tests/infrastructure/mdbx/mdbx_prompt_assembly_profile_storage_test.cpp); prompt owner Phase B | Opt-in immutable profile history is implemented; storing or compiling a profile does not approve its use |
| Managed Memory Blocks / Keyed Memory Facade | [Managed-block work order](#41-managed-block-operations-and-independent-guarantees), prompt owner | Proposed high-level capability; named access, trusted mutation/attachment policy and the end-to-end workflow still need implementation and fixtures, not a new memory foundation |
| Temporal memory | [AM-13 and T0](../memory-lifecycle-governance-roadmap.md#3-am-13-bi-temporal-knowledge), [coverage](../retrieval-roadmap-coverage.md) | Bitemporal queries, origin-visible historical reads and temporal navigation are contract-only, not a production subsystem |
| Existing time fields | [KnowledgeUnitEnvelope](../../src/agent_memory/domain/KnowledgeUnit.hpp), [Conversation](../../src/agent_memory/domain/Conversation.hpp) | Record timestamps and independent optional source/observation times exist; their presence does not implement temporal queries, belief history or causal order |
| Perspective and epistemic layers | [runtime owner](../agent-runtime-integration-roadmap.md#components) | Perspective, evidence/contradiction and epistemic validation are roadmap contracts; an awareness/open-question service is not established |
| Search aspects and publication API | [SearchProjection](../../src/agent_memory/domain/SearchProjection.hpp), [IDerivedPublicationStore](../../src/agent_memory/storage/IDerivedPublicationStore.hpp); [representation owner](../derived-representations-and-codecs.md) | Fixed `ProjectionKind`, text body and single-scope/backend-local publication identity are implemented; named aspect/schema/recipe registration is a broader contract, not a universal publisher or an installed registry |
| Fenced MDBX publication subset | [B2 writer](../../src/agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp), [B3 reader](../../src/agent_memory/infrastructure/mdbx/MdbxCurrentDerivedPublicationReader.hpp), [fixture](../../tests/infrastructure/mdbx/mdbx_derived_publication_store_test.cpp); [frontier owner](../root-resource-frontier-conformance.md) | `publish_fenced` and one-snapshot current/historical reads exist for the bounded direct-unit/root binding path; not generic transitive lineage, federated publication or host authorization |
| Typed representation/locator values | [TypedRepresentation](../../src/agent_memory/domain/TypedRepresentation.hpp), [fixture](../../tests/domain/typed_representation_locator_test.cpp), [preview boundary](../typed-representation-locators.md) | Table/row/cell and media representation values, validators and bounded TL1 locator codec are implemented; this value-only preview does not supply a general catalog, parser, structured persistence or artifact hydration |
| Processing and presentation | [artifact/provenance owner](../artifact-provenance-roadmap.md#341-document-extraction-provider-boundary), [knowledge owner](../knowledge-units-roadmap.md), [linked-publication integration map](../linked-derived-knowledge.md), representation owner | Recipe/producer identity and runtime-neutral provider contracts exist; canonical Markdown materialization, [CanonicalNavigation](../../src/agent_memory/domain/CanonicalNavigation.hpp) and prompt compilation are implemented subsets, not a general processor/presenter composition |
| Question-conditioned aspects | [aspect/recipe owner](../derived-representations-and-codecs.md#aspect-representation-and-recipe-identity), [activation research](../knowledge-activation-roadmap.md#ar0-bounded-adaptive-associative-recall), [evidence owner](../source-trust-roadmap.md#epistemic-non-laundering) | Research candidate over arbitrary-aspect contracts; question/context parameterization, comparable functional views and an end-to-end affordance retrieval path are not implemented capabilities |
| Maintained knowledge views | [ContentChangeSet](../../src/agent_memory/domain/CanonicalContent.hpp), [compaction owner](../compaction-roadmap.md), [offline owner](../offline-consolidation-roadmap.md) | Canonical net block changes are implemented; compaction/offline contracts do not establish an installed `CompactionWorker` or a complete maintained-view service. Offline consolidation remains Docs/tests only |

In particular, the older roadmap wording about a "current TemporalComponent"
does not establish a C++ implementation: temporal memory remains **Contract
only** in the coverage ledger. `PromptPolicy`, `PromptCandidate` and
`PromptGenome` are conceptual names in this work order, not existing public
types. Existing fixtures cited here were inspected, not run in this docs-only
change. No new capability is promoted to Implemented.

### 1.1. Proposal classification and disposition

Use these evidence labels per surface: **IMPLEMENTED** means bounded source
code exists, not fresh execution/qualification; **SPECIFIED** means an existing
owner contract; **RESEARCH** means an unvalidated question or candidate;
**MISSING** means the proposed end-to-end behavior is not supplied. More than
one label may apply to different parts of a proposal. These labels do not
replace the repository coverage ledger or promote its maturity statuses.
The following disposition is audit work, not permission to change schemas.

| Proposal | Evidence classification | Disposition and remaining decision |
| --- | --- | --- |
| Temporal semantics / AM-13 | SPECIFIED bitemporal/frontier rules; RESEARCH interval/clock alternatives; MISSING temporal query subsystem | REUSE AM-13/T0; EXTEND time-meaning and compatibility decisions; DEFER extra universal axes pending a counterexample |
| Epistemic awareness / open questions | SPECIFIED perspective/evidence contracts; RESEARCH coverage/recognition model; MISSING end-to-end gap service | REUSE epistemic/provenance owners; EXTEND observer/context-qualified diagnostics; DEFER new kinds or a separate store |
| Managed Memory Blocks / keyed facade | IMPLEMENTED canonical edits, profile history and compiler; MISSING named facade and governed workflow | REUSE canonical/prompt primitives; EXTEND named access, operation-specific policy and attachment integration, not another source of truth |
| Safe prompt activation / evolutionary candidates | IMPLEMENTED pure compilation, not activation; SPECIFIED host authority boundary; RESEARCH candidate evolution; MISSING complete trusted activation/evaluation workflow | REUSE profiles/manifests; EXTEND trusted admission/rollback with held-out evaluation; DEFER autonomous evolution and agent-editable mandatory policy |
| Extensible knowledge processors | SPECIFIED aspect/recipe/provider contracts; IMPLEMENTED bounded publication subsets; MISSING complete registration-to-result composition | REUSE derivation identity and applicable B2/B3 paths; EXTEND typed profile/results/admission; NEW public types only after the minimal-consumer and compatibility review |
| Independent materializers/presenters | IMPLEMENTED Markdown/outline/prompt subsets; MISSING common multi-knowledge presentation workflow | REUSE exact revisions and provenance/selection contracts; EXTEND bounded output profiles; DEFER per-format stores and a renderer runtime |
| Maintained Knowledge Views | SPECIFIED consolidation/publication contracts; IMPLEMENTED block change sets and bounded freshness adapters; RESEARCH incremental policy; MISSING maintained-view service | REUSE source changes/receipts; EXTEND dependency-aware product integration; DEFER generic cache or background agent runtime |
| Question-Conditioned Derived Knowledge | SPECIFIED arbitrary-aspect foundation; RESEARCH affordance/risk/business and cross-domain comparison; MISSING qualified question-conditioned retrieval path | REUSE aspect/recipe/epistemic contracts; EXTEND the audit and matched experiment plan; DEFER default aspect generation, caching or Relation publication until evidence/admission justify it |

No proposal currently justifies a new fundamental memory entity, independent
provenance system or physical index by assertion alone. Sections 2-5 supply
the questions; section 6 supplies future acceptance work. The complete
comparative audit, API decisions and execution remain open.

## 2. Temporal Semantics Audit

Keep AM-13's bitemporal component and query forms as the base candidate. Two
interval axes have four boundaries, not four independent dimensions. Do not
copy Graphiti-shaped fields into every object or assume a universal third axis.
The audit must compare event-as-separate-evidence with an additional temporal
dimension using a case that cannot be represented faithfully by the simpler
model. AM-13/T0 remains the sole temporal query owner.

Assign each observation to its actual owner before choosing representation:

| Meaning | Owning boundary; must not be substituted with |
| --- | --- |
| Occurrence point/interval | Event/source evidence; not publication time |
| Source report/publication | Source revision/observation; not occurrence or receipt time |
| Durable registration | Canonical journal/storage receipt; not belief or visibility at every replica |
| Claim validity | Optional temporal component; not record lifecycle |
| Observer availability and belief adoption/rejection | Origin visibility receipts and separate perspective/epistemic history; visibility is not endorsement or actual use |
| Derivation/publication | Versioned provenance and publication receipt; not independent primary evidence |
| Order | Origin-scoped sequence and evidenced causal relations; not global order inferred from physical timestamps |

Resolve, without silently changing the existing contracts:

1. The [older TemporalComponent sketch](../knowledge-base-roadmap.md#41-operational-components)
   uses `valid_until_ms = 0` for "still valid", while AM-13 uses optional
   bounds. Distinguish real epoch zero, unknown, absent, open/unbounded and
   imprecise endpoints; record a compatibility decision before persistence.
2. Define validity interval inclusion, point/empty intervals, precision,
   uncertainty, timezone normalization and checked ms/us conversion. The
   inclusive runtime **sequence** range does not decide validity-interval
   semantics. Do not silently convert existing Conversation microseconds.
3. Separate recorded-time `known_at` from another observer's visibility, belief
   and decision use. Reuse AM-13/T0 visibility receipts and pinned `ReadFrontier`
   rules: a late import can change an unpinned old-time query. Stable replay
   needs the declared frontier/receipt policy, not merely a cutoff timestamp.
4. Evaluate which Allen interval relations are required and meaningful for
   incomplete/uncertain inputs. Evaluate Lamport or other clock contracts only
   if origin-local sequences and explicit causal evidence are insufficient.
   A numerical clock order alone must not be interpreted as causal evidence.
5. Evaluate AGM/belief-revision ideas against contradictory evidence and
   append-only provenance; they do not authorize erasing original observations
   or installing a belief-revision engine in core.

This is a pre-implementation/persistence gate for temporal work, not a reason
to delay the separately qualified AMCE codec. Conversation Phase 3/4 must retain
exact source/revision/origin references and declared historical frontiers so
later temporal components remain possible; no new timestamps, payloads or DBIs
are required by this brief.

## 3. Epistemic Awareness / Knowledge Gaps

Keep knowledge kind, epistemic layer, provenance, observer and authority
independent. Known-known / known-unknown / unknown-known / unknown-unknown are
not new `KnowledgeUnitKind` values. Use them only as analysis vocabulary for an
observer/claim-or-question/time/context relationship; its representation is an
audit question, not a newly specified public component.

Distinguish stored existence, authorized visibility, retrievability, recognized
relevance, belief adoption and actual use. In particular:

- No retrieval hit is not proof that no evidence exists or that an observer
  has no knowledge. Missing, budget-pruned, stale, conflicting and inaccessible
  evidence are different conditions. Public diagnostics must not leak denied
  record existence, content, locators or counts.
- A known open question needs an observer/context, scope, evidence frontier,
  missing-evidence or coverage basis, and a later resolution/reopening rule.
  First test whether existing units, relations and perspective contracts suffice;
  do not preselect a new gap store, enum or component.
- Unknown-known is a diagnosable retrieval/recognition gap only against an
  authorized declared reference set. It is not a universally detectable state.
  Unknown-unknown cannot be exhaustively enumerated; coverage limits and
  suspected blind spots remain explicit, bounded hypotheses.
- Confidence or shared character identity does not turn a local hypothesis into
  a validated claim or merge disconnected observers' knowledge. Keep supporting
  and contradicting evidence, revision history and validation policy explicit.

Audit the missing open-question/coverage behavior above the existing
[Perspective/EpistemicStatus contracts](../agent-runtime-integration-roadmap.md#perspectivecomponent)
and [activation boundary](../knowledge-activation-roadmap.md#1-boundary).
Do not implement live cognition, autonomous questioning or agent orchestration.

## 4. Prompt / Keyed Memory

Treat named access and governed context attachment as one proposed high-level
capability: **Managed Memory Blocks / Keyed Memory Facade**. REUSE canonical
content, editing, profiles, compiler and manifest; EXTEND management and API.
This is not a new fundamental memory entity or source of truth, and the
complete facade/policy/context workflow is not implemented by those primitives.

Preserve the existing pipeline: canonical revision, declarative profile and
explicit render context, pure compiler, compiled text plus manifest. Disabling
a parent suppresses its subtree without deleting content. `required` belongs
to a particular profile, not a universal immutable-instruction guarantee;
unreachable descendants are excluded before their required-condition/variant
checks. The compiler and profile store neither authenticate nor activate a profile.

Investigate a small exact named-key facade over canonical references, not a
second authoritative KV corpus or a mandatory Markdown/RAG path:

- Scope/agent/session-qualified keys must resolve deterministically to exact
  canonical document/block revisions or appropriate existing typed values.
  Reuse `read_block` and expected-revision editing where their semantics fit.
- Define key equality, scope, alias/reference ownership, missing/deleted/stale
  targets, conflict/NoChange and retention before choosing an API. A rebinding
  and a value edit must not silently become the same operation.
- Reading a key does not authorize access, attach a block, activate a profile
  or include it in a prompt. Shared content and each consumer's attachment /
  activation ownership are separate; updates require explicit revision and
  stale-use/conflict handling.
- Keep trusted host policy distinct from agent-proposed candidates. A candidate
  cannot remove mandatory controls, alter policy or grant capabilities.
  `PromptPolicy` / `PromptCandidate` name that proposed boundary, not code to
  add now. `PromptGenome`, if needed, is a logical configuration over existing
  blocks/profiles, not a new tree, store or monolithic runtime object.
- Governed activation/evolution needs separate admission, held-out evaluation,
  provenance and rollback/replay criteria. Successful compilation or durable
  storage is not activation approval or evidence of improved behavior.

Start with an exact canonical-reference path. Add a `namespace + key` physical
index only after a measured need and a separately reviewed DBI/identity budget;
do not duplicate text to make an alias lookup convenient. Optional semantic
search over the same values must remain distinct from exact lookup.

### 4.1. Managed block operations and independent guarantees

The follow-up must specify these user-facing requirements before implementation:

- Named blocks such as `human`, `persona`, `current_task` and `preferences`
  support exact scoped-key access without semantic search. Provide read,
  replace, append, delete and revision-history operations over canonical
  content. Define deletion versus alias detachment and retention explicitly;
  an ordinary delete must not silently erase historical revisions.
- Changes use existing canonical edits and `expected_revision`, with explicit
  conflicts rather than lost updates or blind retry. Specify which canonical
  and binding revisions each operation guards; appending after an uncertain
  outcome must not silently duplicate content. History reads need their own
  access checks, not an assumption that present-day access exposes all history.
- Audit a simple API for common operations and a lower-level API for explicit
  control over references/revisions. Both reuse the same canonical history and
  governed admission; the lower-level facade is not an agent-accessible bypass.
- A block may be read-only, editable under specified authority, or protected
  against deletion/rebinding. Trusted application policy owns these rules;
  an agent-editable block or profile must not redefine its own permissions.
- Context attachment may be persistent for a declared consumer/profile,
  conditional on explicit inputs, or on demand. Define these as policy choices,
  not new compiler modes or an automatic consequence of reading a key.
- Shared content may have several consumers, but each consumer's read/write
  rights and attachment policy are checked independently. Sharing a block or
  profile grants neither access nor permission to edit it for another agent.

Mutation protection and required context presence are **independent guarantees**.
A writable block may be mandatory, and a read-only block may be optional.
Trusted host policy must check both, including changes to aliases, ancestors,
profiles and later context trimming. `PromptAssemblyProfile::required` alone
does not enforce that policy or ensure inclusion beneath a disabled ancestor.
If a required block cannot be read, resolved or included within the declared
context budget, return an explicit refusal/error and no silently weakened
context. Define the responsible byte/token budget boundary; do not bypass
authorization, silently omit the block or implicitly truncate its protected
content to make compilation succeed.

### 4.2. End-to-end scenario and freshness boundary

The minimal future scenario starts with the normalized intent "append the
user's C++17 preference to `human`", not an in-library natural-language agent
or tool executor. Resolve the scoped key, check trusted mutation policy and
the expected revision, apply a canonical edit and retain its new revision.
On the next approved context build, the host selects and pins the revision
required by its freshness/attachment policy, then invokes the pure compiler.
For this fixture, that consumer's declared policy follows the current approved
revision; independently pinned consumers remain unchanged.
The resulting manifest identifies that exact content/profile configuration.
The compiler must not read `latest`; updating a block must not silently switch
every other consumer's pinned context. Stale/missing references and denied or
conflicting updates have explicit outcomes, not fabricated successful memory.

Completion of this future capability requires the facade, trusted-policy
integration and end-to-end fixtures. Existing block/profile storage and compiler
tests are not evidence that this managed workflow already works.

## 5. Derivation / Presentation and compatibility

The central follow-up is **composability and extensibility of existing knowledge
mechanisms**, not a new catalogue of memory kinds. Preserve the representation
owner's five concerns: canonical information unit, independently addressable
derived unit, rebuildable SearchProjection, versioned materialized transformation
and physical index/codec. A derived unit may itself have projections and
descendants. "Any number of representations" is model extensibility, not
unlimited fan-out, decoding, allocation or recursion; every execution remains
bounded.

Changing output format or rebuilding an index does not create a new source of
truth. A derived summary retains exact input revisions, recipe, lineage and
publication/freshness policy; a presentation must not silently promote a claim,
discard provenance or resolve `latest` on behalf of a pinned consumer.

### 5.1. Compatibility seams to audit

The named `AspectId`/schema/recipe model is broader than the current fixed
`ProjectionKind` API. Resolve how user-defined aspects enter search through an
additive, versioned compatibility path; do not silently replace enum values,
keys or integrated interfaces. Likewise, the text-oriented, single-scope
`IDerivedPublicationStore` is not already a publisher for arbitrary structured,
multimodal or multi-corpus results. Storage `StoredMatch` is not authoritative
freshness.

The source baseline does include separate MDBX B2/B3 fenced write/current-read
paths. Their direct binding subset checks current units and root manifests in
one transaction, requiring one revision-bound SHA256 source per input unit.
This is useful reuse evidence, not proof of complete causal lineage, generic
transitive freshness or authorization. Preview-only status passages in older
guides must be reconciled against these methods, rather than repeating either
"all publication is missing" or "universal publication is implemented".

### 5.2. Derived Knowledge Processing Profiles

Audit a small runtime-neutral contract for a **Knowledge Processor** and its
processing profile. These are proposed roles, not new public C++ interfaces in
this change. The first bounded candidate must describe:

- Inputs: a source, a KnowledgeUnit or an ordered set of exact input revisions,
  selected under an authorized, pinned read frontier. Multi-context work retains
  separate frontiers and receipts, not an implied cross-context transaction.
- Descriptor: supported input/output types and schema versions, capabilities,
  recipe/version, producer implementation, model identity where applicable,
  parameters, reproducibility declaration and required resource/external
  capabilities. Reuse existing derivation/recipe identity; do not create an
  independent plugin-version namespace.
- Result: typed output with a derivation receipt binding exact inputs,
  fingerprints, coverage, omissions and errors. Preserve partial/failure
  outcomes; a producer's success claim does not establish completeness or truth.
- Disposition: admit an independently addressable derived unit/artifact,
  publish a rebuildable search projection where its lifecycle fits, or return
  the result transiently without creating new knowledge. A format choice alone
  must not choose persistence, source authority or lifecycle.

Recipe, producer implementation, model, relevant parameters, preprocessing and
output schema participate in derivation identity alongside exact inputs and
generation. Changing them invalidates reuse even when source documents have
not changed. Reproducibility claims must name their scope and retained replay
material; a digest or a deterministic-mode flag alone is not a replay proof.

Start the future API investigation with C++17 interfaces and explicit
registration. Before extracting a shared extension point, compare at least two
concrete implementations (for example extractive and host-produced summary
paths), following the existing architecture abstraction budget. Do not require
DLL loading, scripting, a workflow DSL or changes to core for every new profile.
Registration is compatibility declaration, not
trust, authorization, permission to mutate canonical content or authority to
raise epistemic status. Outputs pass normal schema, access, budget, provenance
and admission checks. Claimed provenance is not authenticated merely by a
descriptor; in-process C++ registration is not a sandbox for hostile code.
The host owns execution/isolation, model calls, scheduling, permission decisions
and resource allocation; the library owns result contracts, validation and
admission, storage, publication and retrieval enforcement within its boundary.

The minimal future vertical case is an L0/L1 summary/navigation profile over
pinned sources, using the existing publication path where applicable and
declaring gaps otherwise. Demonstrate registration, typed construction,
receipt/admission, explicit persistent or transient disposition, retrieval and
source-update behavior. L0/L1 are profile roles, not new knowledge kinds.

### 5.3. Knowledge Materialization / Presentation

A **Knowledge Materializer / Presenter** formats existing knowledge; a
processor extracts, creates or updates semantic knowledge. Wiki/Article, Graph,
Timeline and Mindmap are candidate profiles/output schemas, not new stores,
DBIs or fundamental classes. Serializing an existing relation graph is
presentation; extracting a graph from text is derivation followed by
presentation. Displaying dated events differs from inferring events or their
order. Neither chronology nor a rendered edge establishes causality.

Audit a shared result boundary with exact source/revision/frontier references,
current versus historical view, declared coverage/completeness, omissions and
failures, bounded output bytes/depth/fan-out, structured source references and
output format/schema version. A renderer cannot turn incomplete or conflicting
inputs into an unqualified complete result. Diagnostics must be policy-safe:
do not expose denied source identities, locators or counts through omissions.
Historical rendering still requires access checks, not only a revision pin.

Reuse the provenance and selection ideas owned by
[ContextPack](../context-building.md), the implemented `PromptAssemblyManifest`
and the [RetrievalTrace contracts](../knowledge-base-roadmap.md#91-retrievaltrace),
without claiming all three are installed services or creating a parallel
provenance system. Keep output contracts GUI-independent; no renderer runtime
or mandatory graph backend is introduced.

Navigation-producing profiles reuse Relation/DomainMap and the existing
[HN-0 evaluation boundary](../hierarchical-navigation-roadmap.md), with bounded
traversal and independent flat fallback. Canonical outline/subtree access or a
rendered graph is not evidence of an implemented knowledge-graph navigator.

### 5.4. Maintained Knowledge Views

Investigate maintained project summaries and answers to standing questions as
product integration over canonical blocks/`ContentChangeSet`, publication and
freshness, compaction/offline consolidation and exact multi-source provenance.
This is not a new generic cache or autonomous background runtime. An ordinary
warm read of an already materialized result should use validated storage,
without requiring another model call.

The dependency frontier is authoritative; pending refresh, stale dependencies
or unavailable inputs must have explicit outcomes. An unfinished refresh must
not relabel an old result Current; historical inspection is a separate declared
path. Publication must revalidate dependencies, so an update racing with a
refresh cannot admit stale output as current. Preserve existing receipt/retry
and crash-recovery semantics rather than inventing another job identity.

Test affected-block updates and scoped invalidation against full regeneration.
Preserve unaffected content when its dependencies are unchanged; unrelated
views must not be invalidated merely for convenience. A canonical change set
is a hint to resolve dependencies, not by itself a complete dependency proof.
When relevant exact inputs and derivation identity are unchanged, avoid
unnecessary model calls under the declared reuse policy. Recipe/model/schema
upgrades invalidate that reuse even with identical source bytes. Incremental
refresh must retain exact provenance and pass the same admission as a full
refresh; source evidence remains separately addressable, not replaced by the
view or made independent by duplication.

### 5.5. Question-Conditioned Derived Knowledge

Investigate using a shared question to produce comparable semantic aspects of
heterogeneous knowledge. **Question-Conditioned Knowledge Aspects** is the
general research direction; affordance-based retrieval or "intuition of
possibilities" is one candidate profile. Other profiles may address risks,
limitations, dependencies, failure modes, integration or commercial potential.
These are aspect/recipe roles, not new knowledge kinds or a new graph engine.
The conceptual `derive(K, Q, C)` binds exact knowledge inputs `K`, question `Q`
and relevant context `C`; it is neither an implemented function nor a
determinism or quality guarantee. Affordances depend on the actor's abilities,
task, environment and constraints, not only the object description.

The audit must resolve the following before a public or persistent decision:

- Parameterization and identity: decide which stable semantic question family
  belongs in an aspect descriptor and which normalized question/template,
  parameters and context belong in the derivation recipe. Reuse existing
  producer/model/schema/version and exact-input/frontier identity. Do not
  silently treat every free-text question as a new global `AspectId`, or two
  paraphrases as the same recipe. Relevant context must have an exact retained
  binding/replay policy; a name, timestamp or digest alone is insufficient.
- Comparability: identical question text is not a compatible semantic space.
  Compare declared aspect intent/output schema, actor/context assumptions and
  recipe compatibility, plus embedding model/revision, tokenizer/input policy,
  normalization and metric. Keep incompatible routes separate unless an
  explicit alignment/fusion policy is qualified. Similar dimensions, a shared
  label or fluent generated language do not establish compatibility.
- Cost and reuse: compare a finite set of precomputed useful aspects with
  bounded on-demand generation and optional retention/indexing of useful
  results. Record generation, candidate, byte/token, latency and storage limits;
  no all-questions-by-all-units expansion. Cache/reuse identity includes exact
  inputs, question/context, recipe/model/schema and policy bindings. Recheck
  current access and freshness on reuse; private context must not leak across
  consumers/scopes through shared results, keys or diagnostics. Adaptation to
  recurring questions is a host-governed, evaluated policy, not a new autonomous
  consolidation/activation loop.
- Epistemic boundary: separate source-supported capabilities/constraints from
  hypothetical uses, risks, causal scenarios and market demand. Preserve
  uncertainty and unknowns at the claim level. Model confidence, repeated
  derivation or similar outputs do not create validation or independent
  corroboration; reuse existing epistemic/provenance contracts, not new enums.

Similarity is an execution result or candidate analogy, not a saved `Relation`,
proof of equivalence, causal evidence, tool authority or permission to use the
mechanism. Exact dependencies, ownership and provenance remain explicit where
their owner contracts require them. This is not a claim that graphs cannot
have extensible edge types: graph assertions and approximate discovery solve
different tasks and can compose. A separate processor may investigate an
analogy and propose an evidence-bound Relation through ordinary validation and
publication; retrieval itself performs no canonical write or automatic
epistemic promotion.

The minimal future experiment compares original documentation, one shared
question-conditioned aspect and a bounded combined route on pinned project and
competitor documentation. A useful candidate analogy is PromptComposition
versus Memory Blocks for context editing/personalization; differences in
persistence, permissions, required inclusion and runtime integration must still
be exposed, not inferred away. See the
[proposed comparison and false-analogy controls](../experiments/2026-10-29-derived-representation-evaluation.md#2026-11-23-question-conditioned-aspect-follow-up-proposed).
Source verification, dataset, runner and measurements remain pending. The
supplied affordance literature is research input, not a verified novelty claim
or evidence of cross-domain transfer in this library.

### 5.6. Decision priority and disposition

These priorities order **architecture decisions**, not implementation ahead of
Conversation Core Phases 3-5. Milestone ownership remains with the existing
guides; the later audit assigns a minimal slice, dependencies and evidence gate
before any API or persistent-format decision.

| Priority | Architecture work | Existing owner / first future discriminator |
| --- | --- | --- |
| P1 | Extensible Derived Knowledge Processing | Representation and artifact/provenance owners; bounded L0/L1 profile with receipt, admission and version-aware reuse; question-conditioned aspects are a research candidate within this audit, not a new Core gate |
| P1 | Knowledge Materialization / Presentation | Representation owner plus context/trace contracts; existing graph serialization versus graph extraction with separate derivation |
| P2 | Maintained Knowledge Views | Offline consolidation, compaction and publication owners; source update makes only dependent views stale, then a fenced incremental refresh |
| P2 | Managed Memory Blocks / Keyed Memory | Canonical/prompt owners; [managed-block scenario](#42-end-to-end-scenario-and-freshness-boundary) |
| P2 | Temporal & Epistemic Semantics Audit | AM-13/T0 and runtime owners; pinned historical visibility and bounded coverage/open-question cases |

Reuse existing identity, access, publication, reverse invalidation, DBI and codec
contracts without implying their full implementation. Cognee, RAGFlow,
OpenViking and Hindsight processing/view examples supplied for this audit are
comparison candidates, not newly verified upstream capabilities. No Graphiti,
Letta or Hindsight ontology is adopted by analogy; the later comparison must
test behavior, not infer algorithms from type names.
ADELIA/EPHI contracts are architectural inputs, not verified runtime integrations.
This brief adds no external dependency, schema migration, executor or store.

| Disposition | Work |
| --- | --- |
| REUSE | Canonical blocks/editing, pure prompt compiler, profile history, existing provenance/publication and AM-13/T0/runtime-origin contracts |
| EXTEND after audit | Processing/profile registration and typed result composition, including question/context-conditioned aspect investigation; presentation contracts; maintained-view integration; time/awareness clarification; Managed Memory Blocks management/API with trusted mutation and context attachment |
| NEW only with evidence | Minimal public types or physical indexes that the existing owners cannot express; require a counterexample, compatibility/DBI budget, explicit owner approval and a bounded fixture |
| DEFER | Universal multi-temporal objects, per-format memory classes/stores, mandatory graph backend, DLL/scripting/workflow or renderer runtime, autonomous belief/evolution runtime and blanket always-visible/shared-block guarantees |

## 6. Follow-up acceptance and completion boundary

The next combined architecture task must include this brief and the completed
[AMCE vocabulary decision](https://github.com/LimiNode/agent-memory-cpp/blob/7bb88efc078f3b7c3c9a3fb49235d472bbaef717/guides/audits/2026-11-23-amce-event-vocabulary-decision.md)
rather than repeat either discovery cycle.
At this baseline AMCE remains separately reviewed in Draft PR #690, not an
integrated codec claim. These are future acceptance cases, **not passing tests**:

| Lane | Minimum discriminating case / observable result |
| --- | --- |
| Temporal | Claim validity ends March 20, correction received April 10: present and earlier pinned/receipt-qualified views differ for the declared reason; publication, visibility and belief times are not collapsed |
| Temporal | Epoch zero, unknown/open/imprecise bounds, boundary/point/overlap cases and checked ms/us conversion have explicit distinct results; compare the selected interval predicates with a small independent oracle |
| Temporal | Same source at two origins plus clock skew/late import: producer sequence grants no earlier observer visibility; fixed-frontier replay stays stable and unpinned behavior is declared |
| Awareness | Authorized evidence exists but retrieval misses it: expose a measured coverage gap, not fabricated ignorance; denied evidence leaks no existence/body/locator/count |
| Awareness | Open question resolves and reopens on conflicting/stale evidence without changing knowledge kind or silently promoting confidence to validation |
| Keyed memory | Exact scoped key resolves one canonical revision; stale concurrent update conflicts, identical/no-op behavior is declared, and no duplicate text corpus appears |
| Prompt | Reading a key does not activate it; shared-content revision change does not silently switch every consumer; a stored/compiled agent proposal cannot weaken trusted controls |
| Managed blocks | Read/replace/append/delete/history preserve revision history and check operation-specific rights; stale writes and uncertain append retries do not become lost or duplicated updates |
| Managed blocks | The C++17 preference intent changes `human` only when authorized; under the declared follow-current policy, the next approved build's manifest pins the new revision, while denied/conflicting edits leave canonical content unchanged |
| Managed blocks | Read-only and required-presence constraints vary independently; agent changes to a profile, alias or ancestor cannot disable trusted mandatory content, including during later trimming |
| Managed blocks | Required content exceeds a byte/token budget or becomes denied/missing: explicit failure/refusal, no silently weakened context or access bypass; authorized shared consumers retain independent attachment/revision decisions |
| Derivation | Re-render/reindex and stale-source update preserve lineage; rebuilt projection or summary is not independently validated primary evidence |
| Processing | An L0/L1 profile completes registration-to-typed-result/receipt/admission-to-read over pinned inputs; persistent unit/artifact, rebuildable projection and transient return are explicit, not accidental side effects |
| Processing | Identical inputs with a changed recipe, producer implementation, model, relevant parameters or output schema do not reuse the old derivation identity; the old result remains historical, not silently overwritten |
| Processing | Registration grants no source access or write authority; malformed, over-budget or inadmissible output is rejected, and claimed provenance alone cannot promote a result to validated evidence |
| Question-conditioned aspects | Different terminology but a supported shared use retrieves a candidate analogy; similar wording with incompatible preconditions is rejected or qualified, not presented as interchangeability or a validated Relation |
| Question-conditioned aspects | Change question, actor/environment context, source revision, recipe/model/schema or relevant policy: incompatible/stale/private results are not silently reused or fused; on-demand exhaustion has an explicit bounded outcome |
| Question-conditioned aspects | Original/aspect/combined routes use matched authorized sources and budgets; independent labels measure useful analogies and false analogies, including a source correction and an unanswerable or unsupported-use case |
| Presentation | Serialize an existing graph/timeline versus infer one from text: only the latter creates a derivation; exact sources, historical/current status and schema/format remain explicit |
| Presentation | Partial, conflicting, denied or budget-limited inputs yield declared policy-safe coverage/failure without leaking denied identities/counts or fabricating completeness; output size/depth stay bounded |
| Maintained views | Correct one input block: dependent views become explicitly stale/pending, unrelated views remain reusable, and a fenced incremental refresh preserves unaffected content and lineage |
| Maintained views | Repeated unchanged inputs/recipe need no new model call; warm reads are validated storage reads. Concurrent source change, interrupted publication and retry cannot expose stale output as Current or duplicate a generation |

For each proposed change deliver: implemented / contract-only / genuinely
missing evidence; normative owner and milestone/dependencies; REUSE/EXTEND/NEW
disposition; minimal slice and direct acceptance fixture; compatibility and
remaining risk. Reuse the existing evaluation roadmap and
[representation A0-A4 comparison gates](../experiments/2026-10-29-derived-representation-evaluation.md)
rather than introducing a separate test system for profiles.
Compare bounded baseline/full versus incremental work, output correctness,
provenance, authorized coverage, update/reopen and memory/latency/model-call cost;
do not infer a quality gain from successful formatting or extra model work.
Temporal/awareness research remains M2+ and must not silently
expand the current Core gates. A smaller keyed facade can be proposed separately
after its contract is settled, without claiming governed activation is complete.

Pin upstream revisions and primary references when comparative research is
actually performed. Allen relations, clock models, AGM and competitor mappings
are research candidates here, not verified conclusions. If a new access path is
proposed, compare with exact canonical/range-read baselines under matched scope,
frontiers and bytes, including memory, latency and update/reopen cost. Semantic
correctness and authorization precede a performance claim.

Completion now is recording these constraints/questions and linking their
owners. Full comparative audit, new executable fixtures, implementation and
persistent-format decisions remain open. No codec freeze, migration or runtime
activation is approved by this document.

## 7. Ontology-neutral derived knowledge: differential audit

2026-11-23 follow-up at the same source baseline and the prior audit commit
`ac16c58fa9ed7984b273b54525aa9a6be107d9a2`. Documentation only: no C++, DBI,
wire/schema encoding, migration or production behavior changes. Sections 1–6
remain applicable. PR #690 and dependent #694 are separate Draft work; this
audit neither merges them nor changes their acceptance or exact HEADs.

### 7.1. Existing mechanisms and genuine gaps

The [representation owner](../derived-representations-and-codecs.md#ontology-neutral-structured-representations)
separates infrastructure invariants from optional domain ontologies. A
represented object can be a physical thing, state, relation or distinction;
distinctions may be inputs to later distinctions. Rested-Peter and tired-Peter
are distinguishable state representations, not necessarily two people. This
permits recursive derived knowledge without proving that every experience or
relationship is inherently binary. Binary encoding, relation arity and semantic
equivalence are independent questions. No mandatory binary/actant/SPO ontology,
`Distinction` kind or new foundation is adopted.

Evidence labels below have the meaning in §1.1. **SPECIFIED** includes the
explicit future-contract refinements in this change; it never means implemented.
REUSE/EXTEND describe design disposition, not an implementation authorization.

| Requirement | Differential evidence and actual gap | Disposition / normative owner |
| --- | --- | --- |
| Optional schemas and alternative ontologies | SPECIFIED aspect/schema/recipe identities and [optional N-ary facts](../knowledge-base-roadmap.md#optional-typed-relational-facts) predate this audit; MISSING general installed registry/SPI. Binary `Relation` is one graph projection, not a universal arity limit | REUSE identity/recipes; EXTEND [representation owner](../derived-representations-and-codecs.md#ontology-neutral-structured-representations); DEFER API freeze |
| Typed nested elements and stable references | IMPLEMENTED text blocks/IDs/revisions and revision-qualified navigation; also bounded [table/media values, validators and TL1 locators](../../src/agent_memory/domain/TypedRepresentation.hpp). SPECIFIED broader artifact/table anchor contracts; MISSING generic schema-typed element bodies, edit/provenance rules, structured persistence and general hydration | REUSE stable-block/typed-locator principles; EXTEND composite contract, not current block wire format |
| Internal element versus independent unit | SPECIFIED canonical-body/lifecycle/access/citation criteria; MISSING governed promotion workflow | EXTEND [composite element criteria](../derived-representations-and-codecs.md#composite-elements-and-independent-units); no unit per field |
| Containment, semantics, identity, similarity, derivation | SPECIFIED distinct channels and acyclic technical lineage; semantic links may cycle. No automatic mapping between these meanings is implemented | REUSE [linked channels](../linked-derived-knowledge.md#relation-classes-and-traversal-channels); EXTEND explicit interpretation boundaries |
| Several related outputs from one processor | IMPLEMENTED one text body+manifest+marker in [B1 API](../../src/agent_memory/storage/IDerivedPublicationStore.hpp); bounded direct-unit/root B2/B3 fences. SPECIFIED many-input/single-output; MISSING output-set publication and shared atomic visibility | EXTEND [multi-output owner](../linked-derived-knowledge.md#multi-output-structured-publication-future); DEFER physical coordinator design |
| Local/global/role-aware retrieval | IMPLEMENTED unit/text-projection identity, not generic element/role/parent results; SPECIFIED structured route and row/cell anchors. MISSING composite route/index/hydration | EXTEND [structured owner](../structured-data-retrieval-roadmap.md#composite-local-and-role-aware-retrieval); locality is not access scope |
| Historical schemas and incompatible interpretations | SPECIFIED recipe/schema identity, immutable historical generations and Current admission; MISSING general structured schema evolution/conformance fixtures | REUSE publication/history; EXTEND explicit separate interpretation/index identities; no reinterpretation of old bytes |
| Missing, unknown, negated, unsupported | SPECIFIED runtime epistemic and typed-provider outcome distinctions; MISSING general schema-level epistemic validation | REUSE [runtime owner](../agent-runtime-integration-roadmap.md#awareness-and-open-question-audit-m2); EXTEND future negative cases, not a closed-world reasoner |
| Relative time, claimed cause, intended purpose | IMPLEMENTED timestamps do not resolve expressions; SPECIFIED AM-13/T0 and causal evidence. MISSING relative-expression interpretation path | EXTEND [AM-13](../memory-lifecycle-governance-roadmap.md#relative-expressions-and-interpretation-m2) and AM-15; retain unresolved/uncertain evidence |
| Entity/state identity, modality and observer | SPECIFIED AM-19, artifact representations and perspective/scope separation; MISSING generic verified entity-resolution behavior. Matching labels or modalities is not identity | REUSE [artifact/provenance](../artifact-provenance-roadmap.md#34-representation), [perspective](../agent-runtime-integration-roadmap.md#perspectivecomponent) and canonical identity layers; DEFER a separate Symbol Grounding subsystem |
| Question-conditioned knowledge | RESEARCH existing §5.5; MISSING demonstrated functional/affordance route. Same question/embedding is not equivalence or independent evidence | REUSE aspect/recipe/provenance; EXTEND context/criteria/perspective identity; DEFER default questions or automatic Relation creation |
| Cross-query residual activation | RESEARCH, MISSING implementation and measured utility; AR0 remains search-local and is not proof of this capability | EXTEND [activation research boundary](../knowledge-activation-roadmap.md#optional-cross-query-residual-activation); DEFER adoption pending cache-controlled evidence |

Source caveat: the legacy `MdbxKnowledgeUnitStore` is Chunk-oriented, but the
opt-in [typed envelope store](../../src/agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitTypedStore.cpp)
also persists non-Chunk envelopes (the
[fixture](../../tests/infrastructure/mdbx/mdbx_knowledge_unit_typed_store_test.cpp)
includes Fact). Do not generalize the legacy limitation into “no non-Chunk
persistence”. Neither envelope persistence nor the guarded experimental `Custom`
contract demonstrates general typed semantic payloads, schema validation or a
production registry. Likewise, `SearchProjection`'s fixed `ProjectionKind` and
text fields do not implement arbitrary typed nested elements.

### 7.2. Bounded demonstration profiles, not delivered implementations

These are future opt-in examples over the same contracts, not five new core
kinds, APIs or implemented plugins. They must leave ordinary memory and
retrieval working when disabled.

| Profile | Minimum future example and discriminating invariant |
| --- | --- |
| Binary distinction/opposition | Compare rested/tired states of Peter under a declared criterion; distinguish person/state identity, contextual/incomparable outcomes and explicit negation from unknown; a later distinction may cite the first at an exact revision |
| N-ary frame / actant roles | Two giving events retain separate occurrence IDs and Actor/Object/Recipient bindings; role swaps change the match without mixing participants across events. Reuse the existing N-ary proposal |
| Question-conditioned affordance/risk | Derive uses, risks, value or criterion-fit for an actor/task/environment; retain exact question, criteria, perspective, source, recipe/model/schema and context, with hypotheses separate from validated support |
| Structured event / relative time | Preserve “last week”, its source and optional anchor/timezone/calendar/quoted context; no absolute interval is invented when the anchor is missing; intended heating and observed outcome remain distinct |
| Alternative ontologies for one source | Frame, distinction and another schema coexist with independent projections and pinned versions; schema v2 cannot change v1 history or duplicate source evidence into corroboration |

The demonstration obligation is extensibility **without core enum, canonical
wire or mandatory backend changes**, not proof of absolute universality.
Before claiming that obligation satisfied, a later implementation must add a
new schema through a documented bounded capability seam and run independent
schema fixtures. The [SPI checklist](../derived-representations-and-codecs.md#extension-seam-before-api-stabilization)
names only the minimum unresolved capabilities; no interface is frozen here.

### 7.3. Acceptance, dependencies and implementation order

The [proposed study matrix](../experiments/2026-10-29-derived-representation-evaluation.md#2026-11-23-ontology-neutral-structure-and-activation-follow-up-proposed)
owns BASELINE/variant/metrics/limitations and NOT RUN status. Minimum future
acceptance includes role swap; one parent's multiple role matches; exclusion
of local-only elements from global discovery; exact role-aware parent and
anchor hydration; independent parallel ontology indices; immutable historical
schemas; stale Current rejection; explicit partial/missing inputs without
fabrication; no duplicate evidence from embeddings/modalities; unresolved
relative time; and unchanged ordinary retrieval with profiles disabled.
Interrupted multi-output publication, element reordering and identity retention,
unknown schemas and observer/task reset require additional negative fixtures.
These are documentary scenarios, **not passing executable tests**.

| Order / milestone | Dependency and minimum later slice | Acceptance / cost gate and risk |
| --- | --- | --- |
| Decisions before derived-schema/SPI stabilization (M2+ extension) | Resolve element namespace/edit/reference rules, promotion, schema/version/unknown behavior, role mapping, output-set atomicity and policy/freshness bindings with existing owners | Independent semantic fixtures for at least two materially different schemas; reject any need to silently reinterpret a historical record. Risk: freezing semantics behind an opaque payload |
| Opt-in structured publication and retrieval (later M2+ profile) | Implement one bounded structure and declared output set over existing publication/admission, then role-aware local/global read/hydration; new public types only with a concrete compatibility gap | Crash/reopen/retry and stale/access/partial tests; compare container reads, exact structural indices, lexical and optional role-conditioned dense routes for index bytes, peak memory, latency, update/fan-out cost. Risk: combinatorial indexing and false freshness |
| Optional schema demonstrations and richer interpretations (later M2+) | Qualify the five examples, schema evolution and independent indices, using artifact/AM-13/runtime owners | Same-source evidence deduplication, historical interpretation and unresolved-time fixtures; provenance and cost before semantic quality claims. Risk: conflating storage with understanding |
| Stateful activation research (M2+, separately opt-in) | Stateless baseline and recent-query cache before residual state; host owns observer/task binding and reset | Held-out matched-budget benefit, drift/interference, task-switch isolation, stale/denied inputs, bytes/lifetime bounds; DEFER if benefit is absent. Risk: query drift and authority laundering |

This decision sequence does **not** block Conversation Core Phases 3A–6.
Phases 3A–5 retain their existing storage/Core gates; Phase 6's Derived Knowledge
Bridge uses exact primary/derived bindings and freshness without requiring a
universal schema system or residual activation. No immediate kernel rewrite,
graph backend, new source of truth or DBI is justified by this audit.

Disposition summary: **REUSE** identity, canonical blocks, artifact anchors,
recipes, admission/publication, AM-13/T0 and perspective; **EXTEND** the owner
contracts for composite elements, optional ontologies, output sets and role-aware
retrieval; **NEW** only a later minimal type/index/SPI backed by a counterexample
and compatibility evidence (none introduced now); **DEFER** API/physical format
freeze, universal ontological claims and stateful activation adoption.
External monograph/linguistic claims in the supplied discussion have not been
verified and are not used as evidence. Completion here is the documented audit,
not implementation, schema qualification or a claim of philosophical proof.
