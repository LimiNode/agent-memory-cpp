# P4-4 admission boundary audit — owner decision required

Date: 2026-11-26. Read-only source audit of
`feat/conversation-durable-p4-3@fb8656176fcaa4994cb29aa9a1fc497519a932a8`, tree
`ffe987701da42600079c36d54b69537d75532c6c`. No source edits, builds, commits,
dependency changes or PR operations were performed by this audit. This is an
audit/proposal, not a replacement normative specification or approved API/schema.

Owner decision addendum (2026-11-26): typed source states/read-only extraction and
a **fresh witness2 profile without dual-reader or migration** are approved in
direction. The later fresh-profile choice supersedes the earlier v1/v2-reading
request. The [compact exact contract delta](../conversation-durable-admission-v2-proposal.md)
is now the review target before implementation. Alternatives below are historical
decision analysis, not an active compatibility-reader requirement. Valuable v1
data remains an explicit activation precondition to resolve with the owner.

## Finding

The current supported coordinated admission path cannot express message Edit,
Tombstone or Restore. This is a concrete contract gate for complete P4-4 semantic
qualification and P4-5 public activation; it is not a new blocker against the
already-qualified bounded P4-2/P4-3 slices.

There is no honest implementation of those new admissions that leaves both the
public source DTO contract and the persisted original-witness contract unchanged.
Implementing private Current over already-admissible Created/Snapshot histories
can proceed, but it cannot satisfy the assignment's complete state-event parity
matrix. Hand-seeded physical rows alone would not close this gate.

All source/guide paths below are repository-root-relative. The audited worktree
was `.worktrees/conversation-durable-p4-3/`.

## Exact evidence

| Boundary | Source evidence | Consequence |
| --- | --- | --- |
| Canonical events already exist | `src/agent_memory/domain/Conversation.hpp:208` defines `ConversationEdited`; lines 209/210 define Tombstoned/Restored with exact affected/retained refs | No new domain event model or AMCE payload is needed |
| Closed source DTO | `src/agent_memory/ingestion/ConversationSourceImporter.hpp:13` permits only Created/Snapshot; line 54 defines the three-alternative source payload variant | No source-shaped Edit/Delete/Restore can be supplied to the current sink |
| Public write/read boundary | `ConversationSourceImporter.hpp:91` takes that DTO for import; line 98 returns only `const IConversationStore&` | Existing users receive no mutable append escape through the sink |
| Normalization | `src/agent_memory/ingestion/ConversationSourceImporterInternal.cpp:321`, especially lines 333–347 | Only Created, Snapshot, ToolCall and ToolResult are emitted |
| Lossless witness | `src/agent_memory/infrastructure/mdbx/ConversationDurableCodecInternal.cpp:351` writes schema 1 and tags 1/2/3; line 381 decodes exactly those cases, rejecting unknown tags at line 426 | Adding source variants changes persisted witness grammar, even if DBI count and outer row shape stay the same |
| One mapping per occurrence | `src/agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.cpp:297` rejects `mapping_count != entry_count`; lines 626–643 normalize, encode witness and increment both counters in the same operation | A raw append with no coordinated original mapping creates a profile that the next load rejects |
| Frozen correction identity | `src/agent_memory/storage/ConversationStoreInternal.cpp:41`, especially lines 43/55/57 | Correction cannot change payload family, affected target or Restore's retained target; it cannot turn a creation into an Edit/Delete/Restore |
| Public append contract | `src/agent_memory/storage/ConversationStore.hpp:123`; reference implementation `ConversationStore.cpp:265` | Append is a distinct existing operation with caller-supplied exact IDs; it is not source import or correction |
| Witness is not just AMCE | `guides/conversation-durable-physical-contract.md:314`–320 | Envelope-only reconstruction loses producer, effective request, raw classification flags and declared source target; it is explicitly insufficient for the existing source-shaped witness |
| Physical marker/version | `ConversationDurableCodecInternal.hpp:13`; `ConversationDurableCodecInternal.cpp:583` | Current reopen accepts only `conversation_durable.v1.be.keys.sha256.values.witness1` |
| Digest dependency | `ConversationDurableCodecInternal.cpp:476`, specifically line 485; wrapping/verification at lines 680/691 | The schema marker is part of every wrapped-value digest; changing only the marker is not a compatible in-place extension |

