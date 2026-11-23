# Conversation source import: bounded Phase 3B qualification

- Date: 2026-11-24. Status: **HOLD** after hosted Linux allocation-failure abort;
  local Windows qualification does not close this finding.
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
| Initial hosted CI 45287868236, implementation HEAD 22ec139a | Failed: Linux core and Linux MDBX wiring abort in import allocation-failure test; semantic import tests pass |
| Initial hosted Packaging 45287868013, same HEAD | Passed 6/6 |

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
copies/charges and all post-append operations. The hosted Linux finding below
blocks a READY verdict despite the successful Windows checks. Full Core and
upstream provider conformance remain open. The publication receipt names the
exact final SHA and hosted runs; diagnostic/report commits do not change the
implementation's failed qualification status.

## Confirmed hosted finding: throwing standard variant copy

On implementation HEAD `22ec139a39a59144dae28b2c99ebb0ade91eacc3`, hosted
CI [45287868236](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45287868236)
finished with failure. Linux core job 138088880169 passed 66/67 tests; Linux
MDBX wiring job 138088880324 passed 104/105. Both failures are the new import
allocation-failure test with `double free or corruption (out)`. Windows and
macOS core succeeded. Packaging
[45287868013](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45287868013)
passed 6/6; packaging success is not exception-safety qualification.

A narrow Linux reproducer compiled the import failure test, importer, store and
required domain implementation files using Ubuntu GNU/libstdc++ 15.2.0,
`-std=c++17 -O0 -g -fsanitize=address,undefined -fno-omit-frame-pointer`.
The first four matrices passed (6/44/13/58 injected failures). ExternalEvent
ordinal 5/54 reached UBSan `variant:1916`, "execution reached an unreachable
program point". Its stack is:

```text
std::variant<...string, vector<ExternalIdentityComponent>>::copy constructor
  -> ExternalObjectKey / ExternalObjectRef copy constructor
  -> optional<ExternalObjectRef> / ConversationSourceBinding copy constructor
  -> source_status: probe.source = source
  -> import_observation
```

This happens before tentative source-map insertion or canonical append.
It is not evidence of a double erase in importer rollback.

The [standalone diagnostic](../../tests/ingestion/fixtures/variant_copy_bad_alloc_probe.cpp)
uses only standard headers and standard replaceable allocation functions; no
project code, map or store is linked. It compares a throwing copy constructor
of `std::variant<int64_t, uint64_t, string, vector<int>>` with direct construction of its string
alternative. Both should propagate `bad_alloc` and preserve the source.
With the same Linux standard library, the copy reproduces UBSan `variant:1916`
at O0 and an invalid free at O3 without sanitizers; the direct-alternative
control catches `bad_alloc` and validates the unchanged source. The installed
header's never-valueless destruction path visits an incompletely constructed
variant after its string copy throws. This establishes a standard-library
failure on this setup, not a generic compiler-miscompilation claim.

Reproduce from the source root on Linux (the diagnostic is deliberately not
registered as a passing CTest or used to skip the real import failure test):

```sh
g++ -std=c++17 -O0 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
    tests/ingestion/fixtures/variant_copy_bad_alloc_probe.cpp -o /tmp/am-variant-copy-probe
UBSAN_OPTIONS=print_stacktrace=1 /tmp/am-variant-copy-probe control
UBSAN_OPTIONS=print_stacktrace=1 /tmp/am-variant-copy-probe
g++ -std=c++17 -O3 tests/ingestion/fixtures/variant_copy_bad_alloc_probe.cpp \
    -o /tmp/am-variant-copy-release
/tmp/am-variant-copy-release control
/tmp/am-variant-copy-release
```

The optional `AGENT_MEMORY_TRACE_ALLOCATIONS=1` environment variable on the
import failure executable prints operation/ordinal before each injection,
outside the allocation gate. No injection, assertion or fixture is disabled.

**Open finding / decision boundary:** failure-safe external-identity copying
is not qualified on the affected Linux standard library. A workaround must
cover composite-key scalar variants and nested/optional references, not just
the first failing assignment. Importer-only cloning also leaves reference-store
copy paths in `correct` and Current origin metadata; these have not been swept
with external-key OOM fixtures. Do not silently change the integrated AMXR
value-type interface, pin away the failure, disable the test or declare Linux
atomicity from the successful Windows matrices. Next work must choose and
review either complete portable copy hardening (including the Phase 3A paths)
or a demonstrably fixed toolchain, then rerun external/composite-key fault
fixtures and exact-head CI/Packaging. No such correction is included here.

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
