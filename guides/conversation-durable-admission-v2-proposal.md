# P4-4 typed admission, witness2 and read-only API delta

- Status: **DRAFT FOR EXACT CONTRACT APPROVAL; NOT IMPLEMENTED**.
- Date: 2026-11-26. Owner: conversation storage/ingestion.
- Base: `main@8a092d515f99ed34e50ffbc7642cd2fb80e4ef8d`, tree
  `ffe987701da42600079c36d54b69537d75532c6c`.
- Owner-approved direction: fresh opt-in witness2 profile, no dual-reader or
  migration; typed source state events and a common read-only interface.
  This later choice supersedes the earlier request for active v1/v2 reading.
- Review gate: approve the concrete types/grammar/profile/API below before P4-4
  implementation. This proposal changes no executable API or persisted bytes.

Owners remain [source import](conversation-source-import.md),
[reference semantics](conversation-reference-store.md),
[physical grammar](conversation-durable-physical-contract.md),
[identity](identity-policy.md) and [DBI inventory](dbi-manifest.yaml). This is a
bounded delta, not another event model, storage engine or replacement specification.

## 1. Typed source DTO delta

Append existing domain types directly; introduce no duplicate source structs:

```cpp
using ConversationSourcePayload = std::variant<
    ConversationSourceMessage, ConversationSourceToolCall, ConversationSourceToolResult,
    ConversationEdited, ConversationTombstoned, ConversationRestored>;
```

The reused fields are exactly `Edited{EntryRef affected, ConversationContent
replacement}`, `Tombstoned{EntryRef affected}` and `Restored{EntryRef affected,
EntryRef retained_state}` under their existing `Conversation*` type names.
`ConversationSourceObservation` retains the common conversation/scope, source
binding/evidence, producer, actor/observer/role and times unchanged. Normalize
each new alternative into the same existing nested `ConversationPayload` type.
AMCE type IDs/schemas and AMXR bytes do not change. Update preflight, explicit
exception-safe copying, fieldwise witness equality and normalization together;
handle valueless/unsupported variants explicitly. Variant indices are not wire tags.

Every state event is a new source occurrence with revision 1, its own occurrence
key, witness and original receipt. Representation `correct()` still revises one
existing occurrence under CAS and cannot change its family/affected/retained
targets, original mapping or original replay receipt.
Retained evidence must support the observed change; differences between snapshots
do not authorize inventing an unobserved Edit/Tombstone/Restore occurrence.

For first admission, both targets must be valid exact retained refs in the same
full conversation and admitted scope. Resolve chain membership from exact
history, never external message IDs or latest revisions. Restore's retained
state must carry content in that same chain: a prior Restore is allowed and
selects its frozen exact `content_ref`; a tombstone/tool record is not content.
The witness retains the declared `retained_state`, not this derived content ref.

Missing/out-of-order exact state, wrong family/chain or cross-conversation target
returns `InvalidRequest`, with no mapping, ID, generation or counter change.
There is no unresolved state placeholder or automatic retry queue. The host may
retry after dependencies are admitted. Request/access mismatch remains `Denied`;
missing/hidden enclosing conversation remains `NotFound`. Refusals expose no
target ref/count. A bounded target/ancestry lookup that cannot finish returns
`ResourceLimit`, not a shortened chain or false missing result. Charge every
visited/decoded value and retained scratch to the finite transaction/work bounds.

Sibling/non-head changes are admitted evidence yielding `Ambiguous`; an edit
after tombstone also yields `Ambiguous`, never implicit restoration. Creation
and Snapshot remain separate roots even if external IDs match. These are the
unchanged reference-oracle rules, not new last-writer-wins semantics.

## 2. Witness2 grammar and replay

Reuse the physical owner's big-endian `U8/U16/U32/U64`, `B`, `O`, `C`, `R`,
`source_binding`, `actor`, `content` and `call_target` productions and their field
validation/caps. `B` is U32 length plus bytes; `O(X)` is U8(0), or U8(1) plus X.
`C` includes namespace and numeric ConversationId; `R = C | U64(EntryId) |
U64(EventRevision)`, with valid nonzero IDs/revision. Content preserves attachment
order and absent versus empty text under the existing content validity rules.

```text
witness2 = U16(2) | C | B(scope bytes) | source_binding | B(producer)
           | O(actor) | O(observer) | O(llm_role)
           | O(i64(source_unix_us)) | O(i64(observed_unix_us)) | source_payload
source_payload = U8(1) | U8(Created=1 | Snapshot=2) | content
              OR U8(2) | B(call_id) | B(tool_name) | B(original_request)
                         | O(B(effective_request))
              OR U8(3) | call_target | B(call_id) | U8(origin)
                         | U8(reported_outcome) | O(B(result artifact))
                         | O(U8(is_error)) | O(i64(exit_code))
              OR U8(4) | R(affected) | content(replacement)
              OR U8(5) | R(affected)
              OR U8(6) | R(affected) | R(retained_state)
```

