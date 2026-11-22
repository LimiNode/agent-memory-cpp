# Conversation/Event Model: proposed domain contract

- Status: **Phase 1 values/field validation integrated in main; Phase 2 Event
  Codec v1 under independent review in a separate Draft PR. Store and the
  complete Conversation Core profile remain Planned**.
- Date: 2026-11-22. Scheduling: planned early opt-in Conversation Core;
  advanced conversation memory remains M2/M2+ under [milestones](milestones.md).
- Baseline inspected: `main@386e26fe9176d268722894cd9f48df33f3b7bf72`.
- Prerequisite: standalone AMXR v1 integrated and CLOSED within its approved scope.
- Owner: conversation/session domain; rationale:
  [conversation/event ADR](adr/2026-11-conversation-event-identity.md).

This owns the domain contract following the completed Identity integration gate. Phase 1
implements only the value subset in section 4.1; the broader model below is
still proposed. It does not change legacy chat values or add another identity
codec. AMXR and Phase 1 are integrated by #682/#684/#685/#688/#689. A separate assignment
and review must authorize each later implementation slice. The event-versus-representation-revision baseline in
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

### 4.1. Implemented Phase 1 values and field policy

The installed public surface is
[`domain/Conversation.hpp`](../src/agent_memory/domain/Conversation.hpp), also
available through `<agent_memory/domain.hpp>` and backed by the static library.
The broader sketch above is not the implemented API: Phase 1 supplies
`ConversationEntry`, exact `EntryRef`, message creation, snapshot observation,
edit, tombstone and restore only. Tool/command/patch/moderation/derived payloads
remain future work. The executable C++17
[`conversation` example](../examples/conversation.cpp) uses an attachment-only
snapshot without claiming to have observed its creation or edits.

`ConversationId`, `EntryId`, `ActorId` and `EventRevision` are separate strong
u64 types. Construct with an explicit `std::uint64_t`, for example
`ConversationId{std::uint64_t{7}}`; implicit conversions, signed/floating/bool
construction and cross-domain construction/comparison are rejected. Default
zero is unassigned. `UINT64_MAX` is representable; these values have no parser,
increment, allocator, persistent hash or storage-key conversion. Overflow and
committed no-reuse require the later allocator/store, not a value constructor.

`ConversationRef` compares `(allocation_namespace, id)`; `EntryRef` compares
`(conversation, entry, revision)`; `ActorRef` compares
`(allocation_namespace, id)`. Equality and lexicographic ordering retain every
component, including exact revision. Ordering invalid DTOs does not make them
admissible, and reference ordering is not proof of source chronology.

The **new allocation-namespace field policy** is exact ASCII
`[a-z][a-z0-9._-]*`, 1..128 bytes, issued by the host/store. No trimming,
case folding or Unicode normalization is performed. Issuers must coordinate
uniqueness; a valid token does not prove issuer ownership or an allocation.
This grammar is not AMXR namespace issuance, a portable ID format or a new rule
for legacy `ScopeId`. Scope retains its opaque bytes and must be nonempty.

Phase 1 validates already-constructed DTOs with these field bounds, not a
frozen event wire/schema or an untrusted-input decoder:

| Field | Limit / policy |
| --- | --- |
| Inline text | At most 1,048,576 bytes; strict shortest-form UTF-8 Unicode scalars; NUL and whitespace retained |
| Attachments / links | At most 256 each; oversized vectors are rejected without traversing their elements |
| Artifact/source binding and provider revision marker | Nonempty exact opaque bytes, at most 4,096 each; no new UTF-8 restriction on existing ID types |
| Evidence record locator | Nonempty strict UTF-8, at most 4,096 bytes; adapter-defined, with no interchange grammar |
| Adapter recipe | Nonempty strict UTF-8, at most 1,024 bytes |
| Optional external refs | Existing AMXR validator and bounds, unchanged |

Absent or empty text is valid with a valid attachment; both absent/empty text
and no attachments is invalid. Tombstone/restore need no fabricated body.
The validator rejects malformed UTF-8, invalid refs/enums, missing required
bindings and invalid/valueless variants. Diagnostics contain structural paths
and typed codes, not source text or identifiers. Allocation failures propagate;
there is no catch-all converting failure to a successful empty diagnostic list.

