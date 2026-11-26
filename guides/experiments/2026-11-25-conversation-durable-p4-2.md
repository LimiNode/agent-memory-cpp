# Conversation durable P4-2: process recovery and admission races

- Dates: 2026-11-25..26. Status: LOCAL QUALIFICATION PASS; hosted gate PENDING.
- Baseline: #701 integrated at `2c895c9812128dcfc3ccd07ae84a7175e5ff371c`,
  tree `86ec0e739dd382a45b0c6bd89302a276abbc8dfa`.
- Owner: [physical contract](../conversation-durable-physical-contract.md),
  section 7; [ordered plan](../conversation-durable-store-phase4-plan.md).
- Scope: internal optional adapter only. No public Current/correction/store
  factory, wire change, schema migration, release or production activation.

## Question and predeclared acceptance

Does the existing three-DBI transaction remain atomic across acknowledged
process termination and competing independent-process admission, preserving
the reference source witness, original receipt, allocator and generation?

Use fresh temporary databases and bounded parent/child IPC barriers, not
timing sleeps. A fresh verifier process checks complete state, then replay and
changed-witness Conflict; committed IDs must not be reused. Race results are
compared with the unchanged reference oracle in actual commit order. Include
schema creation, conversation creation, each tentative import write and the
two distinct acknowledgment windows.

Required races: equal witness, conflicting witness, distinct sources in one
conversation, distinct conversations in one profile, digest collision,
persistent quota, aggregate bucket rows/bytes, initialization/attach and
reader/writer visibility. Native map/readers/DBI failures and incompatible
flags/geometry/profile are separate from injected exceptions.

## Evidence boundary

The private post-commit hook is after successful wrapper return but before
operation return. Child-side post-return/pre-receipt termination is separate.
There is no native-commit-internal hook and no dependency modification. The
[P4-1 note](2026-11-25-conversation-durable-p4-1.md) preserves the independent
MSVC allocation-failure evidence at the native/wrapper boundary.

## Executed matrix and reference parity

All 22 crash cases use acknowledged parent/child barriers followed by process
termination and a new verifier process: schema initialization (4), conversation
creation (8), source import (10). Precommit termination preserves the complete
old state; post-wrapper-commit and post-operation-return termination preserve
the complete new state. The verifier checks all three DBIs, profile/incarnation,
descriptor, every counter/charge/allocator, generation rows, canonical AMCE
against the unchanged reference oracle, full source witness/original receipt,
total row count/no orphans, identical replay, changed-witness Conflict and the
next committed ID. Creation retains reference-compatible NoChange semantics.

Eight independent writer scenarios run in both operand orders (16 executions):
equal witness, conflicting witness, distinct sources in one conversation,
distinct conversations in one profile, forced digest collision, persistent
quota, aggregate bucket rows and aggregate bucket bytes. The first child holds
its actual writer transaction before commit. The contender must acknowledge
native `MDBX_BUSY` from `MDBX_TXN_TRY` before the parent releases the first;
the normal adapter admission then performs the second write. This test-only
probe is not a new production transaction framework. Initialization/attach
contention and pinned-reader/committed-writer visibility add two executions:
18 independent-process scenarios total. Reference replay uses actual committed
generation order, not process-start order; refused writes consume no identity
or generation. Fresh verification checks both accepted and refused outcomes.

| Failure class | Executed evidence / recovery |
| --- | --- |
| Native `MDBX_MAP_FULL` | 1 MiB fixed map, approximately 600 KiB input; raw binary snapshots/reopen unchanged; repeated refusal, explicit host geometry increase, same next ID on retry |
| Native `MDBX_READERS_FULL` | Real worker-thread reader slots exhausted (112 observed in Windows GNU run); exact/history/open propagate native error; release/reopen and retry preserve identity |
| Native `MDBX_DBS_FULL` | 19 configured DBIs, 3 journal plus 16 tentative host tables; abort removes all tentative tables; missing-profile open at full capacity propagates native failure |
| Policy/schema refusal | Existing profile reserve violation, actual NOMETASYNC/SAFE_NOSYNC/WRITEMAP flags, actual geometry change, descriptor/quota/map mismatches fail closed without rewriting data |

Capacity increase is an explicit host action in the fixture, never an automatic
adapter retry or map-resizing policy. Native errors are not NotFound. Real
device I/O/native commit failure remains unexecuted, distinct from these native
transaction-start/write failures and injected precommit exceptions.

## Qualification receipt (2026-11-26)

