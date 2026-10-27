# Publication stack integration audit (2026-10-27)

**Status:** `READ_ONLY_AUDIT` — no rebase, merge, migration, or writer
activation was performed.

## Snapshot

The audit uses current `main` at
`be3c80c1c635b92b21cee6a37963526d32ea88fd`.

| PR | Base | Head | Draft | Integration state |
| --- | --- | --- | --- | --- |
| #544 | `main` | `b7fff7002efd412d3e2a2630f160a86ac6303fbc` | yes | protocol/plan foundation |
| #554 | `docs/publication-pinned-read-conformance` | `0749147a1256338f6a6471c635b9d80dd5e73820` | yes | corrective participant/read validation |
| #555 | `main` | `00adeb640557a143ac2279d0e4f9ccd4eeead060` | yes | legacy writer activation gate |
| #559 | `main` | integrated in `be3c80c1` | n/a | segmented lexical adapter already on main |

The #544 → #554 ancestry is not currently based directly on current `main`:
the #554 head is 23 commits ahead and 1 commit behind current `main` when
compared by GitHub. Its merge base is the historical
`1e2806d21bc36fa6baeca536aaccec0bf535502c`, while #554 is clean relative to
the #544 head. This is a conflict/restack risk, not evidence that the changes
are safe to merge as-is.

## Contract audit

### Flat-v2 lexical participant

The coordinator stack currently targets the flat persistent lexical participant
and its `PublicationPlan` evidence. Any activation must prove that the
participant writes the same resource generation, unit revision, projection
revision, and payload digest that the coordinator validates. A green protocol
receipt alone is insufficient; the participant's transaction and the
authoritative stores must be inspected together.

### Segmented-v2 adapter

The segmented adapter from #559 is now on `main`, but it is not automatically a
drop-in replacement for the flat-v2 participant. A future participant must
declare the segment generation/fingerprint and bind it to the same publication
frontier and unit revision. It must also preserve fail-closed behavior for a
missing segment, stale generation, partial update, or rollback.

### Legacy writer (#555)

#555 remains an activation-gate document/experiment. Its existence does not
quiesce older writers. Before any production activation, the caller must prove
that legacy writers are stopped or fenced, and that a mixed-writer run cannot
advance the publication frontier without a matching receipt and authoritative
rows. This is an external operational gate, not something to infer from the
presence of coordinator classes.

## Recommended order

1. Keep #544 → #554 Draft and restack the stack onto current `main` only in a
   dedicated corrective pass after exact-head CI has been rerun.
2. Re-run the full coordinator and segmented lexical suites together; resolve
   conflicts by preserving the participant/result binding and rollback
   counterexamples from #554.
3. Keep #555 separate and require an explicit legacy-writer quiescence/fencing
   receipt before any activation decision.
4. Only after those gates pass, evaluate a durable MDBX adapter against the
   #535 oracle and #539 logical baseline. No production learned-sparse writer
   is implied by this audit.

## Decision

`HOLD_FOR_RESTACK_AND_EXACT_HEAD_CI`. The stack has a clear conceptual scope,
but its historical base and the newly integrated segmented adapter make an
automatic merge unsafe. This note records the evidence and boundaries; it does
not authorize a merge or a storage/backend activation.