Tags 1/2/3 and their bodies, all common fields, role/origin/outcome tags and signed
time/exit-code encoding retain their physical-owner definitions. Every observation
in this profile, including old event families, uses header `U16(2)`. No v1 witness
is admitted in a v2 row. Unknown witness versions/tags return `UnsupportedSchema`;
malformed lengths/presence/trailing data are corruption, resource refusal is
separate. Preflight bounds both exact targets and Edit content before owning copies.
Keep `ByteBuffer` output/owned witnesses and borrowed `ByteView` input; no binary
string conversion or new mandatory dependency.

Replay uses decoded, validated source-shaped **fieldwise equality**, extending
`same_witness()` to the three new alternatives. Compare exact targets and every
common source/attribution/classification field, not only normalized AMCE or
serialized witness bytes. Authorized structural/input checks precede lookup;
lookup precedes new-entry quotas and state-dependent target resolution. Identical
replay returns original@1/original generation even after corrections; a changed
target/revision/content/recipe/evidence under the same key is Conflict. Neither
outcome allocates a new Entry or changes counters. There is no cross-profile
runtime replay: historical v1 versus new v2 DTO equivalence is a fixture check,
not a dual-reader or automatic database conversion.

## 3. Fresh storage profile, not migration

Exact proposed marker: `conversation_durable.v2.be.keys.sha256.values.witness2`.
The initial `v2` versions the storage profile, not AMCE, keys or outer values.
Keep the existing three DBI names (including their `_v1` physical-layout suffix),
key tags/bytes, logical source-key recipe, outer `value_schema=1`, row bodies and
descriptor schema 1. Only witness framing becomes 2. Do **not** globally change
`Reader::version()` to expect 2: it also reads independent unchanged grammars.

Use the new marker in the profile body **and** every value-digest preimage.
Consequently newly created v2 wrapper digests differ; AMCE/AMXR digests/bytes do
not. No old wrapper is rehashed or rewritten. Keep profile marker validation
before version-specific wrapper-digest interpretation, so a well-framed foreign
profile is `UnsupportedSchema`, not a misleading integrity failure.

| Backend / selected namespace | Required result |
| --- | --- |
| v2, all three DBIs absent, explicit initialization allowed | Create three DBIs + witness2 marker/profile atomically |
| v2, valid v2 profile | Read/write v2 only, existing descriptor/quota compatibility checks |
| v2, existing witness1 profile, even with initialization allowed | UnsupportedSchema; no marker/row/counter/DBI changes |
| historical v1 backend, existing v2 profile | Explicit unsupported-profile refusal before journal mutation |
| either, partial layout, missing/unknown marker or unsupported descriptor | Refuse; never stamp, repair, reset or delete existing tables |

Preserve the no-CREATE probe of all three DBIs before deciding initialization.
`allow_initialize` is not upgrade permission. Same-prefix mixed profiles are not
supported; use a fresh environment or explicitly fresh independent prefix and
host-coordinated allocation namespace, never rebind committed identities or run
competing authoritative copies. Refusal preserves logical journal/profile bytes;
ordinary MDBX lock-file/bookkeeping I/O is not claimed absent.

There is one active v2 encoder/decoder, no v1 fallback, mixed writer path, migration
tool or bulk re-encoding. Preserve historical v1 fixtures/golden bytes/receipts
and reproducibility via `main@8a092d51`; use that historical implementation outside
the active v2 path. New independent v2 goldens supplement, not replace, that
evidence. Future format changes require explicit marker/witness versions and a
separately reviewed compatibility or migration decision.

Activation precondition: the owner confirms no valuable v1 database requires
current-library access. This has **not** been established by a global user-data
inventory. Known valuable v1 data reopens the compatibility decision; nothing in
this proposal authorizes deletion, overwriting or migration of that data.

## 4. Read-only interface delta

Keep shared DTOs in the existing self-contained `ConversationStore.hpp` to avoid
an unnecessary header/type move. Proposed declarations (same read signatures):

```cpp
class IConversationHistory {
public:
    virtual ~IConversationHistory();
    [[nodiscard]] virtual ConversationReadResult read_exact(
        const ConversationStoreAccess&, const EntryRef&, const ConversationReadLimits& = {}) const = 0;
    [[nodiscard]] virtual ConversationReadResult read_history(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& = std::nullopt) const = 0;
    [[nodiscard]] virtual ConversationReadResult read_current(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& = std::nullopt) const = 0;
protected:
    [[nodiscard]] static ConversationContinuation make_continuation(
        std::shared_ptr<const ConversationContinuation::BackendState> state);
    [[nodiscard]] static const ConversationContinuation::BackendState* continuation_state(
        const ConversationContinuation& continuation) noexcept;
};
class IConversationStore : public IConversationHistory {
public:
    ~IConversationStore() override;
    [[nodiscard]] virtual ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&) = 0;
    [[nodiscard]] virtual ConversationWriteResult append(
        const ConversationStoreAccess&, const ConversationEnvelope&) = 0;
    [[nodiscard]] virtual ConversationWriteResult correct(
        const ConversationStoreAccess&, const ConversationEnvelope&,
        EventRevision expected_revision, const std::string& reason_code) = 0;
};
```

