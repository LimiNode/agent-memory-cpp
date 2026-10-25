# Publication coordinator final acceptance

**Date:** 2026-10-25
**PR:** #554
**Head:** see the exact-head CI run for the current commit
**Status:** Draft; no merge performed

## Acceptance probe

The MDBX read-scope fixture now performs the final semantic probe:

1. a complete target is published;
2. its lexical contribution is erased;
3. the same identities are re-created with different normalized tokens;
4. the target is read through the pinned scope.

The result remains `TargetExact`. This is intentional and defines the boundary
of the experimental result: `TargetExact` proves receipt-selected target
identity, revision/generation coherence, projection-version identity and
lexical membership in the pinned snapshot. It does not prove that lexical
token content is equivalent to the projection body, nor that arbitrary query
token partitions are complete. A token-content binding would require a separate
persisted digest contract and is not silently inferred from matching IDs.

The fixture also constructs a populated canonical store in context B with the
same document identity and revision as the target in context A. A participant
that claims context A while holding that store is rejected at construction by
the connection-identity binding check. This makes the negative result
independent of a missing document in the foreign store; a foreign document is
present and readable through its own context, but cannot be used as evidence
inside A's pinned scope.

The frontier participant has the same boundary: its coordinator is now checked
against the declared context before the participant can be admitted. The
regression creates valid coordinator metadata in an independent environment
and verifies the expected `invalid_argument` connection-identity rejection,
rather than treating an arbitrary MDBX exception as proof.

## Implemented

- coordinator-owned writable MDBX transaction;
- durable profile, fencing, frontier, latest/per-publication receipts and
  resource bindings;
- five concrete write participants with closed admission;
- bounded `Complete` publication;
- six-family pinned target read with closed participant admission;
- `TargetExact` evidence checks for all persisted receipt/binding and target
  rows;
- close/reopen and negative conformance fixtures;
- connection-identity binding for every concrete read participant;
- explicit documentation of the target-level, identity-only lexical boundary.

## Not implemented

- global legacy-writer exclusion;
- production coordinated-profile activation/quiescence;
- arbitrary-query exact token-partition authority;
- scope-wide `Exact` completion;
- integration with segmented-v1;
- full crash-recovery coverage for every participant failure point.

## Code-quality gate

The corrective pass adds no class hierarchy or registry. It adds only point
lookups for durable binding/per-publication receipt, a six-bit read-family
admission mask, and executable counterexamples. The read path performs no
scope-wide scan. The public surface changed by renaming
`MdbxPublicationReadCompletion::Exact` to `TargetExact` and adding
`MdbxPublicationCoordinator::binding_in_transaction` plus the shared
`same_publication_evidence` comparison. Existing historical projection rows and
unrelated lexical units remain explicitly outside the target guarantee.

Known debt is the missing external writer-registration/quiescence gate and the
absence of a persisted lexical content digest. Both are intentionally recorded
as non-goals rather than approximated by flags or inferred evidence.