The source-import guide independently owns these constraints:
`guides/conversation-source-import.md:27` defines the const history view; lines
33–34 prohibit mutable-store escape; line 125 requires mapping, allocation and
canonical append to share an admission unit. Physical witness grammar is owned by
`guides/conversation-durable-physical-contract.md:312`–358, not this report.

## Minimal recommended proposal for owner review

Extend the existing source-import family, not a parallel Conversation API:

1. Add typed source-shaped Edit, Tombstone and Restore alternatives. The smallest
   first contract can use existing exact `EntryRef` targets: Edit carries affected
   ref plus replacement content; Tombstone carries affected ref; Restore carries
   affected ref plus exact retained-content ref. Source-key lookup targets for
   these new families can be separately specified if required; do not silently
   substitute latest revisions or infer targets.
2. Extend the existing preflight, source-shaped copy/equality, normalization and
   target admission helpers. Validate retained same-conversation/scope/chain
   targets using unchanged reference semantics, including sibling ambiguity,
   edit-after-tombstone Ambiguous and exact Restore content.
3. Version the original-witness encoding explicitly; retain all existing source
   key recipes, original replay receipts and one mapping for every new occurrence.
   Keep source key/mapping/witness, canonical revision, Entry allocator, generation
   and counters in the existing single transaction over the existing three DBIs.
4. Keep correction separate. Each source Edit/Delete/Restore is a new occurrence
   with its own original witness/ref/generation; representation correction appends
   only a revision of that occurrence and never rewrites its source mapping.
5. Audit source compatibility of extending a public `std::variant`: exhaustive
   visitors and ABI/layout may change. Do not describe it as a silent no-impact
   addition merely because existing names and alternatives remain.

No new DBI, Current projection or AMCE/AMXR wire change is intrinsically needed
for this proposal. It still requires owner review of the public DTO contract and
the persisted witness/profile version before implementation/adoption.

## Public read-only interface issue

The existing const `history()` view is sufficient to use read-only Current; a new
reader interface is not mechanically necessary for a private P4-4 baseline.
Do not manufacture an append path by casting away constness or exposing a mutable
backend behind that view.

For public activation, explicitly choose what is being advertised:

- A sink-owned const read view may retain the existing const API, with all source
  writes admitted through the extended coordinated sink and correction through
  its existing method. Clarify the implementation/advertisement of inherited write
  methods rather than claiming a generally append-capable public store.
- A genuinely mutable public `IConversationStore` must implement its advertised
  `append()` semantics through a reviewed coordinator. `ConversationEnvelope`
  alone cannot losslessly reconstruct the current source-shaped replay witness.
  A tagged canonical-admission witness mode could preserve existing append input
  types, but requires a new persisted witness mode and an explicit cross-mode
  replay/conflict policy. It is more than a trivial forwarding override.
- A narrower read interface is an alternative API correction, not an already
  approved requirement. Extracting/changing interfaces and return types has
  compatibility consequences that need review.

The existing `ConversationWriteStatus::Unsupported` can report an honestly
unsupported write on a clearly delimited view. It neither supplies missing
message-state admission nor satisfies a claim that the full append-capable store
profile is implemented. Never fabricate NotFound/Empty/Complete for unsupported
reads.

## Witness/profile versioning and migration choices

Owner review should select one explicit approach before persisted adoption:

1. **Fresh opt-in profile/version:** safest bounded first implementation. New
   witness grammar has an explicit marker/version and separately qualified fresh
   environments. Preserve old experimental fixtures and explicit rejection of
   incompatible profiles. No automatic conversion of existing databases.