Every supplied `ConversationSourceBinding` requires the exact retained
`SourceRevisionId`, evidence artifact/record locator and adapter recipe. Modes
are declared, never inferred from a message ID or time:

| Identity declaration | Required / forbidden identity fields |
| --- | --- |
| `ExternalEvent` | Provider event ref required; object ref optional; upstream revision marker absent |
| `ObjectRevision` | Object ref and nonempty provider revision marker required; event ref absent |
| `LocalObservation` | Object ref optional; event ref and provider revision marker absent |

The retained source revision is not necessarily an upstream revision marker.
The validator can check declared shape, not provider key/marker reliability.
`ConversationSnapshotObserved` requires a source binding; native creation and
change values can omit it. Import admission must require the applicable source
change evidence and may not promote a local snapshot into a fabricated edit.
Evidence can be a retained normalized record rather than a whole archive.
Limited same-observation replay and representation corrections follow section 3;
no dedupe operation or durable observation-key recipe is implemented here.

Resolved target policy is explicit:

| Reference position | Cross-conversation / allocation namespace |
| --- | --- |
| Edit/tombstone affected; restore affected and retained state | Forbidden; same full ConversationRef required |
| Reply / Thread | Forbidden; same full ConversationRef required |
| Branch / Forward / Quote | Allowed; never grants access to the target |

All resolved targets require nonzero exact revisions and a different Entry
occurrence from the containing record, even when its revision differs. This
rejects direct self-links, not all multi-entry cycles. Restore can identify
different exact revisions of the same affected occurrence; whether the retained
state belongs to that message's chain requires admission. Unresolved links
require exact evidence and may carry an AMXR ref. They remain unresolved and
cannot claim that same-conversation or access policy has already been proved.
Resolving them later must reapply the relevant target policy.

Actor, optional LLM role and optional observer are independent. Optional signed
UTC Unix microseconds retain the full i64 range and unknown absence. No time
comparison, inference, parsing or lossy conversion is performed by this layer.

`validate_conversation_entry` performs no I/O and proves **field validity only**.
Existence/retention, scope access, committed allocation/no-reuse, authenticity,
revision monotonicity, mutation-chain membership, chronology/cycles, replay
conflict, publication and current freshness require future transactional
admission/read checks. Phase 1 supplies no store, importer, codec, graph or
retrieval activation; section 4.2 separately defines the Phase 2 codec. The
[`conversation_test`](../tests/domain/conversation_test.cpp) fixture exercises
these positive/negative field contracts; legacy chat and knowledge tests remain
separate compatibility controls.

### 4.2. Event Codec v1 (Phase 2 review contract)

This section is the sole wire owner of the extensible **AMCE envelope v1**,
separate from each payload's schema version. It replaces the closed, unmerged
Phase 2 candidate in PR #690 before integration. That candidate's source-bound
goldens remain historical evidence, not another accepted v1 dialect. There is
no automatic format detection or migration reader for those old candidate
bytes. AMXR v1, integrated Phase 1 values and legacy chat APIs are unchanged.

One envelope represents one occurrence/representation revision, not a complete
conversation. It is neither a DBI layout, allocator, journal, import key,
admission proof, current-message view nor an execution command. Retaining an
unsupported payload preserves bytes; it does not validate that payload's
semantics or authorize canonical semantic admission.

The additive `ConversationEnvelope` owns `ConversationMetadata`, a payload
variant and metadata extensions. Metadata contains the same exact reference,
scope, source, actors/role, timestamps and links as `ConversationEntry`.
The payload variant holds the unchanged five-alternative `ConversationPayload`,
typed tool observations or `ConversationOpaquePayload`. The integrated
`ConversationEntry` aggregate/layout and one-argument validator retain their
existing behavior; `validate_conversation_entry_with_limits` is a separately
named, explicit resource-profile entry point. Resource limits never disable
semantic checks.

