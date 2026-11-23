# Conversation source import: bounded Phase 3B qualification

- Date: 2026-11-24. Status: locally qualified reference slice; hosted checks pending.
- Branch: `feat/conversation-source-import`, dependent Draft PR over Phase 3A
  `feat/conversation-reference-store@cd6d5dffc8c2c3684330c2fcfea46aa5042cdcee`.
- Normative owner: [source import contract](../conversation-source-import.md).
  This report records evidence, not another event grammar or admission policy.

## Baseline and Phase 3A gate

Phase 3A final qualification added deterministic allocation-failure and exact
historical-revision tests plus the [Current pagination measurement](../experiments/2026-11-24-conversation-current-pagination.md).
GNU Debug/MSVC Release targeted tests passed 3/3 each. GNU exercised 199 actual
one-shot bad_alloc failures (create 3, append 12, correction 47, exact+receipt 19,
history+continuation+receipt 23, Current+continuation 95). No production store
or codec changes were needed. Current repeats a full fold per page; its bounded
reference output is not a scalable long-dialogue serving claim.

Exact-head Phase 3A hosted CI 45284456938 completed SUCCESS (32 success,
2 intentional skips); Packaging 45284456986 passed 6/6, including macOS.
Verdict: READY for owner review within the reference subset, not integration
authorization. The tested base is unmerged; codec #690 and independent docs
#693 remain unchanged. Future owner integration must reconcile overlapping
architecture/coverage docs and requalify the resulting tree.

## Implemented vertical path

Source-shaped DTO -> bounded reference normalization -> atomic key/ID/append
seam -> canonical AMCE -> exact/history/current reads. The public abstract sink
lets a future backend implement the same atomic admission contract without
MDBX types in domain/ingestion headers. The reference sink owns its store;
mutable append cannot bypass its coordinator. No provider SDK, new event wire,
JSON dependency, DBI, runtime execution, graph or derived bridge was added.

The [C++17 example](../../examples/conversation_import.cpp) is an installed and
relocated-package consumer, not merely a header compile. Its input is an
evidence-qualified local snapshot; it verifies replay, immutable exact read,
explicit CAS normalization correction and current content without inventing
source change history.

## Source-shaped test matrix

All source fixtures are authored inputs to the implemented adapter, not captured
upstream SDK execution or provider conformance. Artifact bytes/authenticity and
provider event/marker reliability are host preconditions, not proved by IDs.

| Case | Exercised assertion |
| --- | --- |
| Denial + synthetic exit 1 | Unknown, not fabricated execution failure |
| Recovery + is_error true | Unknown; origin participates in replay witness |
| Timeout | Unknown, no claim that effects did or did not occur |
| Original A / effective B | AMCE call request stays A; B remains separate source evidence/witness |
| Reused binary call token across runs | Full source domains keep occurrences separate |
| Multiple results/attempts | Distinct source keys allocate separate results, not token dedupe |
| Missing call/body and partial archive | Explicit unresolved variant and absent body |
| Source-reported success | Succeeded report retained; no effect-verification/execution operation |
| ExternalEvent/ObjectRevision replay | Original exact ref/generation, no second Entry |
| Snapshot-only observation | Same retained revision/artifact/locator replays; another locator remains separate |
| Same external message, different events | Separate local occurrences |
| Missing reliable provider marker | Invalid ObjectRevision; explicitly local snapshot permitted, local Created rejected |
| Conflicting recipe/content/actor/producer/effective args/classification/target | Conflict; identical normalized Unknown does not erase differing source assertions |
| Out-of-order result before call | Identical later replay remains byte-identical unresolved; new result may resolve original call@1 |
| Denied/cross-conversation/wrong exact/token target | Refusal without ID/mapping/generation consumption; valid retry succeeds |
| Producer/observer/actor | Distinct attribution, source evidence retained, no actor substitution |
| Explicit correction | CAS, immutable original, original replay does not revert correction |
| Bounds | Mapping row capacity, exact input/aggregate mapping byte boundaries, rollback at capacity, zero budgets, invalid options and invalid evidence/UTF-8 |
| Concurrent duplicate inputs | One Applied, seven NoChange |
| Concurrent distinct inputs | Sixteen additional consecutive IDs/generations, no gaps/duplicates |
| Independent instances | No cross-instance dedupe or allocator guarantee |

## Executed checks

Windows x64 local profile: C++17, static library, JSON/MDBX/research OFF,
tests/examples ON. No required dependency was added.

