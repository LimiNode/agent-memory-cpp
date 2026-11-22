# Conversation/Event Model: proposed domain contract

- Status: **Draft / proposal, no public C++ API or event store implemented**.
- Date: 2026-11-22. Scheduling: planned early opt-in Conversation Core;
  advanced conversation memory remains M2/M2+ under [milestones](milestones.md).
- Baseline inspected: `main@acd5ad5545386b3feeaa79f8a587855b60801b4d`.
- Prerequisite: approved standalone AMXR v1, with integration still pending.
- Owner: conversation/session domain; rationale:
  [conversation/event ADR](adr/2026-11-conversation-event-identity.md).

This proposes the next contract after the Identity integration gate. It does
not approve a runtime implementation, change legacy chat values, or add another
identity codec. AMXR is implemented in the prerequisite stack; **the event
model below is not**. A separate assignment and review must authorize each
implementation slice. The event-versus-representation-revision baseline in
section 2 is approved as the basis of Phase 1 domain values; that approval
does not freeze an event codec, import/store schema or allocation protocol.

## 1. Ownership and canonical entities

Keep domain stores separate, as required by [canonical content, section 2.2](canonical-content-storage-roadmap.md#22-conversation--session).
Do not flatten a conversation into a mandatory document/string or make
`ConversationEpisodePayload` its source of truth.

This is a domain-specific canonical source model, **not a second universal
memory system**. Reuse identity, exact revisions, provenance/publication,
access and storage composition under their existing owners:

| Boundary | Owns | Must not become |
| --- | --- | --- |
| Conversation / Entry history | Communication occurrences, revisions, edits, replies, branches, actors, ordering and observed tool events | A replacement knowledge envelope, execution engine or generic graph store |
| `KnowledgeUnit` | Independently retained notes, assertions, facts, semantic relations and derived knowledge | An obligatory copy of every message or reply link |
| Publication / provenance | Exact inputs, recipe, frontier, generation and admission of a derived output | A second conversation-only lineage system or an adjacency row |
| `SearchProjection` / indexes | Rebuildable lexical, dense, multi-aspect and optional graph navigation views | Canonical evidence, source identity or relation truth |

One source occurrence has one authoritative canonical history. Many derived
knowledge objects and indexes may depend on it; none becomes an independent
source merely by copying or summarizing its content. Artifact retention does
not create a competing conversation journal.

Terminology is by responsibility: **Entry** is a journal occurrence; a
conversation change is a typed edit/delete/restore occurrence. The sketch's
`EventRecord` and `EventRevision` name that journal surface only.
`KnowledgeUnitKind::Event` instead describes an assertion about a domain event
(for example, somebody's arrival); it is neither a journal change nor its ID.
`KnowledgeUnitKind::Relation` owns a semantic assertion, not every `reply_to`,
`edit_of`, `branch_of` or `tool_result_of` link. Existing enum values and legacy
chat types remain unchanged; final public naming is reviewed with slice 1.

Keep three logical graphs distinct: provenance is an acyclic graph of exact
derivations; semantic relations may contain cycles; an execution graph records
operations of an external runtime such as ADELIA. Conversation stores observed
calls/results and causal evidence, never orchestration or execution policy.
Low-level relation machinery may be reused where its invariants match, but
neither semantic GraphEdge materialization nor a graph backend is mandatory for
canonical history or technical lineage.

| Entity | Proposed invariant |
| --- | --- |
| Conversation | Stable conversation/session identity; not a display name, channel label, import run or branch |
| Entry | One registered source-event occurrence or evidence-qualified local observation within a conversation; identity survives corrections to its recorded representation |
| Event revision | Immutable exact representation of one Entry; `(allocation namespace, conversation, entry, revision)` is the full reference |
| Actor | Participant/source of an action; independent of display aliases, LLM role, observer and authorization |
| Relationship | Explicit reply, branch, topic, forward, quote or causal link; exact target revision or explicit unresolved evidence |
| External reference | Existing `ExternalObjectRef` and AMXR, under the [identity policy](identity-policy.md); not a new provider-specific DTO |
| Attachment | Existing immutable artifact binding selected under [artifact provenance](artifact-provenance-roadmap.md), not mandatory inline text |

An actor may be human, service, bot, application or unknown. An optional LLM
role describes the recorded message's role, not who the actor is or what the
reader may access. Alias observations retain issuer, observation time and
source evidence. Two observer accounts can observe one shared source event;
they do not thereby create two source identities.

Observation/import records reference an exact event revision and separately
record observer, import receipt, adapter/recipe and observation time. They are
not identity fields of that event. Scope, actor, role, membership and observer
are not interchangeable permission tokens.

## 2. Proposed event/revision decision

**Baseline approved for Phase 1 domain values:** a message creation, upstream
edit, deletion, restore or moderation action is a distinct Entry when it is a
distinct source event. An edit/delete/restore event targets the exact prior
event/state it affects; it does not overwrite or erase the creation event. A
current-message view is a deterministic fold/projection over these canonical
facts.

An Event revision instead corrects the stored representation of the **same**
source-event occurrence or local observation, for example an explicitly
reviewed normalization correction. Such a correction records its old/new
adapter recipe, evidence and reason, uses an expected revision and publishes
a new immutable revision.
Replaying unchanged evidence is `NoChange`; silently rewriting a revision is forbidden.
This distinction does not change document-edit semantics. Snapshot-only
observations follow section 3: observing a current message state is not proof
of its creation or of an intervening edit, deletion or restore. A complete
upstream event journal is not a requirement for importing such evidence.

| Typed event | Minimum validity / provenance |
| --- | --- |
| Message creation | Text, attachment or both; empty text is valid when the typed content is otherwise meaningful |
| Message snapshot observation | Exact retained import evidence for the observed message state; no assertion that creation or any unknown change event was observed |
| Edit | Exact affected event/state plus replacement content and source edit evidence |
| Delete / tombstone | Exact affected event/state and deletion evidence; not physical erasure of history |
| Restore / moderation | Exact affected event/state, explicit action and source evidence; restore identifies the retained state being restored |
| Attachment | Non-empty immutable artifact-binding list; no fabricated text body |
| Tool call / result | Observed request/result artifacts; a result binds its exact call or retains an unresolved call reference |
| Command | Observed request and any available result evidence; does not execute the command |
| Patch | Patch artifact, exact input/output file revisions when known, and application/observation evidence; unknown application outcome remains unknown |
| Model / configuration change | Exact configuration artifact and affected session context; not an inference instruction |
| Compaction / summary | Derived output plus exact source events/revisions, recipe and publication/provenance receipt; never independent source evidence |

Missing or unsupported provider payloads may remain artifacts with an adapter
descriptor. They must not be guessed into a known event kind. A textless
administrative record is valid according to its payload, not the legacy
`Message` non-empty-text rule. No tool execution, scheduler or agent runtime is
part of these records.
An observed compaction/summary Entry records that a derivation happened; it
does not promote generated content to original evidence or replace its inputs.

## 3. Identity, import, history and order

Proposed allocation profile: a durable host/store-assigned allocation namespace
owns conversation and actor sequences; each conversation owns its Entry
sequence. IDs are non-zero strong u64 values, monotonic within that declared
domain. Event revision numbers are non-zero and monotonic per Entry. Overflow
fails; committed identities/revisions are never reused or rebound after
tombstoning, retention or reopen. Rolled-back tentative values are not durable
assignments. No allocator or persistence is implemented by this proposal.

Portable local references retain the allocation namespace. Import may remap
local occurrence IDs explicitly but preserves complete external identity,
selected source revision, artifact bindings and provenance. Physical table
keys are separate. Neither text nor timestamps are automatic IDs.

Keep four identities separate:

| Identity | Meaning and limit |
| --- | --- |
| External message/object | Provider-qualified message identity, expressed by existing AMXR; it may survive many source changes and does not identify each change |
| Observed source change | A distinct upstream occurrence, identified only when an explicit event key or a reliable revision marker and its reviewed adapter recipe establish that identity |
| Local journal Entry | Allocation-qualified local occurrence/observation; a local Entry ID is not a portable provider event ID |
| EventRevision | Exact representation revision of that same local Entry; neither a new upstream occurrence nor a fabricated provider revision marker |

An adapter declares its source-event or local-observation key recipe, revision
rule and deduplication boundary before idempotent import. Where a reliable
source-change identity exists, the full namespace-qualified key and selected
source revision identify the source fact; the normalization recipe additionally
identifies the representation. AMXR parent/type/domain rules are reused, never
inferred from a numeric value. An external message ID alone is insufficient
to distinguish its creation and individual edits.
A changed normalization recipe does not allocate a competing source Entry; it
requires an explicit representation correction with an expected revision.

A snapshot-only provider may expose a message ID and mutable current content
but no independent event ID or reliable revision marker. Record an explicitly
local observation of that state, bound to an exact retained import artifact
and stable record locator under the adapter recipe. Its observed message ref
may be known while its source-change identity is absent. `SourceRevisionId`
selects exact retained evidence; it must not be presented as a provider-issued
change/revision token when none exists.
Retained evidence may be a normalized record artifact under the existing
artifact/provenance contract; this does not require retaining a whole provider
archive or reconstructing a complete upstream journal.

Such a key can deduplicate a replay of the **same retained observation**, not
prove global event equivalence across archives, observers or polls. State the
limited boundary; separate observations with matching text/time are not proof
of one upstream event. Do not synthesize a portable event identity from text,
content digests, source/observation timestamps or an observer account. Do not
infer unobserved creation, edit, delete or restore events from snapshots; keep
unknown history explicit rather than inventing a complete source journal.

- Same source event/revision, recipe and canonical content: idempotent replay,
  no new Entry/revision; a new observation receipt may be recorded separately.
- Same declared key/revision/recipe but conflicting payload or target: explicit
  conflict/quarantine with both evidence records, not last-writer-wins.
- Missing external event identity: a local observation with exact archive/import
  artifact and record locator may support the declared **local import** key
  above. Without a stable evidence-qualified key, report deduplication as
  unresolved instead of merging on text or time. No Entry need represent a
  source change that the available evidence does not establish.
- Incomplete archives and unresolved parents remain explicit. Later resolution
  is a recorded reconciliation; it cannot silently alter historical source
  evidence or substitute the latest target revision.

Source time and observed/import time are separate, optional facts with declared
clock, precision and unit; unknown source time remains unknown. The proposed
normalized display field is signed UTC Unix microseconds only when exactly
representable, with raw source evidence retained. Lossy rounding or overflow
must not fabricate a timestamp. This display field never defines identity or
causal order.

The baseline canonical stream order is ascending admitted Entry ID within the
conversation; selecting another exact revision does not reinsert that Entry.
Source ordering and causal relationships are separate evidence; late import
never rewrites an already-published order. A source-time/thread view names its
own ordering recipe.
Admission must finalize IDs/order so a lower Entry ID cannot commit behind an
already published higher one. Tentative reservations are not committed stream
positions; the store slice must test this invariant under concurrent admission.
Causal predecessors cannot be inferred from clock order. Unresolved edges stay
unresolved; contradictory/cyclic causal evidence is flagged, not repaired by
inventing an event. Quote/forward navigation may contain cycles and must be
bounded independently.

## 4. Proposed C++17 value surface and usage

The following is a **signature/value sketch, not an installed header**. Local
strong ID wrappers and validation belong to the first future slice, not a new
universal identity layer. Existing artifact and external-reference types are
reused. Mutable DTO construction does not admit a record without validation.

```cpp
namespace conversation_proposal {
struct ConversationId { std::uint64_t value = 0; };
struct EntryId { std::uint64_t value = 0; };
struct ActorId { std::uint64_t value = 0; };
struct EventRevision { std::uint64_t value = 0; };
struct ConversationRef { std::string allocation_namespace; ConversationId id; };
struct ActorRef { std::string allocation_namespace; ActorId id; };
struct EventRef { ConversationRef conversation; EntryId entry; EventRevision revision; };
struct UnresolvedTarget {
    std::optional<agent_memory::ExternalObjectRef> external;
    agent_memory::ArtifactBindingId evidence;
};
using Target = std::variant<EventRef, UnresolvedTarget>;
enum class LinkKind { Reply, Branch, Topic, Forward, Quote, Causal };
struct Link { LinkKind kind; Target target; };
enum class LlmRole { System, Developer, User, Assistant, Tool };
struct Content {
    std::optional<std::string> text;
    std::vector<agent_memory::ArtifactBindingId> attachments;
};
struct MessageCreated { Content content; };
struct MessageSnapshotObserved { Content content; };
struct Edited { EventRef affected; Content replacement; };
struct Tombstoned { EventRef affected; };
struct Restored { EventRef affected; EventRef retained_state; };
struct Moderated { EventRef affected; agent_memory::ArtifactBindingId action; };
struct AttachmentObserved { std::vector<agent_memory::ArtifactBindingId> items; };
struct ToolCallObserved { agent_memory::ArtifactBindingId request; };
struct ToolResultObserved { Target call; agent_memory::ArtifactBindingId result; };
struct CommandObserved { agent_memory::ArtifactBindingId request; };
struct PatchObserved {
    agent_memory::ArtifactBindingId patch;
    std::optional<agent_memory::SourceRevisionId> input;
    std::optional<agent_memory::SourceRevisionId> output;
};
struct ConfigurationChanged { agent_memory::ArtifactBindingId configuration; };
enum class DerivedKind { Compaction, Summary };
struct DerivedObserved {
    DerivedKind kind;
    agent_memory::ArtifactBindingId output;
    agent_memory::ArtifactBindingId publication_receipt;
};
using Payload = std::variant<MessageCreated, MessageSnapshotObserved, Edited,
    Tombstoned, Restored, Moderated, AttachmentObserved, ToolCallObserved,
    ToolResultObserved, CommandObserved, PatchObserved, ConfigurationChanged,
    DerivedObserved>;
struct SourceBinding {
    std::optional<agent_memory::ExternalObjectRef> message;
    std::optional<agent_memory::ExternalObjectRef> event;
    agent_memory::SourceRevisionId revision;
    agent_memory::ArtifactBindingId evidence;
    std::string record_locator;
    std::string adapter_recipe;
};
struct EventRecord {
    EventRef ref;
    agent_memory::ScopeId scope;
    std::optional<SourceBinding> source;
    std::optional<ActorRef> actor;
    std::optional<LlmRole> llm_role;
    std::optional<std::int64_t> source_unix_us;
    Payload payload;
    std::vector<Link> links;
};
struct ValidationIssue { std::string field; std::string reason; };
std::vector<ValidationIssue> validate(const EventRecord& record);
} // namespace conversation_proposal
```

Usage shape for a future attachment-only local session event (not executable
with today's installed library, and not an allocator or write operation):

```cpp
using namespace conversation_proposal;
EventRecord event;
event.ref = {{"fixture-store-a", ConversationId{7}}, EntryId{12}, EventRevision{1}};
event.scope = agent_memory::ScopeId{"fixture-scope"};
event.actor = ActorRef{"fixture-store-a", ActorId{3}};
event.payload = AttachmentObserved{{agent_memory::ArtifactBindingId{"fixture-attachment@1"}}};
// No message text or LLM role is required for this event kind.
const auto issues = validate(event); // Future API; requires the value-types slice.
```

Validation rejects zero/unqualified IDs, an unspecified scope, invalid external
references, empty required artifact bindings, invalid UTF-8 text, invalid/empty
required source-binding fields and payload/target mismatches. A local field
validator cannot prove that an artifact, actor or exact target exists, is authorized, belongs to the
selected revision/frontier, or that an allocation committed. The later
admission/read boundary performs those checks; validation is not authority.
Conflicting duplicates are also an admission/import check, not a property a
single-record field validator can establish.
Scope uses the existing ScopeId bytes/contract; it neither supplies an external
namespace nor changes legacy ScopeId validation. In the sketch,
`DerivedObserved::publication_receipt` is an artifact carrier for a referenced
publication receipt, not the authoritative admission marker. The publication
owner must settle its typed binding and validate the canonical manifest before
serialization/store implementation; artifact presence alone cannot admit it.
Unresolved targets require retained evidence and never pass as resolved facts.
The sketch's edit/delete/restore/moderation payloads require exact affected
EventRefs. An incomplete import missing that state remains unresolved import
evidence, not an admitted typed mutation with a zero or guessed target. Later
reconciliation records an exact revision rather than substituting latest.
No new wire grammar, digest recipe or JSON format is assigned by this sketch.
`SourceBinding::message` and `event` are independent optional references: the
former never fills in the latter by default. The required evidence/revision
and recipe-qualified record locator retain the exact observation when no
source-change identity is available. The locator has no public interchange
grammar here; codec/store review must settle its bounds and resolution rules.
`MessageSnapshotObserved` records available state without claiming a source
creation/change event or complete history. Local validation can check these
fields, but cannot prove the adapter's identity guarantees or global dedupe.

## 5. Bounded reads and retrieval projections

Proposed readers select exact revisions or explicitly request a current view.
They must bound **entries, decoded inline bytes, visited records, traversal
depth and fanout**. Artifact-body materialization has a separate byte budget;
a small binding must not authorize an unbounded body read. Reject invalid or
overflowed limits; no sentinel means unlimited traversal. Stop before a record
exceeds the remaining budget and report partial/oversized-item status rather
than silently claiming completeness.

A continuation binds store/allocation identity, conversation, scope, selected
canonical frontier, ordering recipe, seek position and relevant access-policy
identity. Validate every field and the retained snapshot before continuing.
Wrong scope/store/order, expired/unavailable frontier, malformed cursor or
changed access cannot silently resume at latest. Pagination/reopen support and
cursor encoding require the store slice's separate conformance tests. Denied
records and target identities do not leak through normal pages, counts or trace.

ConversationEpisode, window, summary and search representations retain exact
source Entry/revision inputs, selected frontier, scope, derivation recipe and
publication receipt. [Knowledge-unit episodes](knowledge-units-roadmap.md#55-conversationepisodepayload)
remain derived retrieval views, never canonical conversation history. An
independently retained Summary, Relation or ConversationEpisode is a derived
`KnowledgeUnit` with its own lifecycle; its `SearchProjection` is a separate
rebuildable view, as defined by [derived representations](derived-representations-and-codecs.md#generic-representation-model).
This proposal does not rewrite the existing episode fields or DBIs. Any future
adapter must resolve those locators to exact canonical event revisions before
claiming source-backed completeness. A summary is not a second canonical store.

Source update may make a current projection stale while its historical input
binding remains valid. Missing retained evidence, historical validity,
freshness and publication state are distinct, as in
[linked-derived knowledge](linked-derived-knowledge.md). Technical lineage and
navigation can operate without a graph backend.

### 5.1. Evidence and reverse dependency contract

Reuse the [canonical manifest binding](linked-derived-knowledge.md#authoritative-revision-binding),
not a second list of truth in conversation rows. The event-source adapter must
bind a complete allocation-qualified Entry/revision reference to an exact
`SourceRevisionId` and evidence anchor, retaining source identity and recipe.
It must resolve that binding after reopen/remapping without a numeric-ID cast,
latest-revision substitution or fabricated Resource revision. The existing
publication API does not yet accept Entry references: that extension and its
trusted event frontier need review before event-derived admission is claimed.

Two bounded operations are required for the later derived-knowledge bridge:

- **Evidence drill-down:** from an output revision/publication, open its exact
  Entry/source revisions and anchors, recursively for derived inputs.
- **Reverse dependencies:** from an exact Entry/source revision, find dependent
  publications and their output units/projections; an explicit current-source
  query also identifies affected active generations. Results are deterministic,
  reopen-stable and scope/access checked before identities or text are exposed.

Both operations use the same canonical manifest. An optional reverse index is
a rebuildable acceleration, not a second provenance authority; graph-off must
work. Bound records, bytes, depth, fanout and time; an incomplete index or
exhausted budget reports `Partial`/`Unavailable`, not a complete empty result.
Reverse invalidation can schedule affected work, but current freshness still
requires the authoritative read-time frontier/witness check. An interrupted
reverse walk must never admit stale output as `Current`.

Example: Entry 183@1 says "Petya is Valentina's grandson". A derived Relation
unit may assert `grandchild_of(Petya, Valentina)` with exact participant
identities, direction, extraction recipe and a publication binding to that
Entry revision. The source proves **that somebody asserted kinship**, not that
kinship is true. Preserve the existing epistemic/trust contract rather than
promoting extraction confidence to fact. Search may nominate this unit, but a
kinship answer checks typed roles and hydrates permitted evidence.

An edit affecting Entry 183@1 advances the affected message-state frontier even
when the old Entry revision itself is unchanged. A publication presenting that
assertion as current support is then stale; an explicit historical account
remains inspectable under retention/access policy. The bridge must distinguish
those requests rather than checking only whether Entry 183@1 still exists.
Independent supporting sources and contradictions remain separately visible.
Do not delete a whole claim merely because one support changed, or silently
drop that input from its old manifest: reassessment publishes a new revision
with an explicitly selected support set.

Aspect vectors such as `actor`, `relationships` or `goal-analysis` follow the
existing descriptor/recipe/model contract. They are rebuildable search
projections, never a relation type, participant identity or evidence of truth.
Multiple aspects do not allocate multiple canonical source occurrences.

## 6. Integration boundaries and staged implementation

The [canonical publication contract](canonical-content-storage-roadmap.md#7-mutable-updates-and-readmaterializeedit)
owns coarse optimistic publication and no-op/conflict behavior. Event admission
must preserve its meaning without assuming that existing document APIs already
accept EventRecord. A future event source/frontier adapter must explicitly join
the existing publication/provenance substrate. This proposal supplies no new
coordinator, generic database/transaction interface or cross-context atomicity.
[Backend integration](storage-backend-integration-roadmap.md) retains connection,
transaction provenance, lifetime, reopen and recovery ownership.

Lexical/exact-semantic integration owners retain metadata authority,
authorization, eligibility and a shared canonical read frontier under
[retrieval execution](retrieval-execution-roadmap.md#hard-constraints-and-the-filter-frontier).
Event source/actor/time metadata must come from the authoritative selected
event revision, not from stale search rows. An event snapshot token is not
automatically a shared retrieval frontier or an access grant. Define and test
that bridge with its owner; do not solve it by a side-channel event store or
TypedMetadata implementation. No changes to research PRs #676–679 or search
integration PR #683 are part of this proposal.

### 6.1. REUSE / EXTEND / NEW

This matrix distinguishes available code in the prerequisite stack from
contract-only reuse. It does not claim those branches are integrated into main.

| Action | Types / storage / indexes | Status and boundary |
| --- | --- | --- |
| REUSE | `ExternalObjectRef` / AMXR, `ScopeId`, source/artifact bindings | Value/codec code exists in the prerequisite stack; source/anchor/retention semantics stay with artifact provenance. No new external ID grammar or implicit legacy conversion |
| REUSE | `KnowledgeUnit`, `SearchProjection`, access and representation contracts | Existing envelope/projection code is narrower than the complete roadmap. No new universal memory envelope, aspect registry or embedding system here |
| REUSE | `MdbxStorageContext`, transaction provenance and numeric-mapping patterns | Backend code exists; document/block mapping APIs are not Conversation/Entry APIs and cannot be reused by casting their IDs |
| EXTEND | Canonical publication/provenance and trusted frontier adapters | B1/B2/B3 code validates KnowledgeUnit/root Resource bindings, not event history. Add exact Entry/source binding and bounded direct/reverse lookup with their owners; do not claim arbitrary transitive/event support already works |
| EXTEND | Numeric mapping and allocation composition | New conversation/entry/actor domains need reviewed mapping/sequence/profile deltas. Do not alter AMSI document/block records, DBIs, key comparators or old bytes in place |
| NEW | Conversation/Entry/Actor values, typed changes and event codec | Domain-specific invariants and a separately approved bounded format, not another generic identity layer |
| NEW | Canonical history/import store and required lookup indexes | Exact/current history, AMXR source-event dedupe, order and reply/thread access; numeric MDBX persistence under the existing physical manifest owner, not a new generic GraphEngine |
| EXTEND | Episode/window/summary and optional semantic/index adapters | Existing knowledge/projection contracts; implement only after exact-source admission. Semantic Relation views follow their own graph capability, never a prerequisite for the core journal |

### 6.2. Small-PR implementation order

Conversation Core is a **planned opt-in chat profile** for chat importing and
Efi dialogue memory, ahead of further experimental search expansion. It needs
the qualified identity/canonical-storage/backend prerequisites, not all of
M1a/M1b/M1c or the whole M2 feature set. Its normative scheduling and release
gates are in [milestones](milestones.md#early-opt-in-conversation-core-planned).
The sketch above describes a broader event vocabulary; the first Core path
need not implement every tool/development-session payload.

| Sequential PR | Minimum implementation and acceptance gate | Principal risk |
| --- | --- | --- |
| 1. Values / validation | Use the approved event/revision baseline; add C++17 Conversation/Entry/Actor values, exact references, message/change and reply/thread values, optional AMXR bindings, evidence-qualified snapshot observations, textless example, invalid-input and unchanged legacy tests | Ambiguous identity/target, invented source history or field validation mistaken for admission |
| 2. Event codec | Separately approve versioned framing, tags, limits and source-anchor mapping; exact goldens and malformed/oversized controls. AMXR is nested external identity, not the event format | Prematurely freezing a persistent format |
| 3. Reference store / import | Backend-neutral exact/current history, actor/role observations, message/change fold, expected-revision conflict/NoChange, deterministic idempotent import and bounded stable-frontier paging | Losing history, replay collisions or mixing snapshots |
| 4. Numeric MDBX history | Reuse backend context; review physical manifest/DBI budget and event-specific mappings, atomic history/dedupe/sequence writes, rollback/no-reuse, admission order, prefix/scope isolation and fresh-process reopen/crash recovery | Publishing mappings or current views without their canonical history |
| 5. Core vertical qualification | Import normalized chat/session fixtures, replay, edit/delete/restore, reopen and read bounded history/reply/thread views with exact source refs; verify no graph/embedder requirement and all Core gates below | A value/codec test mistaken for a usable chat profile |
| 6. Derived-knowledge bridge (separate follow-up) | Exact Entry/source publication/frontier adapter, graph-off direct/reverse provenance, multi-support/stale/denied evidence tests; only then episode/summary/semantic-relation and retrieval adapters | Derived rows treated as original evidence or unsupported freshness |

The first vertical slice is complete only after PRs 1–5 jointly qualify:
stable Conversation/Entry identities, AMXR external references, canonical
message/change history, actors/roles, reply/thread links, numeric mappings,
idempotent import, exact revisions/source references, bounded reads and
reopen/recovery. Source references must be usable for the later KnowledgeUnit
bridge, but neither automatic extraction nor derived retrieval is a Core gate.
Advanced graph reasoning, multi-aspect embeddings, intelligent compaction,
arbitrary episode reconstruction and bi-temporal logic remain M2/M2+ work.

These are reviewable sequential slices, not authorization to implement them
together. MDBX remains the first-party canonical backend; any later SQLite
profile must pass the same domain conformance. No backend/default activation,
TypedMetadata or storage migration is included here.

## 7. Decisions and fixtures required before runtime

The event-versus-representation-revision baseline is approved for Phase 1;
per-conversation Entry allocation, source-event/local-observation key recipes
and their durable representation still need the codec/store reviews below.
The first proposed consumers are a chat importer and Efi dialogue memory. Core
qualification uses bounded normalized import/session fixtures, not a live provider SDK: message
creation, attachment-only content, edit, tombstone/restore, replay and unresolved
reply. The broader development-session follow-up adds tool call/result and
patch evidence. Neither proves Telegram/Discord/forum API compatibility or
installs an agent runtime.

Before serialization/store work, separately settle event schema and size limits,
timestamp precision/error handling, actor/alias history, retention/erase policy,
publication/root-frontier adapter, cursor/reopen semantics and import conflict
disposition. Each choice needs its owning contract and direct fixture; none is
silently supplied by AMXR or by the shape of this C++ sketch.

Required future fixtures: exact historical edits/deletes/restores; no-op and
stale expected-revision conflict; no-reuse after reopen/rollback; two observers
of one shared event; snapshot-only evidence without an event/revision key,
limited same-observation replay without invented change history; missing source
IDs and orphan replies; identical replay
versus conflicting duplicate; textless/admin and tool/patch unknown-outcome
events; bounded/cyclic navigation; wrong-scope/expired-frontier continuation;
and stale projection hydration without latest-revision substitution. If an
optimization is proposed, compare against a row-oriented exact baseline under
matched bytes/record limits, context fidelity, p50/p95/p99 and update/reopen
cost. This document reports **no runtime or benchmark execution** for events.

The later derived bridge additionally needs a graph-off fixture from Entry
183@1 to a Relation/Summary/ConversationEpisode and back; an edit while reverse
invalidation is interrupted; retained historical evidence; two independent
supports with one changed; shared-root outputs not counted as independent;
and denied/missing evidence with no body or locator leakage. These extend the
existing linked-knowledge gates, not a second provenance test framework.