The public binary API in `<agent_memory/domain/conversation_codec.hpp>` and
the domain umbrella uses `ByteBuffer` (`std::vector<std::uint8_t>`) and a
borrowed C++17 `ByteView`, not text. A view is borrowed only during a call;
decoded envelopes own their retained bytes. Null with zero length is a valid
empty view, not a valid frame; null with nonzero length rejects. There is no
implicit view construction from a temporary buffer and no public textual
event syntax or event digest-identity recipe.

| API | Boundary |
| --- | --- |
| `serialize_conversation_envelope` | Validate and exact-size preflight, then emit one bounded owning buffer |
| `deserialize_conversation_envelope` | Decode and validate common metadata and supported payload schemas; retain unsupported pairs losslessly |
| `require_typed_conversation_payload` | Refuse opaque payloads and unknown critical metadata; advisory extensions remain in the envelope |
| `make_conversation_envelope(ConversationEntry)` | Explicit move-friendly Phase 1 bridge |
| `to_conversation_entry(ConversationEnvelope)` | Refuse every metadata extension and non-Phase-1 payload, rather than silently discarding bytes |
| `serialize_conversation_entry` / `deserialize_conversation_entry` | Phase-1-only conveniences over the same envelope format; not a separate dialect |
| `inspect_conversation_frame` | Check framing and finite frame budget only; not full metadata/payload validity or admission |

`ConversationDecodeOptions::require_typed_payload` defaults to `false`, the
explicit retention/transport behavior. Setting it to `true` applies the typed
consumption gate after bounded decoding. After validation, typed support proves
only field validity, not authorization, source authenticity or committed store
admission. Calling the gate directly on a mutable DTO is not a substitute for
field validation.

#### Primitives and canonical framing

All integers are fixed width, most-significant byte first: `u8`, `u16be`,
`u32be`, `u64be`. `i64be` is exactly eight two's-complement bytes, preserving
`INT64_MIN..INT64_MAX`; it is never converted through floating point.
`B` means `u32be(byte_count) + byte_count exact bytes`, subject to the selected
resource profile. Zero length is represented, not silently omitted; field
semantics determine whether it is valid at that position. `O(T)` means exactly
`u8(0)` for absence or `u8(1) + T` for presence. All other presence bytes reject.
`V(T)` means `u16be(count) + count ordered T`, subject to its count budget.
Order and duplicates are retained; no sorting/deduplication occurs.

The fixed header is exactly **26 bytes**:

```text
"AMCE" + u16be(envelope_version=1) + u16be(flags=0)
       + u32be(total_bytes) + u32be(metadata_bytes)
       + u32be(payload_type_id) + u16be(payload_schema_version)
       + u32be(payload_bytes)
```

Require `total_bytes == input.size() == 26 + metadata_bytes + payload_bytes`,
using checked arithmetic. Header flags must be zero; type IDs `0` and
`UINT32_MAX`, and schema version `0`, are invalid. Unknown envelope versions
reject. The metadata and payload subreaders must each consume their exact
declared length; trailing bytes cannot be hidden inside either section.
There is no padding or serialization of raw object layout, platform enum
representation or `std::variant::index()`.

Metadata has these **nine tagged fields in exact order**, including absent
optionals and empty sequences, followed by the extension sequence below.
Unknown/duplicate/omitted/out-of-order common-field tags, invalid presence
bytes and malformed nested values reject; they are not skipped.

| Field tag | Value, in wire order |
| --- | --- |
| `01` | `ref: EntryRef` |
| `02` | `scope: B` (nonempty opaque bytes) |
| `03` | `source: O(SourceBinding)` |
| `04` | `actor: O(ActorRef)` |
| `05` | `llm_role: O(u8 role_tag)` |
| `06` | `observer: O(ActorRef)` |
| `07` | `source_unix_us: O(i64be)` |
| `08` | `observed_unix_us: O(i64be)` |
| `09` | `links: V(Link)` |

After tag `09` and its links:

```text
u16be(extension_count)
repeat extension_count times: u32be(extension_id) + u8(critical) + B(bytes)
```

