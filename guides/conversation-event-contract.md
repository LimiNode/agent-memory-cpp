# Conversation/Event Model: proposed domain contract

- Status: **Draft / proposal, no public C++ API or event store implemented**.
- Date: 2026-11-22. Milestone: later M2 conversation/session integration.
- Baseline inspected: `main@acd5ad5545386b3feeaa79f8a587855b60801b4d`.
- Prerequisite: approved standalone AMXR v1, with integration still pending.
- Owner: conversation/session domain; rationale:
  [conversation/event ADR](adr/2026-11-conversation-event-identity.md).

This proposes the next contract after the Identity integration gate. It does
not approve a runtime implementation, change legacy chat values, or add another
identity codec. AMXR is implemented in the prerequisite stack; **the event
model below is not**. A separate assignment and review must authorize each
implementation slice.

## 1. Ownership and canonical entities

Keep domain stores separate, as required by [canonical content, section 2.2](canonical-content-storage-roadmap.md#22-conversation--session).
Do not flatten a conversation into a mandatory document/string or make
`ConversationEpisodePayload` its source of truth.

| Entity | Proposed invariant |
| --- | --- |
| Conversation | Stable conversation/session identity; not a display name, channel label, import run or branch |
| Entry | One canonical source-event occurrence within a conversation; identity survives corrections to its recorded representation |
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

**Baseline submitted for review:** a message creation, upstream edit, deletion,
restore or moderation action is a distinct Entry when it is a distinct source
event. An edit/delete/restore event targets the exact prior event/state it
affects; it does not overwrite or erase the creation event. A current-message
view is a deterministic fold/projection over these canonical facts.

An Event revision instead corrects the stored representation of the **same**
source-event occurrence, for example an explicitly reviewed normalization
correction. Such a correction records its old/new adapter recipe, evidence
and reason, uses an expected revision and publishes a new immutable revision.
Replaying unchanged evidence is `NoChange`; silently rewriting a revision is forbidden.
This proposed distinction needs human approval before event APIs or a durable
schema are implemented. It does not change document-edit semantics.

| Typed event | Minimum validity / provenance |
| --- | --- |
| Message creation | Text, attachment or both; empty text is valid when the typed content is otherwise meaningful |
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

An adapter declares an immutable source-event key recipe and exact revision
rule before idempotent import. Full namespace-qualified external event identity
plus selected source revision identifies the source fact; the normalization
recipe additionally identifies the representation. An external message/object
ID alone need not identify its individual edit events. AMXR parent/type/domain
rules are reused, never inferred from a numeric value.
A changed normalization recipe does not allocate a competing source Entry; it
requires an explicit representation correction with an expected revision.

- Same source event/revision, recipe and canonical content: idempotent replay,
  no new Entry/revision; a new observation receipt may be recorded separately.
- Same declared key/revision/recipe but conflicting payload or target: explicit
  conflict/quarantine with both evidence records, not last-writer-wins.
- Missing external event identity: a local occurrence with exact archive/import
  artifact and record locator may support a declared **local import** key. It
  is not a fabricated globally portable external ID. Without a stable key,
  report deduplication as unresolved instead of merging on text or time.
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
using Payload = std::variant<MessageCreated, Edited, Tombstoned, Restored,
    Moderated, AttachmentObserved, ToolCallObserved, ToolResultObserved,
    CommandObserved, PatchObserved, ConfigurationChanged, DerivedObserved>;
struct SourceBinding {
    agent_memory::ExternalObjectRef event;
    agent_memory::SourceRevisionId revision;
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
source-binding fields and payload/target mismatches. A local field validator cannot prove
that an artifact, actor or exact target exists, is authorized, belongs to the
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

ConversationEpisode, window, summary and search projections retain exact
source Entry/revision inputs, selected frontier, scope, derivation recipe and
publication receipt. [Knowledge-unit episodes](knowledge-units-roadmap.md#55-conversationepisodepayload)
remain retrieval projections, including their existing utterance/participant
fields; this proposal does not rewrite that payload or its DBIs. Any future
adapter must resolve those locators to exact canonical event revisions before
claiming source-backed completeness. A summary is not a second canonical store.

Source update may make a current projection stale while its historical input
binding remains valid. Missing retained evidence, historical validity,
freshness and publication state are distinct, as in
[linked-derived knowledge](linked-derived-knowledge.md). Technical lineage and
navigation can operate without a graph backend.

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

| Future slice | Minimum implementation and acceptance gate | Principal risk |
| --- | --- | --- |
| 1. Value types / validation | Approve the proposed event-versus-revision and allocation rules; add C++17 values, textless example, typed invalid-input tests and unchanged legacy tests | Ambiguous target/identity or mistaken admission claims |
| 2. Event serialization | Separately reviewed versioned schema, exact golden vectors, bounds and strict malformed-input tests; AMXR remains nested external identity, not the event format | Prematurely freezing a persistent format |
| 3. Canonical event store | Domain-oriented exact/current reads, expected-revision conflict/NoChange, idempotent import, bounded paging and publication/source-frontier conformance | Losing history or mixing snapshots |
| 4. MDBX persistence | Reuse existing backend context/composition; reviewed DBI profile, allocation rollback/no-reuse, prefix/isolation, fresh-process reopen and crash/recovery matrix | Transaction/lifecycle and recovery mismatch |
| 5. Projections / adapters | Exact episode/window/summary hydration, source update/delete invalidation, authority/access/shared-frontier bridge and deterministic import fixtures | Treating derived rows as canonical evidence |

These are reviewable sequential slices, not authorization to implement them
together. MDBX remains the first-party canonical backend; any later SQLite
profile must pass the same domain conformance. No backend/default activation,
TypedMetadata or storage migration is included here.

## 7. Decisions and fixtures required before runtime

Human review must approve or revise the concrete baseline in sections 2–3,
especially new source events versus representation corrections, per-conversation
Entry allocation and source-event deduplication recipes. The first proposed
consumer is a bounded imported development-session fixture, not a live provider
SDK: creation, attachment-only event, tool call/result, edit, tombstone and
unresolved reply. It proves no Telegram/Discord/forum API compatibility.

Before serialization/store work, separately settle event schema and size limits,
timestamp precision/error handling, actor/alias history, retention/erase policy,
publication/root-frontier adapter, cursor/reopen semantics and import conflict
disposition. Each choice needs its owning contract and direct fixture; none is
silently supplied by AMXR or by the shape of this C++ sketch.

Required future fixtures: exact historical edits/deletes/restores; no-op and
stale expected-revision conflict; no-reuse after reopen/rollback; two observers
of one shared event; missing source IDs and orphan replies; identical replay
versus conflicting duplicate; textless/admin and tool/patch unknown-outcome
events; bounded/cyclic navigation; wrong-scope/expired-frontier continuation;
and stale projection hydration without latest-revision substitution. If an
optimization is proposed, compare against a row-oriented exact baseline under
matched bytes/record limits, context fidelity, p50/p95/p99 and update/reopen
cost. This document reports **no runtime or benchmark execution** for events.
