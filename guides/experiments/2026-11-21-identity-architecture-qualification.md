# Identity Architecture: implementation qualification

Date: 2026-11-21. Status: **REVIEW_REQUIRED, not CLOSED**.

Question: can a reusable, bounded external identity distinguish provider-scoped
integers, opaque strings and composites without changing existing logical or
physical identities? The intended acceptance signal is independent golden and
negative conformance plus unchanged legacy/numeric behavior and a real installed
C++17 consumer. This is not a throughput benchmark or production activation.

## Source and dependency boundary

Verified main remains `acd5ad5545386b3feeaa79f8a587855b60801b4d`.
The [normative policy](../identity-policy.md) is Draft #682 at
`95262b9d52cd96d88a05b106f5df5e0e498e4340`. Its companion implementation is
[Draft #684](https://github.com/LimiNode/agent-memory-cpp/pull/684) at
`a27e0c0558314d3760b41217ca37b29ab71c3bff`, based on that policy and verified
main ancestry. This conformance addendum does not change library or test code.

- Implementation `src` tree: `213eb5fad08b5e16cde6ef2583c2d19366a0577c`.
- Dependency tree: `b816ddeccafa0375c15b977e71d4a36f6728f59f`.
- libmdbx: `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`.
- mdbx-containers: `c2a2326273a3d85f29283e2cd34942c7b8d72cbd`.
- Local final executions ran with conformance source
  `7c80384d5e0a04b568ea8faf04fbdf9ac011e6cf`; source/dependency trees above
  are identical to #684. Numeric receipt records `source_dirty=false`.

Only four source paths differ from main: new ExternalObjectRef.hpp/.cpp, its
domain umbrella include, and a DocumentId comment clarifying logical identity
across ordinary revisions. Existing declarations, constructors, physical
codecs/DBIs, digest recipes, numeric/text ordering and defaults are unchanged.

## Implemented decision and public consumer

The policy owns the grammar and all identity categories; this report does not
define another codec. The implementation supplies ExternalIdentityScalar,
ExternalIdentityComponent, ExternalObjectKey and ExternalObjectRef, validation,
type-strict equality, canonical unsigned-byte ordering, AMXR serialization /
strict deserialization, and full SHA-256 fingerprint. SHA is reused, not copied.

Provider, service instance, actual identity domain, object kind, parent presence
and typed keys all affect equality. Integers are not strings; opaque text is
not parsed or normalized. Named ordered composites are flat, bounded to 2..16
scalar components. Missing identity remains an unresolved observation/local
occurrence; no allocator or namespace authenticator is hidden in these DTOs.
Observer accounts are provenance unless the issuer's actual domain is local
to an account. The immutable adapter recipe supplies that decision.

The [public example](../../examples/external_identity.cpp), using
`<agent_memory/domain.hpp>`, is also compiled as the installed package consumer
`agent_memory_external_identity_consumer`. It round-trips a negative chat ID and
a chat-local message ID through 90 canonical bytes. No provider is inferred
from a legacy MessageId and no revision is folded into an object key.

There is no mandatory JSON dependency or JSON adapter. A future adapter must
preserve tags and exact integer precision; no integer-to-double path exists.
No existing persisted schema is connected to AMXR.

## Local executed checks

Host: Windows 11 x64, CMake 4.2-series installation, C++17 Release.
Builds/install prefixes are under
`C:/Users/User/AppData/Local/Temp/agent-memory-identity-closure-20261121/`;
E: had little free space, so no new source worktree or large build was created
there. This is a disk-placement choice, not a performance comparison.

| Configuration / observable check | Observed result |
| --- | --- |
| GNU/MinGW 15.2, JSON OFF / MDBX OFF, all configured CTest cases | 55/55 PASS, zero failed/disabled/skipped |
| MSVC 19.44.35228, JSON ON / MDBX ON, affected tests | 8/8 PASS, zero failed/disabled/skipped |
| Same MSVC profile, numeric conformance and diagnostic controls | 2/2 PASS; 19 child processes, five expected negative processes |
| GNU minimal installed `find_package` consumer and public example | PASS; installed target links and round-trip succeeds |
| Public headers as first include | PASS in both local profiles, including the new leaf/domain/root paths |
| DBI manifest and its self-test | PASS; no manifest edits |
| Fresh whole-header AST comparison and separate delta verifier | PASS; 149 headers, 565 declarations, four new types |
| `git diff --check` | PASS |

Affected MSVC cases are external_object_ref, header_self_sufficiency,
artifact_identity, knowledge_unit, storage_identity_mapping,
mdbx_storage_identity_mapping, mdbx_knowledge_unit_typed_store and
mdbx_canonical_flat_storage (all with `agent_memory_` / `_test` wrappers).
The unchanged mapping/typed/flat regressions exercise independent allocation
domains, nonallocating lookup, retirement/no committed reuse, foreign/inactive
transaction rejection, rollback, exact forward/reverse mapping and reopen.
The numeric runner additionally checks physical schema/comparator markers,
logical bytes/OLD fixture parity, interrupted transactions, fresh-process reopen
and five corruptions. Its `POST_NUMERIC_REJECT_OLD` classification remains
**not OLD-binary migration or numeric migration proof**.

The focused AMXR test uses hand-specified wire bytes rather than asking its
encoder for expected values. Coverage includes every scalar tag, ordered
composites, INT64_MIN/MAX, UINT64_MAX, `9007199254740993`, leading zeros,
provider/domain/instance isolation, negative parent, absent/present-empty,
embedded separators and U+0000, Unicode non-normalization, unsigned-byte order,
field/aggregate bounds, duplicate/bad components, malformed UTF-8, unknown tags
and versions, truncation, impossible lengths/counts and trailing bytes.
Decode is followed by exact re-encode. A simulated local occurrence remap
preserves the full external ref and SourceRevisionId. Legacy MessageId,
ThreadId, AuthorId, FactId, ScopeId and string Metadata compile and preserve
their values. The independent SHA golden over the 33-byte minimal reference is
`0291cbe118824162518094615ae35081005996ba2583e73ed962a52ee737a668`.

Reproduction, from the source tree (use disjoint local build directories):

```powershell
cmake -S . -B <gnu-build> -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release `
  -DAGENT_MEMORY_ENABLE_JSON=OFF -DAGENT_MEMORY_ENABLE_MDBX=OFF `
  -DAGENT_MEMORY_BUILD_TESTS=ON -DAGENT_MEMORY_BUILD_EXAMPLES=ON
cmake --build <gnu-build> --parallel 2
ctest --test-dir <gnu-build> --output-on-failure
cmake -S . -B <msvc-build> -G "Visual Studio 17 2022" -A x64 `
  -DAGENT_MEMORY_ENABLE_JSON=ON -DAGENT_MEMORY_ENABLE_MDBX=ON `
  -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED -DAGENT_MEMORY_BUILD_TESTS=ON
cmake --build <msvc-build> --config Release --target agent_memory_external_object_ref_test
ctest --test-dir <msvc-build> -C Release -R external_object_ref --output-on-failure
py -3 guides/audits/extract-identity-closure-delta.py --verify
```

The full affected target list above must be built before selecting that larger
CTest subset. The historical catalogue/extractor remain unchanged. The
[fresh type delta](../audits/2026-11-identity-closure-type-delta.json) uses a
new source SHA, verifies all 561 prior declaration hashes, and labels field-name
scanning as a heuristic, not a complete semantic identity classifier.
The historical extractor's `--verify` requires its original source checkout;
use the new delta verifier on this implementation tree. A changed historical
header must not be made to pass by relabelling the old catalogue.

### Retained raw evidence

Paths below are relative to the local build root above and are intentionally
not committed as bulky generated artifacts.

| Artifact | SHA-256 |
| --- | --- |
| gnu-all-results.xml | `f0fc2a7ec90944d6acdf4ef756788966cc16b6e5095f331e16351b44c50f1276` |
| msvc-on-results.xml | `108bd039c21e77f4ab409143abc4b84de0c4ed4061c3a31460640098e2091ffe` |
| numeric-results.xml | `f51dac3e4d21e282bff54fdc026bd2479565310594b69994868f347374053e9a` |
| msvc-on/tests/numeric-conformance-Release/run-9qqa4j8n/receipt.json | `4d7176d598d1f081a4a7e3ec416453d6be6557a53a64ce2d14f67b407f9b0857` |

Qualification harness failures were retained rather than presented as passes:
the first legacy fixture incorrectly assumed Metadata was a std::map; it now
uses its existing set/get API. A first multi-target Make invocation saw a newly
generated example target only on the next invocation; the explicit target
build and full build subsequently passed. The first catalogue delta collapsed
forward declarations by name; its count assertion rejected the result. The
corrected key includes header/kind/definition, explicitly rejects ambiguity,
and freshly rescanned all headers before reporting 561 unchanged + four new.
MSBuild warned about incremental outputs under TEMP and existing unrelated
compiler warnings remained; no suppression or product-wide cleanup was added.

## Independent review and GitHub qualification

Two agents compared the normative policy to the value model, field/tag order,
framing, equality, ordering, bounds, allocation guards, strict UTF-8 and signed
decode. No remaining wire/equality defect was found. One Conversation ADR
paragraph still deferred already-settled external decisions; #682 corrected it
and the reviewer read it back. This is internal peer review, **not a formal
GitHub review submission or authority to merge**.

The reviewed codec implementation SHA-256 remains
`6ae6a8163e9e12e7590910f1840180712409bf02dfad7ce097cb2f9419012580`.
After that review the header gained only its policy pointer; additional golden,
boundary and compatibility controls were compiled and executed as reported.

[CI 44947460498](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44947460498)
completed SUCCESS on exact #684 head
`a27e0c0558314d3760b41217ca37b29ab71c3bff`: 32 successful jobs; Full research
CTest and R5 combined integration evidence were SKIPPED. Actual successful
runtime jobs include Ubuntu/Windows/macOS, JSON-OFF header self-sufficiency,
MDBX wiring and MDBX MSVC Release/Debug. No skipped job is counted as a test.
[Packaging 44947460558](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44947460558)
completed SUCCESS, six of six jobs: minimal consumers on all three operating
systems, installed JSON, installed MDBX and historical exact-generation replay.
The three minimal lanes execute the new installed consumer before and after
relocation. Historical replay is still historical compatibility evidence, not
numeric migration proof. The conformance-only child PR receives its own exact-
head checks; its documentation classification is not a new runtime execution.

## Disposition and retained limits

#674 and #680 remain unchanged and technically eligible for separate merge
review, not authorized for integration by this task. #681 was corrected at
`006ddaf6c20b66191cc33176d6eed9d8c9b50095`: exact-head #549–#555 reuse matrix,
transaction/ownership/scope/legacy bypass and lexical binding/O(N) caveats.
Its CI 44946379414 passed the three documentation/gate jobs (22 skipped runtime
jobs); packaging 44946379453 passed all six jobs. #682 policy CI 44945826577
likewise passed docs/gates with runtime skipped; packaging 44945826614 passed
all six jobs. Neither documentation pass qualifies proposed runtime stores.

The old coordinator's TargetExact and continuous controlled-writer limitations
remain. The earlier storage TSan finding remains NOT_QUALIFIED and unresolved;
no new TSan run, suppression or unsupported false-positive conclusion is made.
Namespace registry persistence, binary ArtifactId lookup optimization and
conversation allocators retain named non-blocking owners in the decision
register. No correctness-critical AMXR rule is deferred to those workstreams.

AMXR is a bounded opt-in standalone format, not a new persisted DBI or canonical
event model. Host/adapter issuer reconciliation, external authorization and
future schema integration remain explicit separate duties. No throughput,
arbitrary-provider compatibility, sanitizer completeness or production retrieval
claim follows from these conformance tests. Conversation/Event Model,
TypedMetadata, ConversationStore and general coordinator implementation were
not started. No PR was merged, no release/tag/default activation or existing
database migration was performed. CLOSED still requires approved integration
and qualification on the exact final main.