Extension IDs are nonzero, strictly increasing and unique. The writer rejects
unsorted or duplicate input rather than silently sorting it. `critical` is
exactly `0` or `1`; all other values reject. Empty extension bytes remain
present bytes, not absence. No extension semantics are registered in this
slice: all extension values are retained exactly, all critical ones refuse
typed consumption, and advisory ones must survive read-modify-write. An
extension never implicitly replaces scope, evidence or another common field.
Future interpretation-sensitive metadata requires a reviewed critical
extension or a new envelope version, not an advisory escape from old checks.

Nested records have fixed positional fields, **without additional field tags**:

```text
EntryRef = B(allocation_namespace) + u64be(ConversationId)
         + u64be(EntryId) + u64be(EventRevision)
ActorRef = B(allocation_namespace) + u64be(ActorId)
AMXR = B(complete existing AMXR v1 frame)
Evidence = B(artifact_binding) + B(record_locator)
SourceBinding = u8 identity_tag + O(AMXR object) + O(AMXR event)
              + B(source_revision) + O(B(provider_revision_marker))
              + B(adapter_recipe) + Evidence
Content = O(B(text)) + V(B(artifact_binding))
Link = u8 link_kind + u8 target_tag + target_record
target_record[1] = EntryRef
target_record[2] = O(AMXR external) + Evidence
```

| Tag family | Exact mapping |
| --- | --- |
| Identity | `1 ExternalEvent`, `2 ObjectRevision`, `3 LocalObservation` |
| LLM role | `1 System`, `2 Developer`, `3 User`, `4 Assistant`, `5 Tool` |
| Link kind | `1 Reply`, `2 Thread`, `3 Branch`, `4 Forward`, `5 Quote` |
| Target | `1 resolved EntryRef`, `2 unresolved external/evidence` |

All other values in these common tag families reject. Every exact reference
includes its own namespace, conversation, entry and revision; no contextual
shortening is permitted.
Nested AMXR uses the unchanged v1 grammar/limits, including typed signed parent
keys, byte identity and component ordering. Absent text differs from present
empty text. Actor/observer/role and the two timestamps remain independent.
Scope, source revisions, artifact bindings and provider markers remain opaque:
NUL and invalid UTF-8 bytes are preserved where Phase 1 permits them.
Text, tool name, recipe and locator remain strict shortest-form UTF-8 without
normalization (including NUL where permitted). Allocation namespaces retain
Phase 1 ASCII.
Source-mode shape, snapshot evidence, mutation targets and resolved-link policy
are exactly section 4.1, not new provider/authenticity/chronology checks.

#### Payload schemas and append-only registration

The conversation domain owns this append-only registry. Each registered
`(type_id, schema_version)` has one grammar and meaning; IDs and schema pairs
are never reassigned. Addition requires a reviewed owner change and independent
goldens, not a runtime registration callback. Typed alternatives derive their
pair; they do not carry independently mutable duplicate header fields.

| Type ID | Schema | Registered payload body |
| --- | --- | --- |
| `1` | `1` | `MessageCreated: Content` |
| `2` | `1` | `SnapshotObserved: Content` |
| `3` | `1` | `Edited: EntryRef affected + Content replacement` |
| `4` | `1` | `Tombstoned: EntryRef affected` |
| `5` | `1` | `Restored: EntryRef affected + EntryRef retained_state` |
| `256` | `1` | `ToolCallObserved: B(call_id) + B(tool_name) + B(request_artifact_binding)` |
| `257` | `1` | `ToolResultObserved: EntryRef call + B(call_id) + u8(outcome) + B(result_artifact_binding)` |
| `258` | none | Reserved for a future Patch observation schema; retention-only today |
| `259` | none | Reserved for a future Compaction observation schema; retention-only today |

Tool call IDs are nonempty opaque bytes, not globally unique identities or
execution tokens. A tool name is a nonempty UTF-8 observation, not a dispatch
instruction. Request/result bindings are nonempty exact immutable artifact
bindings. Both typed tool observations require `source` with retained
observation evidence. A result's call reference is exact, in the same
conversation, and a different Entry occurrence; field validation does not
prove that the target exists, is a call, has matching call ID, or is accessible.
Those are later admission checks. Outcome wire tags are `1 Unknown`,
`2 Succeeded`, `3 Failed`, `4 Cancelled`; all others reject. Timeout, missing
result or an observed request must not infer an execution outcome.

