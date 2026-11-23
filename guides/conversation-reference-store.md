# Conversation Core Phase 3A: backend-neutral reference store

Status: Phase 3A reference subset implemented for independent review on the
accepted Phase 2 dependency
`7bb88efc078f3b7c3c9a3fb49235d472bbaef717`. This guide owns the bounded
reference-store slice; [the event contract](conversation-event-contract.md)
owns identity/event meaning and AMCE. No codec/wire change, source importer,
MDBX schema/adapter, graph/search activation or runtime execution is included.

## Admission and authority

The reference backend is in-memory, instance-owned and serialized by a mutex.
It does not authenticate callers. The trusted host must authorize each operation
and supply the exact scope and an access-policy binding covering the relevant
principal/permissions/policy revision. Scope, actor, observer, LLM role and a
cursor are never permission tokens. Scope mismatches return no data, counts or
target identities. This is not a new generic authorization subsystem. A commit
generation is a store-local snapshot coordinate, not a scope-specific event count.

The host supplies allocation-qualified conversation/entry identities. A new
conversation permanently binds its ref to one scope within the instance. New
entries must use revision 1 and the next ascending EntryId (starting at 1),
committed under the write lock. No lower ID can commit behind a higher one.
Failed admission consumes no ID; there is no persistence/reopen allocator claim.
Retained scope bytes are bounded by the configured codec frame/owned-byte budgets
and charged to the store's byte capacity; this is a local admission policy, not a
new domain invariant of `ScopeId`.
An exact original-entry replay compares canonical AMCE bytes at that known local
identity: identical is NoChange, different is Conflict. Replaying an old original
does not replace a later corrected representation. No source-key, text, timestamp
or call-token deduplication is performed; importer identity recipes remain 3B.

Only built-in typed payloads with interpretable metadata are admitted. AMCE
validity/canonical resource checks precede mutation, but cannot authenticate
source claims or artifact existence. Unknown payload/critical metadata retention
remains a codec capability, not semantic store admission. Noncritical extensions
remain intact. All committed revisions retain canonical AMCE bytes and exact
source, actor/role/observer, links and tool observations.

Representation correction is a separate expected-revision operation. A stale
expected revision conflicts before NoChange. The proposed next revision is
current+1; equal content after normalizing only the local revision is NoChange
and consumes neither revision nor commit generation. A changed representation
appends an immutable revision and correction receipt with prior revision and a
bounded reason code; old/new adapter recipes and exact evidence remain readable
in the respective envelopes. Identity/source-occurrence bindings and structural
message/call targets and all metadata link kinds/targets cannot be changed by this
correction API. A retarget/reconciliation operation is outside this minimal slice. Every refusal
must leave history, counters and current views unchanged. Allocating write-result
copies are prepared before commit; allocation exceptions also leave the store
unchanged. The changed correction requires source evidence on both revisions.

Resolved targets must identify exact retained, accessible revisions. Explicit
unresolved links/tool-call targets remain unresolved; no nearest-time or
call-token search fills them. A resolved tool result must target an exact
ToolCallObserved with matching call token, not an arbitrary same-token run.
Source issuer/correlation authenticity remains a host/importer obligation.

## Deterministic current-message fold

At a selected commit frontier, choose the latest admitted representation of
each occurrence at or before that frontier, then fold in ascending EntryId.
Creation and snapshot each start their own message chain; matching external
object IDs do not merge them or fabricate source history. Snapshot origin stays
explicit, not an inferred creation event.

Edits, tombstones and restores reference an exact existing state in that chain.
Chain membership follows retained exact targets, not a substituted latest target.
Representation correction does not create another source state/occurrence;
the occurrence's immutable chain identity is unchanged. A linear successor may
advance that occurrence even when its local representation was corrected.
Sibling/non-head source changes remain canonical observations but make the
single-current view explicitly Ambiguous; no last-writer-wins state is invented.
That ambiguity persists in this slice until an explicit future reconciliation.

