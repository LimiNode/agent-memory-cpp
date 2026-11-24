# Conversation source import: bounded Phase 3B qualification

- Date: 2026-11-24. Status: corrective implementation passes local Linux and
  Windows qualification; **HOLD pending exact-head hosted checks and independent
  review**. Historical abort evidence below is retained, not current verdict.
- Branch: `feat/conversation-source-import`, dependent Draft PR over Phase 3A
  `feat/conversation-reference-store@cd6d5dffc8c2c3684330c2fcfea46aa5042cdcee`.
- Normative owner: [source import contract](../conversation-source-import.md).
  This report records evidence, not another event grammar or admission policy.

## Baseline and Phase 3A gate

This section through the original hosted finding records the historical
`22ec139a` / `9ed28efe` qualification. The corrective appendix below supersedes
its unresolved copy-path inventory and records the new dependencies/results.

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

## Corrective qualification: shared external-identity copies

### Dependencies and implementation boundary

Foundation Draft [#696](https://github.com/LimiNode/agent-memory-cpp/pull/696)
introduces additive compiled named copies at `ccc4a3e6`, with scalar-key tests
at `3d7b868d`. The [foundation audit](2026-11-24-external-identity-copy.md)
owns the complete copy-path inventory, evidence, alternatives and compatibility
boundary. No aggregate special member, alternative, layout or AMXR/AMCE wire
grammar changes. Ordinary caller copies remain ordinary STL operations.
No proven fixed alternative STL was available; Clang/libc++ were unavailable
locally. The OpenTelemetry report is corroboration, not an asserted GCC bug ID.

The expanded Phase 3A fixture reproduced two additional failures against the
historical store: `correct` envelope copy and Current origin metadata assignment.
The minimal correction is shared envelope cloning plus moving metadata from
the private decoded root. See the [Phase 3A appendix](2026-11-23-conversation-reference-store-qualification.md#2026-11-24-external-key-exception-safety-correction).
No fold/cache/projection semantics changed.

The importer now routes validation, normalization, source witnesses and
exact/source/unresolved target staging through shared copies. The optional
fallback selects its source by const reference, then clones explicitly; no
conditional-expression copy remains. Message/call DTO fields contain no
external identity variants. All staged values move into mappings before append.
Source keys, equality, budgets, lock, ID allocation and commit/rollback remain
unchanged. After Applied there are only scalar counter updates and the
statically checked nonthrowing result move. No callbacks run under the lock.

Foundation and Phase 3A corrections are distributed here by ordinary
cherry-picks (`723bd4b1`, `5ce7fed7`, `48e8b576`, `2236727f`), with originating
commits retained in commit messages. These are duplicate logical patches in
an unmerged stack, not independent competing implementations. #690/#693 were
not changed. The publication receipt binds final heads and exact hosted runs.

### Executed local qualification

All builds use C++17, static library, tests/examples ON, JSON/MDBX/research OFF.
Linux: WSL2 Ubuntu, kernel 6.18.33.2-microsoft-standard-WSL2, g++ 15.2.0
(package 15.2.0-16ubuntu1), headers `/usr/include/c++/15`, runtime package
`libstdc++6` **16-20260322-1ubuntu1**. Runtime and header versions are not
conflated. Linux Debug applies `-fsanitize=address,undefined
-fno-omit-frame-pointer` to library and executables with no suppression.
Linux Release uses the CMake Release profile. Windows GNU is MinGW-w64
15.2.0 release-posix-seh-msvcrt-rt_v13-rev0; MSVC is 19.44.35228.0,
VS2022 x64 Release. Compiler/runtime-specific interception counts are not
assumed equal.

| Full build + CTest | Result |
| --- | --- |
| Linux Debug ASan/UBSan, `/var/tmp/am-copy-phase3b-debug` | 64/64 passed; no sanitizer report |
| Linux Release, `/var/tmp/am-copy-phase3b-release` | 64/64 passed |
| Windows GNU Release, `.build/conversation-phase3b-gnu` | 64/64 passed |
| Windows MSVC Release, `.build/conversation-phase3b-msvc` | 64/64 passed |
| Public header self-sufficiency, AMXR/AMCE goldens, semantic/concurrency tests | Included in all four full suites |

| Actual injected failures | Linux Debug sanitized | Linux Release | Windows GNU Release | Windows MSVC Release |
| --- | ---: | ---: | ---: | ---: |
| Foundation twelve copy cases | 130 | 130 | 26 | 130 |
| Original store six local cases | 184 | 184 | 39 | 187 |
| Store external keys, seven cases including NoChange | 336 | 336 | 74 | 339 |
| Importer twelve cases | 718 | 718 | 126 | 722 |

Importer operation order and Linux counts:
create/local import/replay/out-of-order result/scalar ExternalEvent/composite
ExternalEvent/composite ObjectRevision/composite replay/conflicting witness/
exact result/unresolved complex result/resolved complex source-target result =
6/44/13/58/54/93/75/30/29/49/108/159. MSVC differs only in create (10).
Windows GNU = 2/6/0/6/7/19/13/5/5/7/23/33. Its replay lane really intercepts
zero calls; allocating Debug/Linux/MSVC replay lanes remain exercised.

Every measured ordinal throws once and disarms before unwinding. The fixture
checks original source/target witnesses, canonical bytes, Current, old replay
mapping and generation; retry and exact two-row mapping capacity detect consumed
IDs, rows or duplicate append. The external store fixture additionally checks
receipts, historical corrected revisions, exact remaining storage capacity and
pinned cursors on correction/read/NoChange failures. Scalar signed/unsigned/string
tags and both composite parent/object keys are tested in the shared foundation.
No previous failure fixture or diagnostic was disabled. The independent standard
variant reproducer remains diagnostic, not an expected-pass CTest.

This is qualification of the measured replaceable C++ allocation calls and
fixtures, not malloc/OS/host-allocator, asynchronous failures, arbitrary caller
copies or whole-process OOM safety. MSVC checked STL remains explicit SKIP 77,
not qualified failure recovery. Importer correction uses the independently
swept Phase 3A path; not every importer refusal branch is separately swept.

Installed C++17 `find_package` consumers for identity, conversation, codec,
store and import plus library-info compile and execute on Linux GNU Release
and Windows MSVC Release. All six consumers also compile/run from a moved
prefix on both platforms, with the original prefix absent. Final exact-head
hosted results are recorded in the PR receipt after execution. Local build/CTest raw logs remain
under the named build directories; this committed table is the durable summary.
No local JSON/MDBX or Clang claim is implied; hosted lanes qualify their own
profiles separately.

The [copy-cost experiment](../experiments/2026-11-24-external-identity-copy-cost.md)
records normal-path allocation counts, directional latency, peak process RSS
and explicit measurement limitations; no speedup or universal OOM claim is made.

### Integration and remaining acceptance (historical pre-review status)

The concrete Linux abort is not reproduced after correction in either full
Linux profile. This closes the local technical regression, **not** the final
acceptance gate: exact-head CI/Packaging and independent reviewer confirmation
must be observed and attached to the publication receipt. Self-review is not
independent review. Until those gates are satisfied, the overall verdict is HOLD.

Owner-only future integration: #690, #693 documentation reconciliation, #696
foundation, #694 on the resulting main, then retarget/reconcile #695 and rerun
exact-tree checks. Ordinary cherry-pick distribution does not authorize rebase,
force-push, branch deletion or duplicate integration. No merge/release/migration
was performed. Phase 4 remains deferred until acceptance; no MDBX code is added.

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

## Final Phase 3B missing-source closure

The independent read-only agent review of `de63e95a`, `a0c2d6bb` and
`3d7b868d` found no blocking issue and independently reran the existing
fault executables in the four qualified profiles. Exact-head CI/Packaging
passed (CI 45304889542 / Packaging 45304889497 for the importer). This
supersedes the historical independent-review HOLD above; it does not authorize
merge into main or establish whole-process OOM safety.

The thirteenth allocation fixture, `MissingCompositeSourceResult`, now enters
the absent source-map fallback through `ConversationSourceTarget`. Its missing
call has composite parent and object keys with signed, unsigned and long-string
components. Every intercepted allocation ordinal throws once. Assertions check
unchanged inputs/history/Current/old mapping/generation, consecutive retry ID,
exact unresolved external identity and evidence, later call admission, frozen
byte-identical replay, and resolution of a separate new result. Only this
fixture uses four mapping rows; all original exact two-row fixtures remain.
The later call/new result consume IDs three/four and the remaining rows,
detecting a partial missing-call mapping or ID consumption. No production code,
wire format, semantics or failure gate changed.

| Targeted source semantic + failure tests | Result | New-case failures | Importer total |
| --- | --- | ---: | ---: |
| Windows GNU 15.2 Release | 2/2 passed | 17 | 143 |
| Windows MSVC 19.44 Release | 2/2 passed | 98 | 820 |
| Linux GNU Debug ASan/UBSan failure executable | Passed, no sanitizer report | 98 | 816 |

New exact-head hosted receipts are published after execution in #695. Combined
integration qualification is a separate temporary rehearsal artifact, not
implied by these individual-branch checks. Phase 4 remains plan-only.