Every otherwise header-valid pair not listed as a supported schema above is
unsupported and may be retained as opaque bytes; an unknown schema of a registered type is also
unsupported, not interpreted as its known schema. The payload bytes remain
byte-identical through decoding, metadata edits and re-encoding. A known
supported schema with malformed bytes rejects: it cannot downgrade to opaque.
Constructing an opaque value for a supported pair also rejects. An envelope
can have canonical framing while its opaque payload's semantic validity and
logical canonicality remain unknown.

Public allocations use the repository registry below `0x80000000`. Private
IDs `0x80000000..0xfffffffe` require an explicit out-of-band issuer/profile;
they have no portable registered meaning and this implementation only retains
them. Numeric coincidence never supplies a schema, access grant or authority.
Metadata extension IDs have the same repository-owned, no-reuse registration
discipline; none has typed semantics in this slice.

Changing envelope framing, common-field tags/order, integer encodings or
extension framing requires a new envelope version. Adding a registered payload
type or a new schema version does not. Changing an existing payload's grammar
requires a new schema version; its old pair remains immutable. An advisory
extension must remain safely ignorable; required interpretation changes cannot
silently be reclassified as advisory.

#### Resource policy, implementation ceiling and compatibility

Wire widths describe representation ranges, not permission to allocate u32
bytes or u16 objects. `ConversationCodecLimits` is a conjunction of finite
consumer budgets; zero forbids that resource and never means unlimited.
Profiles above the implementation ceilings reject before work. The default
profile retains Phase 1 field limits; the extended profile is explicit opt-in.

| Budget | Default | Extended profile | Implementation ceiling |
| --- | --- | --- | --- |
| Complete frame | 33,554,432 bytes | 33,554,432 bytes | 33,554,432 bytes (32 MiB) |
| Inline text | 1,048,576 bytes | 4,194,304 bytes | 8,388,608 bytes |
| Attachments / links, each | 256 | 512 | 4,096 |
| Artifact binding / source revision / provider marker | 4,096 bytes | 16,384 bytes | 65,536 bytes |
| Tool call ID (uses binding budget) | 4,096 bytes | 16,384 bytes | 65,536 bytes |
| Evidence locator | 4,096 bytes | 16,384 bytes | 65,536 bytes |
| Adapter recipe | 1,024 bytes | 4,096 bytes | 4,096 bytes |
| Tool name (uses recipe budget) | 1,024 bytes | 4,096 bytes | 4,096 bytes |
| Metadata extensions | 64 | 256 | 1,024 |
| Bytes in one metadata extension | 65,536 | 1,048,576 | 8,388,608 |
| Charged owned bytes | 33,554,432 | 33,554,432 | 33,554,432 |
| Charged objects | 4,096 | 16,384 | 32,768 |

Allocation namespaces retain their fixed 1..128-byte ASCII semantic contract.
Nested AMXR retains its fixed 65,536-byte frame and every existing component
limit; an event profile cannot widen it. Scope has no independent 64 KiB cap:
its nonempty opaque bytes are bounded by frame and owned-byte budgets, not
reclassified as an allocation namespace. Frame, metadata and payload bounds
apply together, so separately maximal fields need not fit as one event.

Owned-byte charge is the sum of every `B` byte count, including complete nested
AMXR frames, plus an opaque payload's bytes. Object charge counts every `B`
value and every sequence element. AMXR is charged as its framed `B`, not
recursively charged a second time. These are deterministic logical budgets,
not allocator call counts, actual heap peaks or process RSS guarantees. Writer
and reader use identical accounting, including empty values, extension bytes
and opaque payloads. Performance/peak-allocation qualification reports actual
measurements separately.

