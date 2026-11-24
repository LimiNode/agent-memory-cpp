# AMCE pre-merge architecture correction qualification

- Date: 2026-11-22. Owner: conversation domain.
- Review target: [Draft PR #690](https://github.com/LimiNode/agent-memory-cpp/pull/690).
- Integrated baseline: `386e26fe9176d268722894cd9f48df33f3b7bf72`.
- Superseded unmerged closed candidate: `4ec7866f31e0d0b2e3d2ca1933b27c3e78f767ec`.
- Status: implemented and locally qualified for review, not merged or released.
  The PR publication receipt supplies the exact corrective SHA and exact-head
  CI results; earlier successful CI on the closed candidate is not corrective evidence.

The 2026-11-22 results below qualify the initial corrected envelope, not all
later source changes. The [2026-11-23 final review](2026-11-23-amce-final-integration-review.md)
owns the subsequent ToolResult pre-freeze correction and integration gate.
The dated same-grammar experiment appendix selects repeated bounded AMXR
serialization instead of retaining prepared frames. The final PR receipt binds
the assembled schema/strategy to its new HEAD and CI/Packaging evidence.

## Contract and implementation

The sole wire owner is [contract section 4.2](../conversation-event-contract.md#42-event-codec-v1-phase-2-review-contract).
The [source-bound limits/API audit](2026-11-22-amce-premerge-architecture-audit.md)
records the old constraints and correction rationale. The existing
[ADR](../adr/2026-11-conversation-event-identity.md) records envelope versus
payload evolution and the storage/runtime boundary.

The additive envelope retains the integrated Phase 1 aggregate, variant and
one-argument validator. An explicit field-policy validator changes only resource
budgets. AMCE uses owning bytes and an explicit borrowed C++17 view. Unknown
payloads/schemas and metadata remain attached across read-modify-write;
unsupported interpretation is a typed refusal. Malformed supported payloads
cannot downgrade to opaque, and Phase 1 conversion refuses every extension.

ToolCall/ToolResult have real schema1 encode/decode with exact call revision,
binary call identity, artifact bindings, observation evidence and four distinct
outcomes. Patch/Compaction have reserved IDs and documented reference/lineage
invariants, but intentionally no typed schema or semantic-admission claim yet.
The tests prove their opaque retention and interpretation refusal, not their
inner semantics. Adding further schemas requires registry review and goldens,
not an envelope-version change.

## Local verification

| Check | Observed result |
| --- | --- |
| MinGW GNU15.2, Release, C++17, JSON/MDBX OFF, full build/public headers/examples | PASS; CTest57/57 |
| VS2022 MSVC19.44, x64 Release, JSON/MDBX ON, full build/public headers/examples | PASS; CTest104/104; repeated after corrections |
| WSL Ubuntu GNU15.2, Debug, AddressSanitizer+UndefinedBehaviorSanitizer, leak detection | AMXR/Phase1/AMCE3/3 PASS, including implementation-cap tests |
| Independent Python oracle `--check` | 17 literal vectors match |
| Literal bytes and SHA-256, semantic equality, canonical re-encoding | 17/17 PASS |
| Exhaustive truncation / bounded deterministic mutation | 2,313 / 17,974 cases; accepted mutations re-encode byte-identically |
| Combined Phase1 field maxima with258 maximal AMXR frames | 21,186,997 bytes, canonical round-trip PASS |
| Default opaque and extended typed frame ceiling | Exactly33,554,432 bytes; max+1/resource refusal PASS |
| All implementation field/count ceilings in one frame | Text8MiB, attachments/links4096, binding/locator65536, recipe4096, extensions1024/8MiB; exact32MiB PASS |
| Logical object accounting | Exact18,439-unit boundary PASS; one less refuses writer and reader |
| Invalid profiles and cap+1 per dimension | InvalidLimits; no unbounded sentinel |
| Strict reader versus wider writer | ResourceLimit, not corruption; pointwise coverage checks PASS |
| Input ownership/null/empty/reuse, binary00/FF, unknown schema/extensions, critical gate | PASS |
| Standalone headers and domain umbrella | Compiled as first include by repository header tests |
| Installed and genuinely moved/relocated GNU package | Separate strict C++17 consumers compiled, linked and executed; includes tool observations and advisory metadata |
| Historical closed-candidate preservation | Original test/oracle Git blobs preserved exactly under historical fixture directory |
| Standalone AMXR source/header/tests | No diff; unchanged compatibility control passes |

The narrow commands are the repository's CMake/CTest targets, with
`--output-on-failure --timeout 120`; MSVC uses `--config Release` / `-C Release`.
Sanitizers use `ASAN_OPTIONS=detect_leaks=1:halt_on_error=1` and
`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`. The independent oracle is
`py -3 tests/domain/fixtures/conversation_codec_goldens.py --check`.
Build products stay in the root `.build/` tree, not the feature worktree.

## Resource and performance evidence

The [resource experiment](../experiments/2026-11-22-amce-envelope-resource-qualification.md)
owns reproducible commands, source/binary/raw-output bindings, median/p95/p99,
allocation counts/requested bytes, live-requested peaks and process working-set
high-water measurements. Fixture construction and destruction are excluded
from operation timing; exact preflight is included in encoding, not falsely
reported as an isolated measurement. Old/new full codecs have different wire
contracts; identical-byte string/vector copies are the separate container control.

In the original 2026-11-22 measurements, AMXR preparation happened once per
occurrence and was charged before retention. That now-superseded strategy
traded reduced repeated work against a larger peak because prepared frames
and output coexisted. The 2026-11-23 matched-grammar comparison below the same
experiment selects one bounded temporary per occurrence/pass. Neither a frame budget nor the
logical object/owned-byte charges promise a process-RSS bound. Header inspection
does not allocate bodies or certify their validity. No MDBX/index/projection
throughput is claimed without an implemented store.

## Review boundaries and remaining risks

- Independent format/security review remains the next integration decision;
  this work does not authorize merge. Local bounded mutation/sanitizer tests
  are not exhaustive proof against every malformed input.
- Exact-head hosted CI/packaging status is reported in the PR receipt after
  publication. A missing/failed job must remain an open finding there.
- Unsupported inner schemas cannot be semantically validated or admitted;
  critical metadata cannot be consumed without future support.
- Scope access, source authenticity, referenced artifact/call existence and
  matching call ID require future admission/read owners. Codec success grants
  no access and performs no execution.
- Performance is directional single-host, allocation-instrumented evidence;
  separate standalone preflight timing and allocator-reserved memory are not
  measured. The AMXR strategy comparison records both peak memory and cumulative allocation volume.
- The old experimental bytes were never an integrated persisted contract;
  no migration reader/backward-compatibility promise is made for them.

No ConversationStore, MDBX journal/schema, executor, release, database migration,
new mandatory dependency or change to standalone AMXR is included.
