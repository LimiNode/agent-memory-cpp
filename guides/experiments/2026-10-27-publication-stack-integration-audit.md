# Publication stack integration audit (2026-10-27)

**Status:** `READ_ONLY_AUDIT` — no rebase, merge, migration, or writer
activation was performed.

## Snapshot (as of 2026-10-27T18:01:00Z)

The audit uses current `main` at
`be3c80c1c635b92b21cee6a37963526d32ea88fd`.

| PR | Base | Head | Draft | Integration state |
| --- | --- | --- | --- | --- |
| #544 | `main` | `b7fff7002efd412d3e2a2630f160a86ac6303fbc` | yes | protocol/plan foundation |
| #554 | `docs/publication-pinned-read-conformance` (`18548d1f`) | `e3766ab1896b9642c92ed33553b6dff8749864bf` | yes | corrective participant/read validation |
| #555 | `main` | `00adeb640557a143ac2279d0e4f9ccd4eeead060` | yes | legacy writer activation gate |
| #559 | `main` | `995435afbea1235f30ab9d808bdadb7959c26591` (merged) | n/a | segmented lexical adapter already on main |

The #544 -> #554 ancestry is not currently based directly on current `main`:
the #554 head is 25 commits ahead and 1 commit behind current `main` when
compared by GitHub. Its merge base is the historical
`1e2806d21bc36fa6baeca536aaccec0bf535502c`, while #554 is clean relative to
the #544 head. These counts and SHAs are immutable facts for this audit
snapshot, not a dynamically maintained PR dashboard. They describe a
conflict/restack risk, not evidence that the changes are safe to merge as-is.

## Contract audit

### Flat-v2 lexical participant

The coordinator stack currently targets the flat persistent lexical participant
and its `PublicationPlan` evidence. Any activation must prove that the
participant writes the same resource generation, unit revision, projection
revision, and payload digest that the coordinator validates. A green protocol
receipt alone is insufficient; the participant's transaction and the
authoritative stores must be inspected together.

`TargetExact` is target-bounded as an evidence claim, but it is not a bounded
read-cost promise. The current flat-v2 `read_in_transaction()` reconstructs the
complete persisted lexical index before selecting the receipt target, and the
write-side validator uses the same materialization path. The expected cost is
therefore O(N) in the complete flat-v2 index for both validation directions.
This must be measured before production activation; the coordinator does not
turn full-index materialization into target-local work.

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

1. Keep #544 -> #554 Draft and restack the stack onto current `main` only in a
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
but its historical base, flat-v2 O(N) validation cost, and the newly integrated
segmented adapter make an automatic merge unsafe. This note records the
evidence and boundaries; it does not authorize a merge or a storage/backend
activation.

## Reproducible preflight receipt (agent-reported local evidence)

The following evidence was produced in a disposable detached worktree. It is
replayable from the immutable SHAs below, but it was not independently run by
GitHub CI and is therefore not an integration approval.

```text
base: be3c80c1c635b92b21cee6a37963526d32ea88fd
historical_merge_base: 1e2806d21bc36fa6baeca536aaccec0bf535502c
target: e3766ab1896b9642c92ed33553b6dff8749864bf
strategy: git rev-list --reverse historical_merge_base..target | git cherry-pick
conflict_policy: stop, record unmerged paths, cherry-pick --abort
configure: cmake -S . -B tmp/build-restack-preflight -G Ninja -DCMAKE_BUILD_TYPE=Release -DAGENT_MEMORY_ENABLE_MDBX=ON -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED -DAGENT_MEMORY_BUILD_TESTS=ON -DAGENT_MEMORY_BUILD_EXAMPLES=OFF
coordinator_tests: ctest --test-dir tmp/build-restack-preflight --output-on-failure -R 'agent_memory_mdbx_publication_(coordinator|read_scope|write_scope)_test' => 3/3 PASS
segmented_tests: ctest --test-dir tmp/build-restack-preflight --output-on-failure -R 'agent_memory_mdbx_(segmented_lexical_index_conformance|incremental_segmented_lexical_index)_test' => 2/2 PASS
```

Ordered source commits replayed (25):

`aeb4cc19 b7fff700 be4c11f5 00ef298c 45c870c4 38c7473e e64630ab 05a070c5 18548d1f 4323d693 88f88b18 3d51b4a2 a7a43fa8 84cba2ab bcf66f21 5ce3fb05 a235f714 64037159 7fc8f7ab c919b64a e9ea4707 dd78c722 0749147a 1e2b4e05 e3766ab1`.

All 25 cherry-picks applied without conflicts in the temporary replay. The
worktree and temporary commits were removed afterward. This receipt does not
replace exact-head CI after any authorized restack; each affected PR and the
final combined head still require new CI.
