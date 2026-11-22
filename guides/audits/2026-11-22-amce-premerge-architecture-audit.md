# AMCE pre-merge architecture correction audit

- Status: **Source-bound audit of the superseded candidate, with implemented
  correction decisions**. Observed qualification is recorded in the
  [separate review receipt](2026-11-22-amce-premerge-qualification.md).
- Date: 2026-11-22.
- Source: PR #690 at `4ec7866f31e0d0b2e3d2ca1933b27c3e78f767ec`;
  integrated baseline `386e26fe9176d268722894cd9f48df33f3b7bf72`.
- Owner: conversation domain; current wire candidate:
  [Conversation contract, section 4.2](../conversation-event-contract.md#42-event-codec-v1-phase-2-review-contract).
- Task boundary: the complete A–J correction is available. Earlier notes about
  an unavailable task tail are obsolete; no missing requirements are inferred.
- No merge, ConversationStore, physical schema, executor, migration, release,
  tag or default activation is authorized by this audit.

## 1. Findings supported by source

The codec at the source HEAD correctly implemented its closed candidate grammar. That is
not evidence that the grammar meets the newly requested extension contract.
The five-payload vocabulary and fixed top-level tags were explicit requirements
of the earlier task, not accidental decoder omissions.

The new task changes the architecture before integration. It requires separate
envelope and payload-schema evolution, preservation of unsupported data, and
separation of format validity from consumer resource policy. These cannot be
implemented merely by increasing codec constants.

Initial sources inspected at the source HEAD above (the links locate files;
they do not attribute the corrective working-tree changes to that commit):

- [`Conversation.hpp`](../../src/agent_memory/domain/Conversation.hpp) and
  [`Conversation.cpp`](../../src/agent_memory/domain/Conversation.cpp):
  Phase 1 values and field validation.
- [`ExternalObjectRef.hpp`](../../src/agent_memory/domain/ExternalObjectRef.hpp)
  and [identity policy](../identity-policy.md): integrated AMXR identity/profile.
- [`conversation_codec.hpp`](../../src/agent_memory/domain/conversation_codec.hpp)
  and [implementation](../../src/agent_memory/domain/conversation_codec.cpp):
  the unmerged closed wire candidate.
- [`conversation_codec_test.cpp`](../../tests/domain/conversation_codec_test.cpp):
  existing closed-candidate goldens, negative controls and combined maximum.

Phase 1 limits originated in `8e269660c2ded09be8d1c574f9444b4ac5d72c46`;
standalone AMXR in `a27e0c0558314d3760b41217ca37b29ab71c3bff`;
the additional event/scope caps in `6f2527e3e75a501743d97b1dc9bcaa5971b37e34`.

## 2. Limit classification

The numeric "existing bound" column describes the closed candidate at the
source HEAD, not the revised supported profiles. Current profiles and finite
ceilings are owned once by section 4.2 of the conversation contract.

| Quantity | Existing bound | Kind and correction boundary |
| --- | --- | --- |
| String length prefix | u32, 0..4,294,967,295 bytes | Structural representation range, not permission to allocate that much |
| Attachment/link count prefix | u16, 0..65,535 items | Structural representation range, not the current policy of 256 |
| Nonzero IDs, namespace syntax, valid UTF-8 and target/source-mode rules | Phase 1 | Semantic invariants; do not weaken through resource options |
| Allocation namespace | 1..128 ASCII bytes | Integrated domain identity contract; not interchangeable with free-form scope |
| Inline text | 1,048,576 bytes | Integrated Phase 1 validation profile and repeated codec cap; larger policy needs an explicit validator boundary |
| Attachments / links | 256 each | Integrated Phase 1 validation profile, below wire width; same explicit boundary needed |
| Binding / source revision / provider marker | 4,096 bytes | Integrated Phase 1 profile, not a u32 representation limit |
| Locator | 4,096 strict UTF-8 bytes | Integrated Phase 1 profile; retain semantic validity while making size policy explicit |
| Recipe | 1,024 strict UTF-8 bytes | Integrated Phase 1 profile; recipe meaning/version is not resource policy |
| Scope | Nonempty opaque bytes in Phase 1; 65,536 in codec | Additional unmerged codec admission cap; no namespace or UTF-8 rule may be inferred from it |
| Nested AMXR | 65,536 total bytes, scalar/tuple/namespace limits | Integrated AMXR compatibility contract, not adjustable by an event budget |
| Complete AMCE frame | 33,554,432 bytes | Current implementation/admission ceiling; keep a finite ceiling and qualify any replacement explicitly |
| Owned decoded bytes / object count / actual heap peak | No independent accounting | Frame-size admission is not a complete heap or process-RSS guarantee |

The closed candidate called the one-argument Phase 1 validator in both
serialization and final decoding.
Raising only `maxInlineTextBytes` or `maxLinks` in codec options would therefore
still reject larger records. Preserve the existing validator's signature and
behavior; propose a separately named explicit profile-aware validation entry
point rather than silently broadening the old function. That also avoids
introducing overload ambiguity for callers taking its function address.

## 3. Resource-policy decision and implementation boundary

The revised owner separates:

1. Unchangeable semantic validation and the unchanged AMXR v1 contract.
2. Explicit conversation field limits, used consistently by validation,
   serializer and decoder. Existing one-argument validation keeps its current
   profile; `validate_conversation_entry_with_limits` is a separately named
   opt-in entry point and never disables semantic checks.
3. `ConversationCodecLimits` / decode options: complete frame, payload bytes,
   field budgets, sequence counts and owned-byte/object accounting. Defaults
   are finite; invalid profiles reject before work. A consumer can tighten them.
4. A finite implementation ceiling that options cannot override. The complete
   frame remains at most 32 MiB for both default and extended profiles; the
   latter widens individual fields/counts only. No 64 MiB frame profile or
   multi-gigabyte implementation is advertised from the u32 wire width.

Scope should be covered by aggregate and explicitly selected consumer field
budgets, not an independently identity-bearing 64 KiB wire rule. Raising scope
must never change its opaque bytes or weaken required nonemptiness.

Malformed framing/semantic invalidity, resource refusal and unsupported
interpretation remain distinct. `ConversationCodecError` derives from
`std::invalid_argument` and carries a typed reason. Exact codes and public API
belong to the owner. Exceeding a budget is not proof that the unexamined frame
is otherwise valid: bounded decoding need not continue to classify deeper
damage. Logical owned-byte/object charges are separate from measured heap/RSS.

Writer/reader compatibility is multidimensional: frame, every field/count,
object/owned-byte budgets and payload/extension support all matter.

| Writer record / reader capability | Required disposition |
| --- | --- |
| Fits the guaranteed base profile; base reader supports its schema | Typed read succeeds |
| Fits the base profile; stricter reader budget is exceeded | Resource-limit result, not corruption |
| Uses an explicitly supported wider profile; base reader budget is exceeded | Resource-limit result, not corruption |
| Fits resources but payload/schema is unsupported | Lossless envelope preservation; typed access reports unsupported |
| Known supported schema contains malformed data | Reject; never downgrade to an unknown opaque payload |
| Unknown critical metadata could alter interpretation | Preserve only in an explicit transport mode; semantic admission refuses it |

A future store must durably associate required reader capabilities with its
write policy and check them on reopen or report explicit resource refusal.
This is a future store contract, not a new DBI implemented by codec options.

## 4. Extensible-envelope decisions

The owner now specifies one canonical envelope with independently versioned,
length-delimited typed payloads, not a separate serializer for agent sessions.
Additive `ConversationMetadata`/`ConversationEnvelope` preserve the Phase 1
aggregate. Explicit adapters refuse any extension or non-Phase-1 payload when
down-converting rather than silently losing retained bytes.

- Stable type identity uses the conversation-domain append-only repository
  registry. Public IDs are not reused. Private numeric IDs require an explicit
  out-of-band issuer/profile and have no portable registered semantics here.
- Envelope version owns common framing and interpretation. Type identity plus
  schema version owns each payload grammar. Unsupported envelope versions still
  reject; adding a type does not reinterpret an existing type/version pair.
- Common metadata extensions have exact length framing, nonzero increasing
  IDs, duplicate rejection and canonical criticality bytes. Unknown critical
  metadata refuses typed consumption; required interpretation changes cannot
  masquerade as advisory metadata.
- Preservation and typed interpretation are separate results. Unknown type or
  schema bytes survive decode/re-encode and read-modify-write exactly; typed
  access reports unsupported. Known malformed payloads cannot take that escape,
  and constructing an opaque value for a supported pair rejects.
- For unknown payloads the envelope can be canonical while the opaque payload's
  logical canonicality remains unknown. Do not claim schema validation or
  canonical admission for bytes whose schema is unavailable.
- Unknown metadata/payloads grant no access, authority, provenance authenticity
  or right to execute anything. A generic codec is not an admission service.

Protobuf unknown-field retention, FlatBuffers additive schema evolution and
Cap'n Proto bounded traversal are useful comparison subjects, not dependency
decisions or proof of this proposed format. No new dependency is selected.
The exact revised grammar, IDs, extension ordering, profiles and error contract
are in section 4.2. New goldens must follow its independent review rather than
the closed candidate's bytes; implementation/qualification is not declared
complete by this design audit.

## 5. Agent-session proof requirements

The corrective proof must encode and decode typed ToolCallObserved and
ToolResultObserved under registered pairs 256/1 and 257/1, rather than calling
arbitrary opaque bytes typed support. Patch and Compaction reserve IDs 258/259
but have no registered typed schemas in this slice. Their illustrative
artifact-only fixtures separately assert normalized evidence invariants and
codec retention/interpretation refusal; opaque round-trip alone proves no
inner payload semantics.

- Tool call: exact request artifact and declared call identity; recording a
  request does not execute it or prove it ran.
- Tool result: exact qualified call reference or explicit unresolved evidence;
  distinguish observed success, observed failure, cancellation and unknown
  outcome. A timeout is not an inferred successful or failed execution.
  Cross-record existence, type, scope and source checks belong to admission.
- Patch: immutable patch artifact; exact source/file context and input/output
  revisions when known, with unknown application outcome retained. Observing a
  patch does not prove it was applied; a claimed applied result needs evidence.
- Compaction: reference-only derivation observation, exact inputs/revisions,
  output/recipe and provenance receipt; no promotion of generated summary to
  independent original evidence or automatic KnowledgeUnit publication.

Ordinary chat, Efi dialogue and Maschinenwerk development sessions are three
fixture contexts, not three core runtimes or proof of provider integration.

## 6. Physical storage boundary

One frame encodes one occurrence/representation revision, never a whole
conversation or database. No Conversation MDBX layout is implemented. Entry
history, representation revisions, current-message fold, source deduplication
and reply/time indexes have distinct query/identity obligations; their physical
keys are not decided here. Canonical text and artifact body placement remain
with their existing owners. Read projections and search indexes are separate
from wire encoding. Zstd and page/storage tuning belong to the physical layer.

Prioritize covering secondary indexes/metadata projections for future queries.
A metadata-only/header reader is a separate measured optimization; skipping an
unvalidated payload must not return a fully validated event. Do not add a
storage engine or public zero-copy lifetime API to answer this design question.

## 7. Qualification requirements established by the audit

The earlier 11 goldens and 21,186,977-byte maximum qualify only the closed
candidate at the source HEAD above. Preserve them with their source context;
they are not revised-envelope compatibility proof, a supported second dialect
or known persisted ConversationStore fixtures. Their 21,186,977-byte maximum
does not qualify the revised profile's maxima.

The old candidate's counting and writing passes both called `write_external`,
which invoked the complete AMXR serializer each time. The maximum fixture had two
source references plus 256 unresolved-link references: 258 occurrences and
516 nested serializations across the two passes. Those temporaries were bounded
by AMXR, but exact event preflight was not allocation-free. The corrective proof
must compare bounded preparation/reuse against that baseline without changing
AMXR or adding an unbounded cache. Preparation must charge retained nested
frames incrementally against aggregate budgets, not build every allowed
reference before discovering that the complete event exceeds its ceiling.

New verification must separately account for fixture construction, size
preflight, encoding, decoding, temporary allocations, owned decoded bytes,
peak live heap and process high-water RSS. Repeat maximum-size qualification
for the revised envelope and every advertised profile. Report directional
single-machine measurements as such, not stable performance guarantees.

Required controls: malformed lengths/counts/tags/nested AMXR, noncanonical
extension order/duplicates, unsupported type/schema preservation, known-invalid
schema rejection, stricter/wider profile compatibility, invalid profiles,
read-modify-write with unknown fields and no implicit execution/admission.
New independent goldens, GNU/MSVC C++17, sanitizers and installed/relocated
consumers follow the agreed revised contract. Existing successful #690 CI
does not qualify the corrective changes merely because the old candidate passed.

The local [resource qualification experiment](../experiments/2026-11-22-amce-envelope-resource-qualification.md)
records source/binary-bound process outputs and timed samples. Default,
extended and implementation-cap fixtures reach exact 32 MiB frames. Preparation reduces
nested serialization/allocation volume but increases peak live requested bytes;
the note preserves this tradeoff and keeps actual process peak working set,
logical budgets, allocation-probe bytes and different-wire latency separate.
This local measurement does not replace new exact-head CI or independent
review, and exact size preflight remains included in encode timing rather
than separately instrumented.

## 8. Binary field and public API review

No existing ByteView/ByteBuffer definition was found by graph lookup or source
fallback. The new explicit C++17 view has no implicit container conversion;
its caller owns the memory and keeps it alive/unmodified throughout the call.
Returned values own their bytes; the input can be reused immediately afterwards.
Empty views are representable, but not complete AMCE frames. No decoder retains
the view. Sizes use subtraction-based bounds before pointer arithmetic/copying.

| Field family | Representation and disposition |
| --- | --- |
| AMCE output/input, unsupported payload, extensions, new tool call token | ByteBuffer / ByteView; arbitrary bytes including NUL/FF, never UTF-8 text |
| Message text, recipe, locator, tool name | Text strings; strict UTF-8 where defined, no normalization |
| Allocation namespace | Exact ASCII token string, fixed identity grammar |
| Existing ScopeId, ArtifactBindingId, SourceRevisionId, provider marker | Integrated opaque identifier carriers backed by string; retain their public value types and byte identity, including non-UTF-8; not converted through text encodings |
| AMXR string scalar | Existing AMXR textual wire contract and validator, unchanged |
| Nested complete AMXR frame | Explicit bounded adapter borrows its existing string frame as bytes; one bounded temporary per occurrence/pass, with no retained aggregate cache after the 2026-11-23 same-grammar comparison |

The integrated opaque identifier carriers are not new arbitrary-body APIs.
Changing their storage types would break independent integrated interfaces;
this correction does not silently migrate them. All new arbitrary binary
containers use bytes. There is no binary-to-text-to-binary transcoding or
whole-domain string replacement. The Phase 1 serializer traverses its fields
by reference; only the explicitly requested value bridge copies an lvalue
(or moves an rvalue). Decoder-to-Phase-1 conversion moves retained fields and
rejects extensions/nonlegacy payloads instead of dropping them.

The public API is field validation/transport, never admission or authorization.
Typed consumption refuses opaque pairs and unknown critical metadata. Advisory
metadata stays attached even when typed consumption is permitted. Allocation
errors propagate, and diagnostic text contains no source identifiers.