`IConversationImportSink::history()` becomes `[[nodiscard]] virtual const
IConversationHistory& history() const noexcept = 0;`; its concrete reference
implementation returns the same reader type with `override`. Move continuation
friendship/mint-inspect ownership to `IConversationHistory`; helper/destructor
definitions stay in compiled storage code, and the private durable helper-only
`ContinuationAccess` derives from the reader, not a fake mutable store. No token
format, access rule, budget, lifetime or owning-result semantics change.

The sink owns the reader for its lifetime and returns a borrowed const ref;
reads/continuations reauthorize each call, own results and retain no MDBX txn/view.
No cast/mutable escape or artificial `append()` implementation is introduced.
Durable exposure stays internal until all three reads, especially Current, are
real and qualified; no fake empty/Complete/NotFound/Unsupported read result.

This intentionally changes pre-release source/ABI surface: explicit bindings of
`sink.history()` to `const IConversationStore&`, exhaustive variant visitors and
layout/vtable/pointer-to-member assumptions require updates/rebuild. Existing
store subclasses keep their read/write overrides; ordinary `auto`/read callers
retain their operations. Update forward declarations, friendship, sink signatures,
umbrellas/header probes and installed/relocated consumers; promise no stable ABI
or deprecated parallel API solely for this intermediate contract.

## 5. Usage and acceptance gate

Given fully host-prepared observations with distinct source occurrence keys,
retained evidence and matching access/conversation, a host function returning
`ConversationWriteResult` can use this sequence:

```cpp
edit.payload = ConversationEdited{original_ref, replacement};
const auto edited = sink.import_observation(access, edit);
if(edited.status != ConversationWriteStatus::Applied &&
    edited.status != ConversationWriteStatus::NoChange) return edited;
deletion.payload = ConversationTombstoned{edited.ref.value()};
const auto deleted = sink.import_observation(access, deletion);
if(deleted.status != ConversationWriteStatus::Applied &&
    deleted.status != ConversationWriteStatus::NoChange) return deleted;
restore.payload = ConversationRestored{deleted.ref.value(), original_ref};
return sink.import_observation(access, restore);
```

The reading caller needs no write interface:

```cpp
const IConversationHistory& history = sink.history();
const auto page = history.read_current(access, original_ref.conversation);
```

`original_ref` is the exact retained content revision, not a latest lookup. Each
call is one transaction, not an atomic batch; callers handle every refusal and
retain the committed prefix/retry position. The snippet is proposed API usage,
**not compiled or executed evidence**.

| Required acceptance after exact approval | Expected observation |
| --- | --- |
| Independent witness2 goldens, all six source alternatives | Lossless fields/targets, canonical bytes, absent/empty/binary distinctions; invalid tags/lengths/budgets refuse |
| Historical v1 fixtures vs v2 common-family fixtures | Preserve v1 evidence; semantically equal DTO inputs without active v1 runtime decoding |
| Cross-profile open, with/without initialization | Old/new readers reject opposite marker; logical rows/marker/counters unchanged, no DBI creation/deletion |
| Replay/conflict at quota, after correction and fresh-process reopen | Original@1/generation preserved; changed exact target/recipe/evidence conflicts; fieldwise equality |
| Created/Snapshot, siblings, tombstone, edit-after-tombstone, Restore and Restore-to-Restore | Every admitted prefix/frontier equals unchanged oracle, including content corrected after earlier Restore |
| Missing/out-of-order/foreign/denied targets and exhausted ancestry work | Explicit refusal, no fabricated events/latest substitution or counter/ID gap |
| Allocation/precommit rollback, unacknowledged commit, concurrent admission | Atomic mapping/witness/history/allocator/generation, mapping_count == entry_count; retry/uncertain outcome retained |
| Corrections between pinned pages, wrong-view/foreign/reopened tokens, policy changes | Frozen frontier/exact content and current authorization; no MDBX handles across calls |
| GNU/MSVC/Linux, MDBX-off, header probes, installed/relocated C++17 read-only consumers | Intended source changes verified; no optional dependency leaks or synthetic append |

All new implementation/grammar/consumer checks above are **NOT RUN**. After owner
approval, implement coordinated admission and bounded Current in P4-4, then run
the unchanged [Current screen](experiments/2026-11-24-conversation-durable-current-plan.md).
Only after its semantic gate proceed to separate P4-5 scale/full qualification.
No automatic merge, migration, release or new DBI is authorized by this draft.
