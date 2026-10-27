# Publication coordinator semantic audit

**Date:** 2026-10-25
**Base:** PR #553 (`d5e82f731bdb0fb0f2d1673a95d7f414e45986d5`)
**Corrective head:** read-authority closure (local, before CI)
**Status:** bounded audit; no merge performed

## Contract changes

The authoritative API no longer has an implicit metadata-only overload.
`publish(plan, participants)` requires exactly one participant for each of the
canonical, knowledge-unit, projection, lexical, and manifest roles. An empty
or incomplete vector returns `InvalidPlan` without opening a publication
frontier. Migration/recovery tooling that intentionally advances coordinator
metadata must call the explicitly named `publish_metadata_only(plan)` path.
That path records `MetadataOnly` and can never produce a `Complete` receipt or
a `TargetExact` pinned read.

The read completion name is deliberately bounded. `TargetExact` proves one
receipt-selected resource, unit, projection revision and lexical membership in
one pinned transaction. It does not prove that the scope contains no unrelated
lexical units or historical projection revisions, and it does not claim that
legacy writers have been globally excluded.

This naming boundary is deliberate: a caller cannot mistake a metadata-only
operation for an authoritative publication by selecting the old one-argument
convenience method (that method was removed).

## Abstraction inventory

The audit covered the nine coordinator/read/participant headers and
implementations in the bounded MDBX slice: 2,074 physical lines, 11 concrete
participant/scope classes and 2 participant interfaces. Role values are no
longer part of the participant API.

| Component | Retained invariant or ownership responsibility |
| --- | --- |
| `MdbxPublicationWriteScope` | one owner-thread MDBX transaction; only scope commits/aborts |
| `MdbxPublicationParticipant` | context admission, in-transaction apply, abort reset |
| five write participants | copy prepared payloads into their authoritative row family and validate the resulting row |
| `MdbxPublicationReadScope` | one owner-thread pinned read transaction, closed six-family admission, and target completion classification |
| six final read participants | read one storage family through the supplied transaction; unknown, duplicate and missing families are rejected |
| `MdbxPublicationCoordinator` | profile/fencing/frontier CAS, receipt/evidence durability, role completeness, replay |
| `LambdaParticipant` | private adapter that lets the coordinator execute metadata logic under the write-scope ownership check |

No class was removed merely to reduce names: each retained class either owns a
transaction boundary, performs a storage-family read/write, or supplies an
independent evidence check. The one-argument `publish(plan)` wrapper was
removed because it added no invariant and made the weaker path too easy to
call accidentally.

## Executable counterexamples

The focused MDBX tests now reject or downgrade these cases:

| Guarantee | Counterexample |
| --- | --- |
| `TargetExact` | metadata-only receipt, missing schema, mismatched frontier, corrupt latest/per-publication receipt, corrupt binding, wrong unit, missing lexical target, projection/manifest/canonical mutation |
| atomic | failing participant leaves frontier/receipt and all rows unchanged |
| retry/idempotency | the same prepared participants can retry after rollback; identical metadata replay is stable |
| fenced | stale fencing and malformed durable profile are rejected |
| durable | reopen preserves the committed frontier/receipt |
| pinned | all six trusted families read through one scope-owned transaction; a forged reader is rejected before any callback |
| bounded target | an extra historical projection and an unrelated lexical unit preserve `TargetExact` but are not represented as scope-wide completeness |

The complete fixture applies all five participants through one coordinator-owned
MDBX transaction. The former fixture that wrote families independently is kept
only as a negative case and asserts non-`TargetExact` completion.

## Complexity accounting

Across the complete semantic-audit series, the earlier pass removed one public
method and its implementation wrapper. This read-authority corrective adds the
minimum persisted-authority lookups and admission state needed for the new
counterexamples:

- public coordinator methods: **-1** (`publish(plan)`);
- forwarding implementation wrappers: **-1**;
- public methods in this corrective: **+2**
  (`binding_in_transaction`, `same_publication_evidence`);
- public enum surface: `MdbxPublicationReadCompletion::Exact` was renamed to
  `TargetExact`; no scope-wide replacement was added;
- production diff for this corrective: **+176 / -73 lines**;
- read families/classes/interfaces: **0 added / 0 removed**;
- classes/interfaces/virtual dispatch branches removed: **0** (no safe deletion
  was identified without weakening a tested ownership or evidence invariant);
- duplicated serializers removed: **0** (the remaining encoders have distinct
  wire compatibility contracts).

