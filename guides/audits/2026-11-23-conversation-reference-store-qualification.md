# Conversation Core Phase 3A qualification

- Date: 2026-11-23.
- Status: local reference subset qualified for independent Draft PR review;
  complete Conversation Core remains Planned.
- Dependency: PR #690, exact reviewed HEAD
  `7bb88efc078f3b7c3c9a3fb49235d472bbaef717`; not merged by this work.
- Contract owner: [reference store](../conversation-reference-store.md).
- Evidence boundary: source/tests in this change, Windows local GNU/MSVC;
  hosted checks and exact resulting commit are linked in the Draft PR.

## Implemented and checked

`IConversationStore` separates canonical-history operations from its compiled,
mutex-serialized in-memory reference backend. The backend retains canonical
AMCE bytes. No Phase 2 codec, nested AMXR format or golden fixture changed.

| Boundary | Deterministic evidence |
| --- | --- |
| Immutable identities/history | Exact original-byte recovery, append replay/collision, unchanged correction, source-occurrence rebinding refusal, old/new recipe and reason receipts |
| CAS/atomic status refusals | Stale/future expected revision, entry/revision/storage/conversation caps, failed target admission consumes no ID/generation, concurrent append and concurrent correction |
| Deterministic fold | Create/edit/tombstone/restore, exact historical content rather than latest substitution, correction after descendants, sibling ambiguity, edit after tombstone remains ambiguous |
| Snapshot evidence | Independent snapshot roots sharing the same AMXR object do not fabricate creation/edit history or deduplicate separate observations |
| Tool observations | Binary repeated call tokens stay independent; exact typed call and token validation; partial bodyless results, unresolved calls, tools excluded from message folding |
| Lossless metadata | Actor/role/observer, source evidence, unresolved reply and binary advisory extensions retained; opaque/critical data refused from semantic admission |
| Bounded reads | Exact/history/current input/output limits, zero budgets, partial prefixes, oversized-first retry without skipping, exhausted fold exposes no unqualified current content |
| Snapshot isolation | History/current pages pin commit generation across append/correction; wrong scope/policy/view/conversation/instance/default token rejected; destroyed-instance token rejected |
| Backend-neutral cursor | Independent backend can mint private derived state through protected interface helpers; reference backend rejects that foreign state |
| Public package | All public headers compiled first-in-translation-unit; strict C++17 installed consumer exercises history, correction and bounded paging |

## Local commands and observations

Both builds used tests/examples ON, JSON OFF, MDBX OFF and research tests OFF.
No dependency download, production storage activation or database mutation was
needed.

| Configuration/check | Observed result |
| --- | --- |
| GNU 15.2, Ninja, Debug, C++17; full build + CTest | 58/58 passed |
| MSVC 19.44, Visual Studio 17 2022 x64, Release; full build + CTest | 58/58 passed |
| GNU final store test after independent-backend/stale-token fixtures | 10 repeated executions passed |
| MSVC installed `find_package(agent_memory)` consumer, Release, strict C++17 | Built and ran successfully |
| `git diff --check` | Passed |
| MSVC additional Debug build | NOT RUN to completion: MSBuild stopped with insufficient disk space before compiling/testing this configuration |

Build roots are `.build/conversation-phase3a-gnu`,
`.build/conversation-phase3a-msvc`, `.build/conversation-phase3a-prefix` and
`.build/conversation-phase3a-consumer` in the shared repository workspace.
The consumer is `agent_memory_conversation_store_consumer` from
`packaging/consumers/installed`; its source is the checked
[`conversation_store` example](../../examples/conversation_store.cpp).
After verification, 55 generated GNU test executables (139 MiB) were removed to
recover disk space; source, library, store-test executable and logs were retained.
They are rebuildable, not deleted evidence/source records.

Independent read-only source review found metadata-link retarget and an
allocating result copy after write commit. Both were corrected before
publication. Result preparation now precedes mutation, source/link/structural
bindings are compared directly, history resume starts at its exact revision
offset, retained scope bytes have local admission/accounting, and edit after
tombstone cannot silently restore Live. A second source pass found no remaining
confirmed blocker. Allocation-failure rollback is source-reviewed, not yet
qualified by allocator fault injection.

## Explicit non-claims and remaining work

- Scope/policy values are trusted host input, not authentication or an access
  policy engine. The host must authorize every operation and referenced target.
- Byte charges are conservative logical frame/receipt/scope accounting, not a
  peak RSS or allocator-overhead guarantee. No throughput/performance claim.
- There is no SDK importer qualification. Phase 3B must exercise all eight
  Phase 2 tool-semantics scenarios against actual normalized adapter input.
- No durable cursor, retention/erase/reconciliation API, actor-alias registry,
  persistent allocator, crash recovery or cross-store transaction guarantee.
- No MDBX DBI/schema, graph/search/embedding activation, derived-knowledge
  publication adapter or tool/runtime execution. Those retain their separate
  Phase 4/5/6 owners. This is not a complete chat-memory product profile.

## 2026-11-24 final targeted qualification

The earlier allocation-failure limitation above describes the original review
baseline. The new isolated `conversation_store_failure_test` uses standard
replaceable C++ allocation functions in one single-threaded executable, not
runtime allocator detours or a production test seam. Fixtures/arguments and
verification allocate outside its gate. Each successful operation's allocation
count is measured, every ordinal is failed once, and the next ordinal must
succeed with the same count. Injection disarms before unwinding/verification.

GNU 15.2 Debug exercised **197 actual bad_alloc failures**: create 3, append 12,
correction 47, exact read 18, history/continuation 22, Current/continuation 95.
Every failure preserved canonical history/receipts, current content, generation,
unused conversation/Entry/revision IDs, exact remaining byte capacity and old
pinned cursors; the same operation then retried successfully. Coverage is
limited to replaceable ordinary/aligned/nothrow C++ allocations in those
fixtures, not malloc/OS/host/asynchronous faults or arbitrary inputs. MSVC
checked STL may allocate inside noexcept constructors; that configuration
explicitly skips this test with code 77, not a passing fault-injection claim.

`conversation_store_revision_test` additionally checks correction after a
descendant, multiple retained revisions with differently targeted siblings,
occurrence-head advancement through an old representation, pre/post-correction
frontiers, exact historical restore, and nonexistent/cross-conversation/denied
targets. The fold intentionally compares occurrence identity for head
advancement; exact target revision remains immutable in history. Different
revision targets do not turn siblings into a linear chain. No revision-sensitive
branching model or store/codec implementation change was needed.

The [pagination experiment](../experiments/2026-11-24-conversation-current-pagination.md)
records nine executed full-versus-paged cases, API decode/byte counters, elapsed
time and process peak memory. Current repeats the fold per page; the tested
reference subset is bounded, not a scalable serving/SLA qualification.

GNU Debug and MSVC Release targeted CTest: **3/3 passed each** (original store,
revisions, allocation failure). Exact-head hosted results are recorded in the updated PR
receipt when observed; the earlier-head CI is not qualification of these new
tests. No confirmed blocking implementation defect was found by these cases.
Verdict: **READY for owner review within the declared reference subset**;
Phase 4/5 persistence, crash/reopen and scalable projection qualification remain
explicitly open.