Serialization validates, prepares bounded nested AMXR frames, and counts the
exact complete size with overflow-safe arithmetic before allocating its output
buffer. Retained preparation is incrementally charged, not an unbounded cache.
The output is reserved once; an actual/estimated size mismatch is an internal
error. Decoding checks whole-frame bounds first, then each length against
remaining bytes and applicable budgets before copying, and sequence counts
against limits and feasible minimum bytes before reserving. No recursive
event decoding is introduced; AMXR's existing flat bound remains. No partially
accepted value escapes a failed operation.

`conversation_codec_limits_cover(reader, writer)` checks pointwise resource
dominance after validating both profiles. Actual writer/reader compatibility
additionally requires payload-schema and critical-extension support. A record
accepted by a wider writer can legitimately receive `ResourceLimit` from a
stricter reader; it is not thereby corrupt. A future store must record its
required reader/write profile and qualify reopen against it or report explicit
resource refusal. No capability DBI or store policy is implemented here.

`ConversationCodecError` derives from `std::invalid_argument` and exposes
`ConversationCodecCode`: `Malformed`, `InvalidValue`, `ResourceLimit`,
`InvalidLimits`, `UnsupportedEnvelope`, `UnsupportedPayload`,
`UnsupportedMetadata`. Framing/encoding damage, semantic field invalidity,
budget refusal, unsupported profiles and unsupported interpretation are
distinct. A bounded refusal does not certify unexamined bytes as otherwise
valid, and the decoder need not keep parsing to discover a deeper error.
Allocation failures propagate as allocation failures; diagnostics do not echo
untrusted source text or identifiers. No normalization or latest resolution
occurs on either successful or failed paths.

#### Agent-session proof and later admission boundary

Ordinary chat, Efi dialogue and Maschinenwerk development-session fixtures use
the same envelope. Typed ToolCall/ToolResult demonstrate new-schema support;
unsupported Patch/Compaction samples demonstrate only artifact retention and
interpretation refusal. They are not additional installed typed schemas:

| Future observation | Reference-only fixture invariant | Must not imply |
| --- | --- | --- |
| Patch, reserved type `258` | Immutable patch artifact plus exact source/file context and input/output revisions when known; retain unknown application outcome and observation evidence | Applying a patch, fabricating an output revision, or granting a typed schema to illustrative opaque bytes |
| Compaction, reserved type `259` | Exact input Entry revisions, output artifact, recipe and provenance receipt stay in retained evidence; a fixture without a receipt cannot claim published derived knowledge | Replacing canonical inputs, treating a summary as independent evidence, or automatic KnowledgeUnit publication |

The illustrative artifact content has no public AMCE payload interchange
grammar. Opaque preservation tests alone cannot establish those inner semantic
invariants; normalized fixture assertions and evidence checks must be separate
from the codec's retention/unsupported assertions. Tool result existence/type,
call-ID matching, artifact availability, access and source authenticity also
remain admission responsibilities, not consequences of successful decoding.

Reference-only design examples (not registered payload grammars):

- A patch observation names immutable `patch@1`, exact source/file context and
  input `file@4`; output is absent and action is `Observed`. It claims neither
  patch creation nor an application attempt. `Created` is a distinct observation
  with its creation evidence. `ApplicationAttempted` names its attempt receipt;
  a failed/unknown attempt cannot supply a fabricated successful output.
  Confirmed application names both `file@4` and `file@5`, the immutable patch,
  and an application receipt establishing that outcome. Multiple files require
  their complete exact revision set in the retained manifest, not one guessed
  aggregate revision. Artifact availability and receipt authenticity are checked
  separately when consumed.
- A compaction observation names exact input Entry revisions and any immutable
  input artifacts, output `summary@1`, the versioned derivation recipe and
  retained derivation evidence. Its input set/frontier is not inferred from a
  time interval. A publication receipt, when present, selects the existing
  publication contract; its absence means no published derived-knowledge claim.
  Reading the output never upgrades it into independent primary evidence or
  discards the original input references. Recompaction names the previous
  derived input and its original lineage rather than flattening away provenance.

Opaque transport of either reserved type establishes none of these assertions;
the later schema must encode these distinctions before typed admission is
implemented. This is deliberate proof of retention versus interpretation, not
an executor or an unbounded body embedded in AMCE.

