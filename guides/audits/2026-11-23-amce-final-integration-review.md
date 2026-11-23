# AMCE final independent review and integration gate

- Date: 2026-11-23. Owner: conversation domain.
- Scope: [Draft PR #690](https://github.com/LimiNode/agent-memory-cpp/pull/690),
  after the architecture correction at
  `994b0d7c9ed4534a08586c3dd30c8cb0165e7b80`.
- This is an implementation/review record, not another wire specification.
  [Contract section 4.2](../conversation-event-contract.md#42-event-codec-v1-phase-2-review-contract)
  remains the normative owner.
- The final publication receipt in the PR owns exact final HEAD/tree, hosted
  CI/Packaging results and the explicit READY/HOLD recommendation. No merge,
  release, migration, ConversationStore or executor is authorized here.

## ToolResult pre-freeze decision

The previous supported `257/1` required both an exact local call EntryRef and
a nonempty result artifact. It could not represent a partial imported journal
with only an external call token, or a cancellation/unknown outcome without a
retained result body. This was a schema restriction, not evidence of malformed
import data. The audit's explicit-unresolved-evidence intention and the DTO did
not agree.

| Alternative | Consequence |
| --- | --- |
| Keep narrow typed schema; retain incomplete records as original artifacts or a future unsupported payload | No invented references, but requires an additional import-only grammar and prevents typed handling of already modeled unresolved evidence |
| Reuse `ConversationLinkTarget`, make result binding optional | Two existing semantic distinctions become explicit without new event kinds, fabricated IDs or arbitrary JSON |

Choose the second alternative before persisted schema freeze. Reuse exactly
the existing unresolved record (optional AMXR external plus mandatory artifact
and locator evidence). A resolved call still requires exact namespace,
conversation, occurrence and revision, must be in the containing conversation,
and cannot name the containing occurrence. Resolution must check that policy
again; unresolved evidence never establishes local identity, scope access,
target existence, call type or call-ID matching.

Both tool observations always require valid source observation evidence and a
nonempty binary call ID. Any present result binding must be nonempty. Absence
means no retained body; it is independent of all four outcomes. Source evidence
is sufficient for observing an outcome even when the body or local call is
missing. It is not proof that an execution occurred or that an outcome claim is
true. Cross-record authenticity/access/admission remain separate.

The common target reader/writer and validator are shared with links, preserving
their established grammar and policy rather than copying unresolved rules into
a new schema. No integrated Phase 1 type/layout/default validator is weakened.

The affected old `257/1` literal is preserved with the full seventeen-vector
`994b0d7c` set under
[historical fixtures](../../tests/domain/fixtures/historical_amce_premerge_v1/README.md).
Those pre-merge bytes were never a persisted contract. They are not another
supported dialect and cannot downgrade a malformed current known schema to
opaque. Other payload schemas and the envelope version do not change.

## Integration ancestry and overlapping paths

The feature authoring baseline was `386e26fe9176d268722894cd9f48df33f3b7bf72`.
The earlier hosted synthetic merge used main
`fae028d4e946f28fd5ad82a3d5ddfcee2528c69a`. At this review's synchronization,
remote main is `aea7d89f657c04b7b6388353530d9418a6a6ad81`, including the
separately integrated search work. Its post-`fae028d4` changes qualify the exact
semantic index lifecycle and installed consumers; they do not modify AMCE.

Ordinary merge commit `ce92f0892a0c182e2989daf007cae8c760a8a431` has parents
`994b0d7c9ed4534a08586c3dd30c8cb0165e7b80` and
`aea7d89f657c04b7b6388353530d9418a6a6ad81`; its tree is
`993806cdef1409f69615dcb5b542ccc04d4ba941`. No rebase, squash or force-push.

| Overlap | Verified composition against latest main |
| --- | --- |
| `CMakeLists.txt` | Latest main source list/provider/compiler wiring retained; AMCE adds its single `.cpp` to the existing static target exactly once |
| `.github/workflows/packaging.yml` | Latest main's exact-semantic consumers/provider fix retained; installed/relocated codec executions remain exit-code guarded on all three OSes |
| `guides/retrieval-roadmap-coverage.md` | Search maturity updates retained; Conversation row alone records integrated Phase 1 and separately reviewed Phase 2, without claiming a store |

The clean merge is only textual evidence. Final builds, tests and actual codec
consumer execution must qualify the assembled result; previous synthetic-merge
checks do not substitute for the final receipt.

## Fresh read-only review

A non-author agent reviewed the corrected wire/validation/Phase 1 boundary,
first at `994b0d7c` and then checked unchanged reviewed files at `ce92f089`.
No confirmed blocking implementation defect was found. Its independent oracle
check passed all seventeen then-current literals. This establishes only that
reviewed source and oracle boundary, not final-head execution.

Confirmed source invariants: exact framing and body exhaustion; strict ordered,
unique extension IDs and 0/1 critical flags; unknown pair retention with no
known-malformed downgrade; common metadata validation even for opaque bodies;
explicit unsupported typed gates; finite conjunctive budgets charged before
copies/reserves; unchanged integrated Phase 1 behavior; lossless conversion or
explicit refusal. The separate semantic-economy pass found no confirmed dead
path or unjustified parallel implementation in the reviewed code.

The fresh review also recommended the exact unresolved/optional schema change
above. The author checked the resulting implementation delta against those
invariants: the only target-grammar helpers are shared by links/results, their
existing Phase 1 checks are unchanged, artifact presence uses strict optional
framing, and no unsupported fallback was introduced. This author check is not
misrepresented as a second independent review. Final-head tests and hosted
checks are recorded in the PR receipt; human integration approval remains with
the PR owner.

## Local final-source verification

- GNU 15.2 Release, JSON/MDBX OFF: full build and CTest 57/57; target tests
  repeated after the final negative controls. MSVC 19.44 x64 Release,
  JSON/MDBX ON: full build/public headers/examples and CTest 104/104.
- WSL GNU 15.2 Debug, ASan/UBSan with leak detection: Phase 1/AMCE 2/2 and
  standalone AMXR 1/1. The codec run took 16.09 seconds.
- Twenty independent Python/literal vectors agree, including unresolved call,
  optional external evidence and resolved success with no retained result body.
- All outcomes cross both target forms and artifact-presence states. Invalid
  evidence/AMXR, fabricated/self/cross-conversation targets, missing source,
  empty present artifact/call ID, reserved target/presence/outcome tags and
  stricter writer/reader evidence budgets refuse as expected.
- 2,796 exhaustive truncations and 20,872 bounded mutations, accepted-frame
  canonical re-encoding, combined 21,186,997-byte and exact 32 MiB/all-field-cap
  fixtures pass. Historical 994b0d7c literals retain exact Git blob
  `40cc81a239913d833a54119ba62bbe7ebafca78b`.
- GNU and MSVC examples execute the partial-result path as well as the complete
  tool observations. Hosted packaging must additionally execute that same
  example from installed and relocated packages before final READY.

## Selected AMXR memory strategy

The [same-envelope experiment](../experiments/2026-11-22-amce-envelope-resource-qualification.md#2026-11-23-same-envelope-retained-versus-repeated-amxr)
compares retained and repeated serializers at the same integration source,
grammar, validation and budgets. Both nested and combined frames are
byte-identical by binary comparison and SHA-256. Twelve measured processes,
48 operation measurements and 4,848 timed samples passed; hashes were stable
before/after. These are frozen strategy measurements, not measurements of the
concurrent ToolResult correction.

Select repeated bounded serialization. It removes the aggregate cache/cursor
and lowers encode peak live requested bytes from 33,849,524 to 16,990,131
(nested) and 38,111,927 to 21,252,534 (combined), at approximately 13-16% more
complete encode time and greater cumulative allocation volume. The observed
additional 6-7 ms on these large frames is accepted for a 44-50% lower peak
and less internal state; no production SLA or concurrent-throughput claim is
inferred. Both strategies use the same standalone AMXR without changes.

## Final semantic closure after `45d3f25c`

The architecture owner accepted the current envelope and `256/1`, `257/1`
layouts. The final batch refines [contract section 4.2](../conversation-event-contract.md#42-event-codec-v1-phase-2-review-contract)
and API Doxygen: the call retains the original request, and a result retains a
source-reported outcome at a documented observation boundary, not verified
execution or world state. Approval, rewriting, attempts and effect verification
remain distinct. No `Denied` outcome, lifecycle schema, runtime header or
execution logic is added. The [vocabulary decision record](2026-11-23-amce-event-vocabulary-decision.md)
preserves the completed research's pinned sources, counterexamples and deferred
scope; it does not duplicate the normative grammar.

Eight authored normalized scenarios in `tool_observation_semantic_fixtures()`
exercise denial, synthetic recovery, timeout, request rewriting, repeated tokens
across runs, several attempts, unresolved/no-body import and unverified reported
success. A deliberately contradictory but structurally legal outcome remains
codec-valid. These checks demonstrate retained references/outcomes and canonical
round-trip, not a working importer, deduplicator, retry policy or effect verifier.
The expanded installed-consumer example likewise claims preservation only.

The codec implementation/API, wire registry, versions, standalone AMXR and all
twenty independent goldens are unchanged. GNU Release JSON/MDBX OFF CTest
57/57 and MSVC Release JSON/MDBX ON CTest 104/104 pass again, including the new
scenarios and public-header tests. The latest example executes on both compilers
and from a freshly installed GNU package genuinely moved to a new prefix with
the original prefix absent. Independent read-only delta review found no blocker;
its oracle check also matched twenty literals. Final sanitizer and exact-HEAD
hosted CI/Packaging outcomes belong to the PR publication receipt, not an
anticipatory claim here.

No new benchmark is run: runtime behavior is unchanged, and the earlier
source-bound memory/timing experiment remains a historical measurement at its
recorded source/binary hashes. Final READY/HOLD and the exact published tree are
reported separately after hosted checks. PR #690 stays Draft pending owner
integration confirmation; this batch performs no merge.

## Open boundaries, not merge scope expansion

- Patch/Compaction typed schemas, ConversationStore, MDBX journal and metadata
  read projections are not prerequisites for this bounded review.
- Codec validation is not authentication, authorization, artifact/call lookup
  or cross-record matching. Unknown schemas cannot be semantically admitted.
- Bounded mutation/sanitizer tests do not prove the absence of every defect.
- Standalone preflight latency and allocator-reserved memory are not separately
  measured; this is non-blocking if matched-grammar complete-encode and memory
  evidence supports the strategy decision.
- Local timing is directional, not a production latency guarantee. The
  [existing experiment](../experiments/2026-11-22-amce-envelope-resource-qualification.md)
  owns the same-grammar strategy comparison and its retained raw/hash bindings.
