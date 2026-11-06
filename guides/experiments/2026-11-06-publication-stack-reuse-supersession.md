# Publication coordinator reuse and supersession audit (2026-11-06)

**Status:** `REVIEW_GATE` — this is a read-only disposition of historical
branches. It does not merge, restack, activate a writer, or make the
historical coordinator API a production dependency.

## Scope and evidence

The audit covers the coordinator sequence `#544 -> #549 -> #550 -> #551 ->
#552 -> #553 -> #554`, plus the separate legacy-writer gate in `#555`. It was
performed against `main@4e79a6a4acc9fe4c7762a35b9812b7d6bcf51fe3` (including
#579 typed revision-bound retrieval, #582 release-profile and #583 artifact
identity contracts) and compares
the historical files with the current B3 publication substrate, the typed
revision-bound retrieval slice (`#579`), the first-agent snapshot admission
work, and the planned ArtifactCatalog/File CAS and ResourceImporter recovery
contracts. Historical claims are source-level comparisons; no coordinator
implementation is silently imported into main by this document.

The current main equivalents are `DerivedPublicationAdmission`,
`MdbxDerivedPublicationStore`, `MdbxCurrentDerivedPublicationReader`, and the
resource-frontier snapshot tests. They provide admission, current-read and
frontier evidence, but do not yet provide a coordinator-owned multi-family
write transaction or a File CAS publication protocol.

## Feature/source-ownership matrix

| Invariant | Original PR / file / symbol | Existing equivalent on main | Missing behavior | Reusable implementation | Redundant implementation | Required change | Release priority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Coordinator owns the write transaction | #549 `MdbxPublicationWriteScope::{begin,commit,abort}` | No equivalent; stores accept caller transactions independently | One owner must span canonical, unit, projection, manifest and lexical writes | Transaction boundary and abort semantics | Per-participant transaction wrappers | Re-express as one narrow storage seam after ArtifactCatalog/CAS contracts settle | P0 |
| Single-writer fencing/ownership | #549/#550 `MdbxPublicationCoordinator` ownership token | B3 frontier admission detects stale state, not active writer leases | Concurrent legacy/coordinator writers can still race without an operational fence | Token shape and fail-closed ownership checks | Participant-local “owned” flags without durable lease | Define durable writer epoch and legacy admission/quiescence receipt | P0 |
| Atomic canonical/unit/projection/manifest publication | #550 coordinator commit path; #551 authoritative participants | `DerivedPublicationAdmission` validates derived rows after publication | No atomic cross-family commit or rollback of all authoritative rows | Plan/evidence model and rollback tests | Participant callbacks that can publish separately | Implement only with a storage transaction that owns all row families | P0 |
| Lexical participation | #552 `MdbxProjectionLexicalPublicationParticipants` | `MdbxCurrentDerivedPublicationReader` reads current derived evidence; no writer | Lexical rows and projection must share target revision/generation | Target-bound evidence fields and lexical digest checks | Separate lexical callback that advances frontier | Bind lexical generation and digest to the same publication transaction | P1 |
| Pinned reads | #553 `MdbxPublicationReadScope::finish` | B3 current reader and #579 typed revision-bound retrieval | Pinned snapshot must prove one transaction and reject mixed frontier evidence | Revision-bound result types and fail-closed checks | Enum assignment/receipt-only “PinnedSnapshot” claims | Require transaction identity/frontier proof, not an assigned enum | P0 |
| Target-level vs scope-wide guarantee | #554 corrective validator and read scope | `RevisionBoundRetrieval` is target/revision bounded | Scope-wide Exact must not be inferred from one target's evidence | Target-level status vocabulary and negative tests | Global Exact shortcut over contradictory partition evidence | Make scope completeness explicit and reject contradictory partitions | P1 |
| Independent File CAS publication | Not present in #544–#555 | Planned first-agent ArtifactCatalog/File CAS branches | Content-addressed body/blob publication is outside MDBX coordinator scope | None yet; use CAS API once landed | Treating a digest as if it published bytes | Keep CAS upload/commit independent, then atomically publish artifact reference | P0 |
| Source/body digest and content-token binding | #551/#552 evidence fields | Canonical content and B3 derived admission carry revision/digest evidence | No end-to-end proof that source bytes, body digest and token payload are the same artifact | Evidence comparison helpers | Duplicated serializers and IDs without byte binding | Reuse canonical digest API and add content-token binding at importer boundary | P0 |
| Crash recovery | #554 rollback/corrective tests | MDBX resource importer has recovery/fencing protocols; B3 snapshot admission is fail-closed | Recovery must reconcile partial row families and CAS references | Counterexample scenarios and receipt vocabulary | Assuming transaction callbacks imply durable recovery | Specify journal/commit marker and replay policy before writer activation | P0 |
| Legacy writer admission and deployment quiescence | #555 writer activation gate | No automatic quiescence on main | Old writers can advance rows/frontier outside coordinator | Operational gate format and mixed-writer negative cases | Treating a green coordinator test as deployment fencing | Require explicit quiescence/fence receipt and versioned writer epoch | P0 |