New independent literal goldens and malformed/mutation controls qualify only
the revised bytes and advertised profiles. The archived closed-candidate
vectors and its 21,186,977-byte fixture remain identified by their original
commit and do not prove revised-envelope maxima. Qualification must include
unsupported type/schema and extension retention through read-modify-write,
critical-extension refusal, known-invalid-schema rejection, strict/extended
profile behavior and measured maximum inputs. Test SHA-256 values identify
fixtures, not event IDs.

The codec allocates no domain IDs, deduplicates no imports, reads no artifact
bodies, authenticates no evidence and chooses no journal order. History,
expected-revision conflicts, replay, no-reuse, storage indexes and crash/reopen
recovery remain later store contracts. Encoding, compression and physical
storage are separate: future zstd/page tuning must not redefine logical
identity or provenance. A small metadata/header inspection is not a validated
event, and wire framing is not authorization to implement a new storage engine.

| Read strategy | Work and validity boundary | Physical owner |
| --- | --- | --- |
| Full decode | Traverses and owns all selected fields; validates supported schemas, retains unsupported bodies | Implemented AMCE codec; memory/latency measured separately |
| Fixed header inspection | Reads 26 bytes plus checks declared lengths against the complete borrowed view; no body allocations, metadata/payload validity is unproven | Implemented codec framing API; not a source/scope lookup |
| Covering secondary index | Avoids materializing bodies for selected metadata predicates; needs transactionally consistent index and authorization checks | Future ConversationStore; no MDBX schema or performance claim in this PR |
| Separate read projection | May serve compact query-specific fields; needs exact event/revision binding and freshness policy | Future projection/store boundary; never independent primary evidence |

Compression, dictionaries and MDBX pages belong below these logical contracts.
Artifact storage owns large request/result/file/patch bodies. A binding neither
loads those bodies nor grants access to them.

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

This matrix distinguishes integrated identity/domain code from contract-only
reuse and the separately reviewed Phase 2 codec. It does not claim the full
Conversation Core is implemented.

| Action | Types / storage / indexes | Status and boundary |
| --- | --- | --- |
| REUSE | `ExternalObjectRef` / AMXR, `ScopeId`, source/artifact bindings | Value/AMXR codec code is integrated; source/anchor/retention semantics stay with artifact provenance. No new external ID grammar or implicit legacy conversion |
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
| 2. Event codec | Separately approve envelope framing, payload schemas/registry, metadata extensions, resource profiles and source-anchor mapping; exact goldens and malformed/oversized/unsupported-retention controls. AMXR is nested external identity, not the event format | Prematurely freezing a persistent format or treating opaque retention as semantic admission |
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

## 7. Remaining decisions and fixtures for store work

The event-versus-representation-revision baseline is implemented in Phase 1;
section 4.2 defines the separately reviewed Phase 2 wire format and limits.
Per-conversation Entry allocation, source-event/local-observation key recipes
and their transactional persistence still need the store reviews below.
The first proposed consumers are a chat importer and Efi dialogue memory. Core
qualification uses bounded normalized import/session fixtures, not a live provider SDK: message
creation, attachment-only content, edit, tombstone/restore, replay and unresolved
reply. Phase 2 adds codec-only typed tool-call/result observations; the broader
development-session/store follow-up adds their history and patch evidence.
Reserved Patch/Compaction artifact-only codec fixtures are not implemented
typed schemas. None proves Telegram/Discord/forum API compatibility or
installs an agent runtime.

Before store work, separately settle actor/alias history, retention/erase policy,
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
cost. Phase 1 field tests are not evidence of these history/store behaviors or
of any event performance improvement.

The later derived bridge additionally needs a graph-off fixture from Entry
183@1 to a Relation/Summary/ConversationEpisode and back; an edit while reverse
invalidation is interrupted; retained historical evidence; two independent
supports with one changed; shared-root outputs not counted as independent;
and denied/missing evidence with no body or locator leakage. These extend the
existing linked-knowledge gates, not a second provenance test framework.
