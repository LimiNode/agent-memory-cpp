# Publication coordinator semantic audit

**Date:** 2026-10-25  
**Base:** corrective PR #554 (`0386c942`)  
**Status:** bounded audit; no merge performed

## Contract changes

The authoritative API no longer has an implicit metadata-only overload.
`publish(plan, participants)` requires exactly one participant for each of the
canonical, knowledge-unit, projection, lexical, and manifest roles. An empty
or incomplete vector returns `InvalidPlan` without opening a publication
frontier. Migration/recovery tooling that intentionally advances coordinator
metadata must call the explicitly named `publish_metadata_only(plan)` path.
That path records `MetadataOnly` and can never produce a `Complete` receipt or
an `Exact` pinned read.

This naming boundary is deliberate: a caller cannot mistake a metadata-only
operation for an authoritative publication by selecting the old one-argument
convenience method (that method was removed).

## Abstraction inventory

The audit covered the nine coordinator/read/participant headers and
implementations in the bounded MDBX slice: 2,074 physical lines, 11 concrete
participant/scope classes, 2 participant interfaces, and 5 role values.

| Component | Retained invariant or ownership responsibility |
| --- | --- |
| `MdbxPublicationWriteScope` | one owner-thread MDBX transaction; only scope commits/aborts |
| `MdbxPublicationParticipant` | context admission, role declaration, in-transaction apply/validate, abort reset |
| five write participants | copy prepared payloads into their authoritative row family and validate the resulting row |
| `MdbxPublicationReadScope` | one owner-thread pinned read transaction and completion classification |
| six read participants | read one storage family through the supplied transaction; no nested transaction |
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
| `Exact` | metadata-only receipt, missing schema, independent canonical mutation, incomplete roles |
| atomic | failing participant leaves frontier/receipt and all rows unchanged |
| retry/idempotency | the same prepared participants can retry after rollback; identical metadata replay is stable |
| fenced | stale fencing and malformed durable profile are rejected |
| durable | reopen preserves the committed frontier/receipt |
| pinned | read scope requires coordinator metadata, matching receipt/evidence, lexical pinned consistency and authoritative row agreement |

The complete fixture applies all five participants through one coordinator-owned
MDBX transaction. The former fixture that wrote families independently is kept
only as a negative case and asserts non-`Exact` completion.

## Complexity accounting

This pass removed one public method and its implementation wrapper:

- public coordinator methods: **-1** (`publish(plan)`);
- forwarding implementation wrappers: **-1**;
- classes/interfaces/virtual dispatch branches removed: **0** (no safe deletion
  was identified without weakening a tested ownership or evidence invariant);
- duplicated serializers removed: **0** (the remaining encoders have distinct
  wire compatibility contracts).

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