## Historical PR dispositions

| PR | Disposition | Reason |
| --- | --- | --- |
| #544 | `REUSE SELECTIVELY` | `PublicationPlan` and evidence vocabulary are useful; its whole coordinator surface is not a release contract. |
| #549 | `REQUIRES REDESIGN` | The transaction-owner idea is valid, but must be rebuilt around the eventual ArtifactCatalog/CAS and one durable writer fence. |
| #550 | `SUPERSEDED` | Coordinator metadata and generic serialization overlap with B3 admission/frontier evidence and would create a second source of truth. |
| #551 | `REUSE SELECTIVELY` | Authoritative participant checks can inform a future adapter after byte/revision binding is moved to canonical/CAS APIs. |
| #552 | `REUSE SELECTIVELY` | Projection/lexical evidence is useful, but separate callbacks must not publish or advance a frontier independently. |
| #553 | `REQUIRES REDESIGN` | Pinned-reader shape is useful, while the historical proof is insufficient for a production snapshot guarantee. |
| #554 | `RESEARCH-ONLY` | Corrective negative cases and audit terminology are valuable evidence; the implementation remains tied to superseded historical participants. |
| #555 | `REUSE SELECTIVELY — UPDATE AND REVALIDATE` | Keep the operational gate, but update it after the writer epoch and CAS publication contract are fixed; its historical wording is not sufficient as-is. |

## Release decision and coordination

No historical coordinator PR is release-ready solely because its tests pass.
The release sequence is: (1) land the first-agent public snapshot, canonical
content and ArtifactCatalog/File CAS contracts; (2) define one writer epoch and
transaction owner; (3) implement a minimal adapter with crash-recovery and
mixed-writer counterexamples; (4) compare reads against B3 admission and the
#535 oracle; and only then (5) consider a production writer.

The first release may choose a controlled single-writer deployment. In that
profile, the required guarantee is a durable writer epoch, an admission receipt
and an explicit startup/quiescence procedure for all other writers. It is not a
claim that arbitrary legacy writers are automatically safe. If arbitrary
writers must remain supported, legacy-writer fencing and mixed-writer negative
tests are P0 release gates.

Current B3 admission is a post-publication validation/current-read contract;
it is not a cross-family atomic publication guarantee. A future adapter must
keep File CAS writes outside the MDBX transaction, record a recoverable CAS
publication state, and atomically publish only the MDBX reference/frontier
after the CAS object is durable. The historical rollback/fault fixtures that
are reusable are the missing-row, stale-generation, digest-mismatch,
partial-update and rollback counterexamples. Their assertions should be
ported to the new typed contracts, without copying historical participant
classes or serializers.

This matrix must be reviewed with the first agent before any historical code is
restacked or selected. In particular, the coordinator must not own File CAS
publication, and a digest or `PinnedSnapshot` enum must never be treated as
proof of bytes, transaction identity, or cross-family atomicity.

## Gates

| Gate | Status | Evidence required |
| --- | --- | --- |
| Historical ownership mapped | PASS | Matrix above covers #544, #549–#554 and #555. |
| Current-main equivalence checked | PASS | B3 admission/current-read/frontier and #579 are named explicitly. |
| File CAS boundary | DEFERRED | First-agent ArtifactCatalog/File CAS public APIs are not on this branch. |
| Durable crash recovery | DEFERRED | No production journal/replay contract exists for a coordinator adapter. |
| Legacy writer quiescence | DEFERRED | Requires deployment receipt and writer epoch, not a library-only test. |
| Production activation | BLOCKED | Do not activate the historical coordinator stack. |
