# Conversation durable P4-3: CAS corrections and historical frontiers

- Date: 2026-11-26. Status: **IN PROGRESS / NOT YET QUALIFIED**.
- Base: qualified Draft #702, exact HEAD
  `b546791191bb15b89436b7b09e25de08035a234b`, tree
  `917cf985c5f4f6076382ad078d4db9ea3937542c`.
- Entry gate: [P4-2 final receipt](2026-11-25-conversation-durable-p4-2.md#final-exact-head-gate-2026-11-26),
  CI 45850534246 (14 success, 16 conditional skips), Packaging 45850534204 (6/6),
  GNU/MSVC 111/111 each, MDBX-off 65/65, Linux sanitizers 8/8 and independent
  source review READY. #702 remains unmerged.
- Owners: [reference semantics](../conversation-reference-store.md),
  [physical contract](../conversation-durable-physical-contract.md),
  [ordered plan](../conversation-durable-store-phase4-plan.md).

## Question and predeclared acceptance

Can the private durable adapter append reference-compatible CAS corrections
without changing original source witnesses or allocation, and enumerate every
revision at a pinned historical frontier in EntryId/revision order?

No new DBI/schema, AMCE/AMXR wire/digest/golden, source identity or public durable
interface is authorized. The physical receipt and Correction-generation grammar
already exist. Reuse the pure immutable-binding predicate with the reference
oracle, preserving its behavior. No Current strategy is selected here.

The changed correction is one transaction: new immutable revision and receipt,
generation row, profile revision/charge counters. CAS precedes NoChange;
NoChange/refusal consumes no generation/revision, and source rows plus allocator
remain unchanged. Original source replay returns original@1 and original
generation after any correction/reopen, not the latest representation.

History must filter future corrections without stopping later Entries. Mandatory
regression: E1/R1 at generation 2, E2/R1 at 3, E3/R1 at 4, E1/R2 at 5; frontier 4
still returns E2/E3. Seek is the exclusive `(EntryId, revision)` pair, permitting
page size one inside one Entry's revisions. Future/invisible physical work is
bounded separately from complete AMCE frame decodes. Never skip an unreturned
eligible oversized item or report Complete when raw/input work is exhausted.

Each page opens/closes a fresh transaction and returns owning values. Opaque
continuations bind instance, incarnation/profile, conversation/scope/policy,
frontier and both seek coordinates; they retain no cursor/transaction/MDBX view.
Recreated instances reject old tokens, even for the same reopened environment.
The explicit `read_history_at` selector is qualification-only, not a portable
public token or historical-frontier export. It rejects explicit zero, future and
pre-conversation-creation frontiers; ordinary no-token reads select latest.

## Required qualification (PENDING)

- Reference parity: CAS-before-NoChange, immutable binding and retained target
  rules, receipts, exact old revisions, frozen source replay, quota/exhaustion
  and no-op/refusal stability.
- Pinned pages after corrections/appends, revision-aware page size one, invalid/
  foreign/default/policy-revoked/reopened tokens, explicit invalid frontiers,
  owning bounded results, zero/oversized/decode/raw/live budget behavior and
  progress through invisible revisions.
- Allocation-failure rollback, acknowledged correction crash windows before/
  after wrapper commit and after operation return, competing process CAS with
  real native writer-lock contention proof and fresh-process verification.
- Full Windows GNU/MSVC Release, MDBX-off, relevant Linux ASan/UBSan, installed
  C++17 consumers/private-header/export audit, independent final review and
  exact-head hosted CI/Packaging.

## Local qualification checkpoint

Windows Release uses GNU 15.2/Ninja and MSVC 19.44/VS2022 x64, MDBX ON,
JSON/research OFF, tests/examples ON. The separate MSVC MDBX-off build keeps
tests/examples ON. Flat dependency pins are unchanged: libmdbx
`fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`, mdbx-containers
`c2a2326273a3d85f29283e2cd34942c7b8d72cbd`.

| Check | Result at this checkpoint |
| --- | --- |
| GNU full CTest | PASS 114/114, 51.02 s |
| MSVC full CTest | PASS 114/114, 57.74 s |
| MSVC MDBX-off CTest | PASS 65/65, 9.66 s |
| Installed MSVC C++17 consumers | PASS 6/6; private headers and source/worktree export paths absent |
| Public-header manifest fixture | PASS positive and expected-negative configurations |
| Linux ASan/UBSan | PENDING |
| Independent final review | Initial P2 found/fixed; final re-review PENDING |
| Exact-head hosted CI / Packaging | PENDING publication |

These CTest wall times are single-run qualification durations, not latency
distributions or serving-performance measurements. Raw logs are retained under
the canonical checkout's `.artifacts/p4-3-*`; generated build trees are
`.build/conversation-durable-p4-3-{gnu,msvc,msvc-off,linux-asan,msvc-consumer}`.

Three new test targets cover reference correction/history parity, replaceable
allocation/precommit failure and acknowledged correction crash/process CAS.
The correction matrix passes eight crash boundaries and four contended races
(equal/conflicting proposals, both operand orders), including actual MDBX_BUSY
observation and fresh-process verification of all three DBIs, source witness,
receipts, counters, allocator and absence of orphan rows. GNU's allocation sweep
executes 29 ordinals, all before commit; MSVC executes 63, with 62 before commit
and one complete committed state despite a boundary allocation failure. Both
execute six injected precommit rollback cases. This preserves the wrapper's
uncertain-caller-outcome finding; an exception is not a rollback guarantee.

Independent review identified a late continuation scratch refusal that could
discard already consumed decode counters/frontier/resume state. The production
fix reserves continuation memory before any output decode. A new nonzero-live-
budget regression discovers platform accounting thresholds, checks an owning
one-row exhausted page and its exact continuation, and retries an oversized
first item without skipping it. Compiled against the pre-fix GNU archive it
failed at the intended frontier/token assertion; rebuilt against the fix it
passes on GNU and MSVC. Observed thresholds on both: exact 1,271,731 bytes,
history 1,275,858 bytes; these are conservative accounting boundaries, not RSS.
See `p4-3-continuation-regression-before.log` and
`p4-3-{gnu,msvc}-correction-fixed.log`.

Correction conservatively reserves the persisted codec frame ceiling before
encoding and then shrinks to actual size. Tight local live budgets can refuse a
small correction without implying corruption or an incompatible persisted
profile. NoChange does not prepare future changed-row writes. History keeps the
continuation reservation alongside retained row/object charges; invisible-row
scratch is released while its bounded physical work and pair progress remain.

Source SHA-256 at this checkpoint (final commit/hosted receipt follows):

| Source | SHA-256 |
| --- | --- |
| `MdbxConversationDurableInternal.cpp` | `5a4f06fae08be140e9c2c05f5e11109eecf27e27c8d901fa6adc3a74663ff7c6` |
| `ConversationStoreInternal.cpp` | `07e9f170980aff464a757f80ddd391546b286da33a3eb84227725b094c8fb4e0` |
| `ConversationStore.cpp` | `fafffcb553fd3dddf4af753de2a43bc6e28dead91ad8af0ea53eaecae7faaaeb` |
| `conversation_durable_correction_test.cpp` | `70d9dd1361419487e31044c0be379742e2b2c8d512b3eb625a15a0d8e4575b85` |
| `conversation_durable_correction_failure_test.cpp` | `8d0bd2f30e5bdc7ca9cb95e3824f8807a37d0f0a8c6b111981d9567472289f83` |
| `conversation_durable_correction_process_test.cpp` | `a7c8f11417b76542d146f0a1765e7bd430426e20fad90dc590f8223b43d1a79b` |

Reproduce from the pinned base plus this change: configure both Windows
toolchains with `AGENT_MEMORY_ENABLE_MDBX=ON`,
`AGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED`, `AGENT_MEMORY_ENABLE_JSON=OFF`,
`AGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF`, tests/examples ON. Build Release and
run `ctest --test-dir <build> -C Release --output-on-failure`; add
`-R conversation_durable_correction -V` for the explicit matrices. For core
repeat with MDBX OFF. Install Release, configure
`packaging/consumers/installed` against that prefix and the installed pinned
dependencies, then build/run all six consumers. The manifest fixture command is
`cmake -DAGENT_MEMORY_SOURCE_DIR=<source> -DAGENT_MEMORY_HEADER_FIXTURE_BUILD_ROOT=<fixture-build> -P packaging/consumers/header-manifest-fixture/run.cmake`.

## Remaining boundaries

Internal only, no fake Current/Empty/Complete or partial public implementation.
No automatic merge, release, deployment, database migration, retention/erase or
production activation. Real device I/O/native commit failure, native-internal
commit termination and power-loss durability remain NOT RUN without a controlled
device/filesystem fault harness. P4-4 measured Current and P4-5 actual 65,537
admissions/fresh reopen/pages/replay plus memory/latency stay open.
Small per-entry revision quota boundaries and seeded MAX generation are tested;
direct MAX-revision/global revision-count overflow seeds are NOT RUN. No seeded
counter is evidence of a physically populated large journal.
