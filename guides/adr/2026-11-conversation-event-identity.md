# ADR proposal: conversation events and external identity

- Status: **Proposed / review pending**, not an implemented public contract.
- Date: 2026-11-21.
- Source inspected: `acd5ad5545386b3feeaa79f8a587855b60801b4d`.
- Milestone: later M2 conversation/session integration, not M1b text storage.
- Scope: domain identity, source events, compatibility and implementation gates.

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

The proposed logical fields are:

```text
codec_version, object_kind,
provider, service_instance, provider_identity_domain,
conversation_key? (when the object key is conversation-local),
object_key
```

Provider, service instance and identity domain are mandatory adapter-defined
namespaces. A self-hosted service is not the same issuer as another instance.
The identity domain records any account/workspace/tenant boundary required by
the provider's uniqueness rule. An observer account used to download a shared
message is normally observation/access provenance, not an identity component.
Include it in identity only when the provider's actual identifier domain is
account-local; document this choice in that adapter's contract.

Keys are tagged signed i64, unsigned u64 or opaque UTF-8 string components,
and explicitly framed tuples where a provider needs composite keys. A bare
`variant<uint64_t, string>` is insufficient. Preserve component type, tuple
arity and optional presence. Do not concatenate unescaped text with separators.
Display names, aliases and URLs are mutable descriptors unless an adapter
explicitly establishes a stable identity rule.

For a future codec prototype, evaluate a versioned tuple framing with a type
tag and u32 big-endian byte length per component, fixed-width big-endian
two's-complement i64/u64 payloads, and strict UTF-8 string payloads. The tuple
must encode arity, required fields and absent/present optional fields. This is
a candidate, not an existing wire format; exact tags, limits, namespace
normalization and hex goldens require review before persistence.

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

The smallest implementation after review is a dependency-free external-reference
value/codec prototype with exact golden vectors and explicit limits. A canonical
event store is a separate M2 slice after the product storage foundation and
store/transaction/recovery owner are agreed. No provider SDK is required in
core, and no implementation is included here.

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
It costs explicit namespaces, adapters and versioning. Review must settle the
portable namespace issuance rule, exact event revision model, missing-ID policy,
codec grammar/limits and first concrete consumer before public API or durable
schema implementation. Rejected alternatives are a bare numeric/string variant,
provider-specific canonical DTOs, mandatory whole-conversation vectors, and
silently replacing legacy IDs with counters.