2. **Compatibility reader/profile:** support old and new witnessed admissions
   under an explicitly negotiated descriptor/version. Preserve old key/witness
   bytes and digest interpretation. Define reader/writer admission compatibility,
   mixed-history behavior and old-reader refusal. Adding tags to schema 1 is a
   changed contract and is not implicitly authorized by reserved numeric values.
3. **Reviewed migration:** if an existing populated profile must move to a new
   marker, separately design backup/rollback, OLD/NEW fixtures, value rewrapping,
   fresh-process replay, peak DBI/disk capacity and failure recovery. Current
   assignment does not authorize executing migration.

Changing the marker constant without preserving/versioning digest interpretation
would invalidate old row wrappers. Merely retaining the three DBI names or outer
`SourceRow {key,witness,ref,generation}` layout does not avoid this compatibility
decision. Do not weaken `mapping_count == entry_count` to conceal a raw append.

## Required checks after a decision

- Admit every state-event test through the chosen supported coordinator, not only
  seeded rows; compare unchanged oracle refs/content/status at every prefix.
- Preserve original replay/conflict before and after correction/reopen and under
  competing processes, including changed target/recipe/evidence witnesses.
- Verify missing/foreign/denied targets, exact Restore content, frozen resolution,
  consecutive Entry IDs, no-op/refusal counters and atomic rollback.
- Add witness version/unknown-tag/old-profile tests, independent goldens, digest
  compatibility checks and supported installed/relocated C++17 consumers.
- Continue bounded Current's complete-input-before-output rule and separate
  physical/decode/retained-state/output budgets. Source DTO extension does not
  itself prove Current correctness or performance.

Conclusion: **HOLD for exact DTO/witness2/profile/read-only contract approval and
full message-state qualification.** Direction is selected in the addendum above.
Existing #702/#703 integration and the
separate diagnostic-allocation dependency fix are unaffected by this audit.

## Ordered continuation and Phase 5 handoff

This is not a completed P4-4 implementation or benchmark receipt. The
[predeclared Current screen](../experiments/2026-11-24-conversation-durable-current-plan.md)
remains unchanged and **NOT RUN**. The source findings above were inspected,
not reproduced by a new build or durable-scale measurement.

After the owner approves the linked exact admission/profile contract:

1. Review the minimum DTO, lossless witness/version, old/new reader/writer and
   append-versus-read-view contract delta; preserve existing evidence and prohibit
   automatic migration. Review actual grammar before implementation/adoption.
2. Implement coordinated state-event admission and a bounded full-fold baseline
   in the P4-4 Draft. Require unchanged-oracle parity on every authored prefix,
   randomized histories, exact Restore references, corrections and pinned pages.
3. Execute the frozen 2048-root/page-32 screen, then its declared larger,
   branching, restore/correction-heavy regimes. Explicitly report refused reads
   and unexecuted regimes. Only measured need can justify an optional projection
   proposal; any new DBI remains a separate owner decision.
4. Only after P4-4 semantic qualification, open a separate P4-5 Draft. Execute
   65,537 real committed admissions, fresh-process reopen, bounded first/middle/
   last pages and quota replay/conflict; measure row-count and logical-byte
   capacity separately. Close direct MAX-revision/global revision-count tests,
   correction uncertain-outcome recovery, quota-policy exposure and installed/
   relocated real durable consumers. None of these checks has run in this audit.
5. Phase 5 full Conversation Core qualification then exercises the accepted
   public profile end to end: retained evidence/authority boundaries, recovery,
   unavailable targets, exact/current/history/correction/replay compatibility,
   resource/refusal behavior, installed consumers and independent review. Explicitly
   delimit retention/erase and untested device/power-loss guarantees rather than
   inferring them from the Phase 4 process-crash suite.

P4-4/P4-5 stay separate Drafts; neither is authorized for automatic merge.
Phase 6 derived bridge, Managed Memory Blocks, generic ontology SPI and numeric
product integration remain independent. The mdbx-containers corrective PR must
be accepted separately before a downstream dependency-update PR repeats the
durable allocation matrices; lost acknowledgments still require retry recovery.