Tombstones hide current content without erasing prior revisions. An edit targeting
a tombstoned head is retained but makes Current Ambiguous; it cannot implicitly
assert a source restoration. Restore selects the exact retained same-chain content
state, not its latest corrected revision;
a tombstone is not content. Preserve root/state/content references and enough
origin metadata to distinguish the selected evidence. Tools are history records,
not message edits, dispatches or outcomes inferred from mere record presence.

## Bounded reads and frontiers

Exact reads return the requested immutable revision or an explicit refusal.
History pages enumerate all admitted revisions in `(EntryId, revision)` order.
Current-message pages enumerate roots in EntryId order and report live/deleted/
ambiguous state explicitly. All returned values own their data; no borrowed
storage view survives a call. Artifact bodies are never loaded.

Every successful append/correction advances a commit generation; NoChange and
Conflict do not. A stable history/current continuation pins that generation,
not merely the maximum EntryId, so later corrections cannot move an old page.
The opaque in-memory continuation binds instance, allocation/conversation,
scope, view/order, seek position and host policy binding. Revalidate it and
current host authorization on every use. Wrong instance/scope/view/policy,
invalid/default-as-resume tokens and unavailable frontiers do not resume latest.
The interface exposes protected mint/inspect helpers for backend-owned opaque
state, rather than coupling the public cursor to this reference implementation.
Each backend must reject foreign state types/instances. Tokens are not a
persisted wire format or cross-process cursor; all admitted history is retained
for the lifetime of this reference instance.

Bound entries/output bytes, visited records and decoded input bytes, with finite
defaults and implementation caps. Zero is a real zero budget, never unlimited.
Stop before an over-budget item; distinguish Partial, OversizedItem and
BudgetExhausted from Complete. An oversized first item is not skipped; its
continuation keeps the same seek so a larger budget can retry. Byte charging
uses declared canonical-frame bounds, not an asserted peak-heap/RSS guarantee.
`visited_records` counts complete frame decodes (including restore-content
decodes), not ordered-map or binary-search comparisons. History resume starts at
the exact revision offset without rescanning its returned prefix. Storage charges
count canonical frames, correction reason bytes and retained conversation scope
bytes, not container/allocator overhead. Exact/history output charges count frame
and reason bytes. Current output conservatively charges the sum of the origin,
selected state and content-source frames, even when they are the same frame.
Report consumed work. Current folding must cover its selected input frontier
before exposing any state as current: exhausted input budgets return no
unqualified current content, even if some prefix was examined. Output paging
may then return a complete prefix plus a fixed-frontier continuation.

The reference implementation repeats the complete selected-input fold for each
Current output page; a page limit is not an incremental-work or peak-memory
limit. See the [pagination screen](experiments/2026-11-24-conversation-current-pagination.md)
for measured decode amplification and the bounded reference-profile trade-off.
This is not a long-dialogue serving-performance claim or a mandate for a cache.

## Qualification and remaining boundaries

The [Phase 3A qualification report](audits/2026-11-23-conversation-reference-store-qualification.md)
records the exercised configurations and remaining gaps; it is not another
event or storage-format specification.

Required deterministic fixtures cover immutable exact recovery, unchanged
replay, stale corrections, correction receipts, edit/delete/restore, sibling
ambiguity, snapshot-only history, same call tokens in independent observations,
partial/unresolved results, scope isolation, wrong/stale continuations, old
frontiers after correction/append, and input/output budget boundaries. Test
concurrent admission order and failed-operation atomicity as well as happy paths.
Public headers and an installed consumer must compile under C++17.

These tests do not qualify SDK event classification, external-source dedupe,
artifact authenticity, full host policy, durable retention/erase, crash recovery
or MDBX. The eight Phase 2 semantic scenarios must later be tested against real
normalized importer input in Phase 3B, not only hand-constructed codec values.
Phase 4 owns persistence; Phase 5 owns the complete Conversation Core profile;
Phase 6 owns the independent derived-knowledge bridge.
