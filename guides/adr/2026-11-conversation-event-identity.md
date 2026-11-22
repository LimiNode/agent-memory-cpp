# ADR proposal: conversation events and external identity

- Status: **Phase 1 values implemented on dependent branch / review pending**;
  broader event model and Conversation Core remain proposed/planned.
- Date: 2026-11-21.
- Source inspected: `acd5ad5545386b3feeaa79f8a587855b60801b4d`.
- Scheduling: planned early opt-in Conversation Core under
  [milestones](../milestones.md#early-opt-in-conversation-core-planned);
  advanced conversation memory remains M2/M2+, not the M1b document row.
- Scope: domain identity, source events, compatibility and implementation gates.

Identity Architecture addendum: [identity policy](../identity-policy.md) is now
the central owner of reusable external-reference namespaces, value semantics
and exact AMXR v1 grammar. It supersedes this ADR's initial codec candidate,
not its still-proposed conversation/event model. The companion identity value
implementation does not implement ConversationId, EntryId, actors or a store.

The separate [Conversation/Event contract proposal](../conversation-event-contract.md)
develops the next value surface, event-versus-revision baseline, bounded reads
and staged implementation gates. Its section 4.1 owns the implemented Phase 1
values/field policy and executable example. That narrow implementation does not
authorize an event codec, ConversationStore or later runtime work.
The proposal's ownership/reuse matrix and evidence/reverse-dependency contract
reuse the existing KnowledgeUnit, publication and projection owners. Journal
entries/changes are distinct from `KnowledgeUnitKind::Event`; conversation
links do not automatically become semantic Relation units.

## Context and owner

[Canonical content storage, section 2.2](../canonical-content-storage-roadmap.md)
already describes a Conversation / SessionEventStream: messages, edits,
replies/branches, tool calls/results, attachments/patches, commands, model changes
and compaction/summary events. [Knowledge units, section 5.5](../knowledge-units-roadmap.md)
describes `ConversationEpisodePayload` as a retrieval-oriented projection.
[Milestones](../milestones.md), [product path](../product-path.md) and
[memory stacks](../memory-stacks-roadmap.md) remain the owners of scheduling,
component boundaries and product activation.

The current `chat/Message.hpp` is a retained public scaffold: string MessageId,
ThreadId and AuthorId, text Message, vector-backed Thread and AuthorRole. It has
no canonical ConversationStore. Its non-empty-text validation cannot represent
all attachment-only or administrative events. This proposal does not rename,
remove or silently reinterpret those types.

## Proposed decision

Use a backend-neutral event model, not a family of Telegram/Slack/etc. domain
DTOs. Keep five distinct identity layers:

| Layer | Meaning | Proposed boundary |
|---|---|---|
| Conversation | Stable conversation/session identity | Distinct from a channel label, import job or thread branch |
| Entry | A canonical source entry/event | Local strong u64 occurrence with explicit allocation namespace; never a bare portable counter |
| External reference | A provider object in its real uniqueness domain | Versioned, namespace-qualified tuple with typed components |
| Actor | Source participant/principal | Stable actor identity plus aliases; not merely a display name or LLM message role |
| Physical key | Backend lookup/allocation | Separate storage identity mapping; not a citation or source revision |

Thread/topic and reply/branch edges are explicit relationships. Do not collapse
them into ConversationId or infer equality from matching names. An exact
EntryRef additionally identifies the selected event revision/history point;
latest resolution is a separate operation and cannot silently replace it.

Illustrative model mappings, not provider-specific canonical classes:

| Source shape | Conversation context | Entry/relationship examples |
|---|---|---|
| One-to-one or group chat | Stable chat identity, with separately observed membership | Message, attachment, edit, reply; a renamed group does not acquire a new identity |
| Channel with topics | Channel conversation plus declared topic/thread context | Channel entry and topic/reply links; a topic's local number needs channel scope |
| Forum discussion | Provider-declared forum/topic context | Posts and reply tree; topic versus thread mapping is an explicit adapter rule |
| Imported archive | Source/import namespace with explicit gaps | Partial entries, revisions and unresolved parents, never fabricated continuity |
| LLM/development session | Session conversation with branches | Role-tagged messages, tool call/result, command, patch and compaction events |

Membership and moderation observations may affect a provider's historical view,
but are not substitutes for the caller's current authorization checks.

Local allocation requires a store-owned monotonic, non-zero, no-reuse sequence,
including retirement/history and rollback rules. A portable local reference
includes its allocation/source namespace; importing may remap the occurrence
counter without rewriting the external source identity or selected revision.
Neither an integer constructor nor this ADR supplies an allocator.

### External-reference tuple

Use the generic ExternalObjectRef described by the
[central owner](../identity-policy.md#2-external-namespace-issuance-and-missing-identity),
not a second chat-specific ExternalEntryRef. The parent_key's role is fixed by
the object-kind recipe; it is not a separate unqualified ConversationId.
Provider/instance/domain issuance, account-local exceptions, missing IDs,
flat composite bounds, strict scalar equality and exact AMXR framing are
specified there once. A selected event revision remains a separate binding.
Any future durable event codec needs its own reviewed schema and provenance
contract; the standalone reference does not supply that event format.

Examples that the future fixture must distinguish:

- Telegram chat `-100123` needs signed i64; message `42` in that chat differs
  from message `42` in chat `-100124`.
- Equal object numbers at different providers/instances/domains differ.
- The same shared message observed by two accounts remains one source object,
  with two observations, unless the provider's ID domain says otherwise.
- Opaque string `00123` differs from string `123` and integer `123`; no generic
  numeric parsing, Unicode normalization or case folding is authorized.

JSON adapters must parse integers losslessly, never through binary64. Test
`9007199254740993` (`2^53 + 1`), INT64_MIN/MAX and UINT64_MAX. If an interchange
cannot represent an integer exactly, use a tagged decimal string or explicit
error, not rounding. A provider's numeric-string equivalence, if any, belongs
to its versioned adapter, not a global heuristic.

### Actors and event payloads

ActorKind (human, service, bot, application, unknown) is independent of an
optional LlmMessageRole (system, user, assistant, tool, etc.). A human may author
a system-like imported record; a service may relay a user's message. Do not
derive identity or permission from either enum. Actor aliases retain issuer,
observation time and provenance rather than overwriting a single name.

Store immutable event revisions/records for message creation, edits, deletion,
restoration and moderation; retain the source entry they affect. Keep source
time, local observation/import time, ordering and causal/reply references
separate. Missing source timestamps are explicit, not filled with import time.
Attachment-only, empty-text administrative and tool-result events are valid
when their typed payload is valid; the legacy Message text rule is unchanged.

Tool calls/results, commands, patches and model changes are durable observations.
They do not execute tools, schedule agents or install a runtime in this library.
Opaque provider payloads may be retained as artifacts with versioned adapter
descriptors, not parsed into unbounded dynamic domain objects by default.

### Import, history and reads

An import adapter must declare its deduplication key and revision rule.
Replaying the same source event and revision is idempotent; a conflicting
duplicate is an explicit conflict/quarantine, never last-writer-wins by accident.
Incomplete archives, reordered events and unresolved reply targets retain
their gaps/unresolved references. Do not invent missing parents or merge two
sources because their text, timestamps or aliases happen to match.

Cross-posts, quotes, forwards, attachments and links are relationships with
source namespace, selected revision and scope. They do not grant access to the
target. Every dereference rechecks authorization/lifecycle independently.

Expose bounded entry/range/branch reads with limits on entries, decoded bytes,
depth and fanout. A continuation cursor binds the scope, ordering and selected
frontier so a page cannot silently mix snapshots. Retaining a complete
`vector<Message>` is not a prerequisite for a canonical read. Reopen/recovery
and revision selection belong to a later store specification.

Episodes, windows, thread views and search projections reference exact source
entries/revisions plus frontier, derivation recipe, scope and publication
state. They may accelerate retrieval but never replace canonical events or
serve as independent evidence. Staleness, source unavailability and historical
validity remain distinct, as in the linked-derived-knowledge contract.

## Compatibility and implementation gates

| Existing surface | Required compatibility |
|---|---|
| Message/Thread/AuthorRole | Keep current includes, value semantics, validation and bytes; no reinterpretation of old string IDs |
| Legacy chat import | Explicit adapter supplies missing provider/domain/context; absence is unresolved, not guessed |
| Canonical source/anchors | Exact revision and scope survive occurrence remapping; no local numeric substitution |
| Episode payload and knowledge envelope | Derived references/provenance, not a second canonical conversation store |
| Numeric MDBX mapping | Reuse reviewed storage composition later; do not change DBIs/comparators in this ADR |

The current Identity Architecture workstream supplies the separate reusable
external-reference value/codec and its qualification. A canonical event model
and event store remain separately reviewed slices of planned Conversation Core
after their product storage and store/transaction/recovery owners are agreed.
Only advanced conversation memory waits for M2/M2+. No provider SDK is required in
core. Only Phase 1 values/field validation are implemented on this branch;
no canonical event history, codec or store is included.

The following acceptance matrix is **required future work, not tests run by this
documentation PR**:

| Fixture family | Required result |
|---|---|
| Chat-local numeric message IDs | Equal numbers in different chats never collide |
| Provider/instance/domain namespaces | Equal numbers/text at distinct issuers never collide |
| Signed/unsigned/opaque keys | Tags preserved; negative chat IDs and leading zeros round-trip |
| Integer JSON boundaries | Exact `2^53 + 1`, i64/u64 endpoints; overflow/rounding rejected |
| Two observing accounts | One shared source identity, independent observations/access |
| Account-local provider | Account namespace included only under the adapter's declared rule |
| Missing IDs/incomplete archives | Explicit unresolved/import identity, no fabricated global ID |
| Replayed/conflicting duplicates | Idempotent same revision; conflicting duplicate fails explicitly |
| Reordered/orphan replies | Deterministic ordering and unresolved edge, no invented parent |
| Edits/deletes/restores/moderation | Exact historical versions remain addressable |
| Attachment-only/service events | Typed payload validity, independent of non-empty message text |
| Branches/cross-posts/quotes | Source/revision binding and bounded traversal; no access grant |
| Actors/roles/aliases | Actor identity, role and display alias remain independent |
| Tool/development session | Calls/results/patches persist without execution/orchestration |
| Paging/reopen/import remap | Bounded stable-frontier reads, scope checks and provenance preserved |

Codec corruption, truncation, unknown tags, oversized components, tuple ambiguity,
allocator overflow/retirement and legacy adapter behavior also require negative
controls. A benchmark, if a representation optimization is later proposed, must
retain a compatible baseline and report bytes/allocation/read cost; no speed
claim follows from choosing an integer type.

## Consequences and unresolved decisions

This separates source truth from local lookup without making every ID numeric.
It costs explicit namespaces, adapters and versioning. External namespace
issuance, missing-ID handling and AMXR grammar/limits now belong to
[identity policy](../identity-policy.md); an event adapter cannot silently
redefine them. Remaining conversation review must settle the exact event
revision/relationship schema, occurrence/actor/thread allocation, store and
recovery semantics, and first concrete consumer before a durable event API or
schema is implemented. AMXR alone supplies none of that event/store behavior.
Rejected alternatives remain a bare numeric/string variant, provider-specific
canonical DTOs, mandatory whole-conversation vectors, and silently replacing
legacy IDs with counters.