| Check | Observed result |
| --- | --- |
| GNU 15.2 Ninja Release full build + CTest | 62/62 passed |
| MSVC 19.44 VS 2022 x64 Release full build + CTest | 62/62 passed |
| Public-header self-sufficiency | Passed in both full suites, including new leaf/umbrella |
| Source-shaped semantic/concurrency test repeated ten times (GNU) | 10/10 passed |
| Strict C++17 installed find_package consumer, MSVC Release | Built and executed successfully |
| Copied/relocated-prefix C++17 import consumer, MSVC Release | Built and executed successfully |
| New contract/report relative Markdown links and diff whitespace | 8 links valid; git diff --check passed |
| Exact-head hosted CI/Packaging | Pending publication/observation; not counted as passed |

`conversation_import_failure_test` uses isolated standard replaceable
ordinary/aligned/nothrow C++ allocation functions in a single-threaded executable.
Arguments, fixtures and assertions allocate outside the operation gate. It
measures successful calls, fails every observed ordinal exactly once, disarms
before unwinding, checks unchanged canonical bytes/current/generation/old replay
mapping and then retries. Exact two-row capacity and consecutive ID two after
retry detect consumed mapping rows or IDs. The semantic test separately checks
exact aggregate mapping/input byte boundaries and refusal/retry at capacity.

| Fixture | GNU Debug narrow probe | MSVC Release | GNU Release |
| --- | ---: | ---: | ---: |
| Create with existing conversation/mapping | 6 | 10 | 2 |
| LocalObservation import | 44 | 44 | 6 |
| Existing replay / returned ref | 13 | 13 | 0 |
| Out-of-order source-target result | 58 | 58 | 6 |
| ExternalEvent / nested AMXR key | 54 | 54 | 7 |
| Actual one-shot bad_alloc failures | **175** | **179** | **21** |

The narrow Debug probe compiles the new importer and failure test with GNU
`-std=c++17 -Wall -Wextra -Wpedantic -O0 -g`, linking the existing same-source
Phase 3A Debug static archive. The store/domain implementation is unchanged
from the qualified base. This is not reported as a full Phase 3B Debug build.
Commands/outputs are retained under `.build/conversation-phase3b-debug-probe`;
full-suite logs are `.artifacts/phase3b-{gnu,msvc}-ctest.log` (untracked local
artifacts, not durable published evidence archives).

Counts depend on optimizer/runtime visibility. Initial GNU Release testing
exposed a test assumption that replay must allocate; its successful path has
zero intercepted calls. The corrected harness reports zero without inventing
an allocation or claiming replay fault coverage in that configuration. Debug
and MSVC Release exercise the allocating replay path. MSVC checked STL is an
explicit SKIP 77 because proxy allocation inside noexcept construction may
terminate. No malloc/OS/host-allocator, asynchronous, arbitrary-input or crash
guarantee follows. Fully resolved source-target, correction and every refusal
branch are not independently swept by this import allocation matrix; Phase 3A
separately qualifies its correction fixture.

The implementation review checked ownership, fail-closed lookup before replay,
full input witness versus normalized bytes, frozen target resolution, bounded
copies/charges and all post-append operations. No confirmed blocking production
defect was found in this subset. READY for review is conditional on observing
exact-head hosted checks; full Core and upstream provider conformance remain
open. The publication receipt names the exact final SHA and hosted runs.

## Open boundaries for Phase 4/5

- Instance-local exceptions/concurrency are not process-crash durability,
  persistent import-key mapping, cross-process allocator, retention/erase,
  reopen or transaction recovery qualification. Phase 4 needs one durable
  transaction over ID, source map, canonical history and indexes with a reviewed
  physical manifest/DBI budget, rollback/no-reuse and fresh-process tests.
- Reference Current repeats full folds; Phase 4/5 must qualify projection,
  checkpoint or index alternatives against the exact oracle/frontier contract.
- Source record retention/authenticity/hydration and production authorization
  remain host/artifact owners. New observer receipts and explicit target
  reconciliation are deferred, not silently implemented by replay.
- Phase 5 must qualify the complete chat/session path including imported
  edits/delete/restore/navigation, durable recovery, policy revocation,
  retention/erase, work/memory/latency and partial-archive disposition. Current
  source families do not implement every provider event kind.
- Real SDK parsing/classification, upstream reliability, execution attempts,
  approval/effects verification and runtime retry policy need separate adapter
  conformance. Phase 6 exact-source publication remains independent.

No merge, rebase, force-push, release or migration is authorized by this report.
