# Conversation durable P4-4 implementation qualification

- Date: 2026-11-27.
- Status: **implementation tested; qualification OPEN**. This is an internal
  Draft result, not a deployment, release or merge receipt.
- Source: `feat/conversation-durable-p4-4-implementation@182dd95e`.
- Base: integrated P4-3 tree `8a092d515f99ed34e50ffbc7642cd2fb80e4ef8d`.
- Owner-approved contract: [admission v2 proposal](../conversation-durable-admission-v2-proposal.md),
  accepted at `aa0bae40` and integrated through #704.

## Confirmed in the implementation

- `ConversationEdited`, `ConversationTombstoned` and `ConversationRestored`
  use the existing source DTO variant and existing AMCE domain payloads.
- The active profile is `conversation_durable.v2.be.keys.sha256.values.witness2`.
  The historical witness1 profile is rejected before marker, row, counter or
  DBI mutation; no dual-reader or migration path is present.
- Coordinated admission preserves one source mapping per occurrence, exact
  targets, replay/conflict ordering and atomic allocator/history/generation
  publication. Correction remains a representation revision.
- `IConversationHistory` is the read-only seam; no public durable store is
  advertised. Current uses the existing three-DBI profile and owning,
  instance-bound continuations.
- Exact `EntryRef` target preflight includes the full reference charge.
- Current live-byte accounting retains the conservative row charge and adds
  retained historical content for Restore. Tool-call/result rows are ignored
  by the message fold without attempting a message-state map lookup.

## Executed checks

| Environment | Command/result |
| --- | --- |
| MSVC Release, MDBX OFF | `ctest --test-dir .build-p4-4 -C Release --output-on-failure` — **63/63 passed** at this commit |
| GNU/MinGW Release, MDBX ON | `ctest --test-dir .build-p4-4-mingw -R 'agent_memory_(conversation_durable_codec_test\|conversation_source_import_test\|mdbx_conversation_durable_test)' --output-on-failure` — **3/3 passed** |
| Source-shaped state parity | Durable tests cover v2 literals, Edit/Tombstone/Restore, Restore-to-Restore, sibling ambiguity, edit-after-tombstone ambiguity, exact targets, tool-call/result rows and Current continuation/frontier parity |
| Formatting | `git diff --check` and `git diff --cached --check` passed before publication |

The GNU build uses the currently pinned mdbx-containers tree (`c2a2326`, the
pre-#336 pin). The combined #335/#336 dependency tree is qualified separately
in Draft #705 and has not been merged into this P4-4 branch.

## NOT RUN / blocked gates

- Frozen 2048-root/page-32 Current benchmark, repeated latency percentiles,
  allocations, peak RSS, transaction duration, persisted bytes and larger
  32768-root regimes remain **NOT RUN**. The current plan therefore makes no
  long-history serving or projection/checkpoint claim.
- Full MDBX fault/ASan/UBSan and installed-consumer qualification at the exact
  P4-4 head remain **OPEN**. The bundled MSVC MDBX build is blocked by the known
  parent `libmdbx` C compilation failure; parent-provided dependency lanes are
  not evidence for bundled support.
- The mandatory 65,537-admission scale fixture, native device/power-loss tests,
  production activation, migration, release and automatic merge remain outside
  this Draft.

The next acceptance action is the frozen Current decision screen on a named
host, followed by combined-tree qualification with the dependency-update line.