The durable checks add two coordinator point lookups (per-publication receipt
and resource binding). They add no storage-family scan. The target classifier
does not call the whole-frontier lexical validator: it scans only for the
receipt-selected target membership, while descriptor/snapshot structure is
checked independently. This prevents unrelated lexical units from being
silently promoted into a claim of scope-wide completeness.

The audit intentionally records zero speculative deletions rather than
replacing one abstraction with a larger generic framework. Further reduction
requires a separate API migration decision, not a cosmetic rewrite inside this
corrective PR.

## Remaining boundary

The coordinator proves only publications performed through its shared MDBX
context. It does not globally quiesce arbitrary legacy writers. Production
activation therefore still needs an external writer-registration/quiescence
gate; a post-commit bypass mutation is detected by read-side evidence and
downgrades completion, but is not prevented by this bounded slice.

## Proof-integrity corrective

Complete admission is a closed boundary: only the five `final` concrete storage
participants are accepted. Unknown subclasses, duplicates, incomplete sets and
wrong contexts are rejected before `apply()`. Role declaration and virtual
`validates()` self-attestation were removed; concrete validators read their own
authoritative rows in the coordinator transaction.

Evidence coverage:

| Field | Authoritative source | Comparison | Counterexample |
| --- | --- | --- | --- |
| latest receipt | `meta/latest-receipt` | complete identity, payload, target and sequence | malformed latest receipt |
| publication receipt | `receipt:<publication_id>` | exact agreement with latest receipt | malformed per-publication receipt |
| resource binding | `binding:<resource_id>` | every `PublicationEvidence` field agrees with receipt target | missing, malformed, wrong resource, stale target, changed projection/manifest digest, changed generation |
| scope frontier | `meta/frontier` | exact scope and sequence agreement with both receipts | independently changed frontier sequence |
| resource generation | manifest revision + unit source | exact identity/generation | unrelated resource |
| canonical document/revision | canonical row | document id + revision | post-commit edit |
| unit id/revision | unit row | id, scope, revision, source generation | wrong unit |
| projection version/payload | projection row | full version + SHA-256 payload digest | changed body |
| manifest state/schema/payload | manifest row | state, schema, payload version | state mutation |
| manifest topology/bindings | manifest row | SHA-256 over persisted revision, records and pending reclaim | membership mutation |
| lexical descriptor/membership | lexical rows plus target checks | pinned descriptor/snapshot and selected target membership | target erase; unrelated unit remains allowed |

`manifest_revision` is not claimed because `ResourceManifest` has no such
durable field. `manifest_binding_digest` is computed from the persisted
manifest fields above. The lexical adapter reports only lexical rows; the read
scope does not fabricate active-binding flags for unrelated units. A fresh
context after destruction of all prepare objects revalidates the complete
fixture and must reach `TargetExact`.

## Exactness boundary

`Complete` describes the write receipt: all five authoritative participant
families were validated and committed with coordinator metadata in one MDBX
transaction. `TargetExact` describes a later pinned read of the target selected
by that receipt. The receipt's projection version is the active selection for
this bounded check; older stored revisions are historical rows. An unrelated
lexical unit is outside the selected target and does not invalidate the result.

Neither state proves complete token-partition coverage for an arbitrary query
nor an exact scope-wide publication snapshot. Those stronger guarantees require
an active-revision/partition authority and global legacy-writer exclusion that
this experimental stack does not provide. The implementation stops at the
bounded result instead of fabricating those guarantees.

## Corrective pass (2026-10-27)

The follow-up pass closes three concrete evidence gaps without introducing a
new coordinator abstraction:

1. Every write participant constructor now rejects a null or mismatched storage
   connection before a writable publication scope can be opened. The focused
   test constructs all five participants with a declared context from one
   environment and an underlying store from another; construction fails and
   the coordinator frontier remains unchanged.
2. Knowledge-unit validation rereads the authoritative unit row through the
   coordinator transaction and checks unit identity, scope, revision, resource
   identity, and resource generation. The in-memory result returned by the
   create operation is no longer sufficient evidence for `Complete`.
3. Read-side target evidence selects a source by the receipt's resource ID and
   generation. It no longer assumes `sources.front()` is the target, so
   multi-source envelopes cannot silently downgrade or promote a target based
   on source ordering.

These checks preserve the bounded semantics above: they do not establish
scope-wide lexical completeness or global legacy-writer exclusion.