| Configuration | Result |
| --- | --- |
| Windows GNU 15.2, Release, MDBX ON, JSON/research OFF, examples ON | Full CTest **111/111 PASS**, 48.09 s |
| Windows MSVC 19.44, Release, same options | Full CTest **111/111 PASS**, 52.73 s |
| Windows MSVC, Release, MDBX OFF | Full CTest **65/65 PASS**, 12.49 s |
| Linux WSL Ubuntu, GNU 15.2, Debug, ASan+UBSan, MDBX ON | Relevant durable/codec/import CTest **8/8 PASS**, 31.38 s; leak detection and halt-on-error, no sanitizer diagnostics |
| Installed MSVC package, six consumers | **6/6 PASS**, explicit C++17; no Internal headers or source/build/worktree paths in installed exports |
| Independent final source review | **READY**, no confirmed blocker; read-only review, not a rerun of platform tests |
| Exact-head hosted CI / Packaging | **PENDING**; publish final head/run URLs and decision in the Draft PR receipt |

Times are single-run qualification duration, not latency benchmarks. The Linux
dependency and project both enable ASan/UBSan (`ENABLE_ASAN`, `ENABLE_UBSAN`,
`-fsanitize=address,undefined -fno-omit-frame-pointer -g1`); the pinned libmdbx
build must be sanitizer-aware. An earlier Linux CTest selection registered the
not-yet-built process target (7 passed, 1 NOT RUN); the completed frozen-source
8/8 run above supersedes that incomplete attempt, not a hidden runtime failure.

Builds reside under canonical `.build/conversation-durable-p4-2-{gnu,msvc,
msvc-off,linux-asan}`. Raw local logs are retained under canonical `.artifacts/`:
`p4-2-{gnu,msvc}-freeze-{build,ctest}.log`,
`p4-2-linux-asan-freeze-ctest.log`, `p4-2-gnu-matrix-verbose.log`,
`p4-2-msvc-off-{build,ctest}.log`, and
`p4-2-msvc-{install,consumer-configure,consumer-build,consumer-run,
consumer-package-audit}.log`. Reproduce full tests with
`ctest --test-dir <build> [-C Release] --output-on-failure`; run the Linux
selection with `-R 'conversation_(durable|source_import|import_failure)'`,
`ASAN_OPTIONS=detect_leaks=1:halt_on_error=1` and
`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`. Process harness support is
Windows/Linux only; macOS registration is intentionally absent.

Frozen implementation/test SHA-256 (file bytes in the qualified checkout):

| Source (under repository root) | SHA-256 |
| --- | --- |
| `src/agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp` | `98b1d43a669f0b312e9117ec5534b1f6423bc193797dd2119c605b3b4df31039` |
| `src/agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.cpp` | `f327b32689a427bd60f8e64082814f8df1397851d108b27f51b382c831cca320` |
| `tests/infrastructure/mdbx/conversation_durable_process_test.cpp` | `f6141b51dcb0bc0cd717546448629b1ab75353afbe2c7ca412bc6366d408572c` |
| `tests/infrastructure/mdbx/conversation_durable_native_failure_test.cpp` | `bbf5d0c2d0824959da93152250d072cf1a2642352b8442a55f7c5e7d7187d0a2` |
| `tests/infrastructure/mdbx/conversation_process_harness.hpp` | `9b101bd3618ade60d048e1f5f3d6307024d7978ac4e0acb921783bde6b18078c` |
| `tests/CMakeLists.txt` | `8871adb9f6c6c85792c275ed319d71ca6ff05d0ac2b5f8dfdbdaa3b646dad996` |

The PR receipt binds this local reviewed source to the eventual exact commit
and hosted check runs. No AMCE/AMXR wire, digest/preimage, golden, persisted
schema or public factory/interface change is part of this slice.

## Interpretation and remaining gates

Local recovery/concurrency/failure qualification supports the bounded internal
admission claim, not complete Phase 4. P4-3 remains gated on successful exact-head
CI/Packaging with no unresolved blocker. No merge of either new Draft is
authorized. P4-3 must remove the Entry-order generation early-break shortcut,
add revision-aware seeks, CAS receipts and pinned historical pages; no Current
projection is implemented here.

NOT RUN: device power-cut/firmware/filesystem fault qualification, real storage
I/O/commit failure and termination inside native commit. Future storage-fault
qualification needs a dedicated controlled filesystem/device fault harness;
process termination does not replace it. P4-4 measured Current selection and
P4-5 actual 65,537 admissions/fresh reopen/pages/replay, memory/latency and
complete installed public-profile qualification remain separate gates.
