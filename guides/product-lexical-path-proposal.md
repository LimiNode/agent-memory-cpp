# Product Lexical Path proposal

Status: **Draft proposal / owner decisions pending**. Audited baseline:
`main@aea7d89f657c04b7b6388353530d9418a6a6ad81` (2026-11-23).
This promotes the local next-slice plan into tracked review material. It adds
no production API, test-double authority, persisted fields or defaults.

Goal: one opt-in persistent BM25 route with canonical eligibility, final top-K
and explicitly verified source materialization. The current candidate source
is implemented; the target composition is not. This is a bounded step towards
[Search integration Phase 1](search-integration.md#phase-1-closure-gates), not
a new milestone or a release gate waiver.

## Owners and current integration gaps

Normative owners remain [retrieval execution](retrieval-execution-roadmap.md)
(eligibility, budgets, deduplication and completeness),
[AccessPolicy](policies-roadmap.md#12-accesspolicy-and-retrieval-enforcement-planned)
(host-issued authority), [persistent lexical foundation](persistent-lexical-index-foundation.md)
(index identity/publication), [KB-0 admission](kb0-revision-bound-retrieval.md)
(existing result axes), [backend integration](storage-backend-integration-roadmap.md#63-same-context-consistent-read-snapshot)
(transaction ownership) and [Raw Markdown ingestion](raw-markdown-product-ingestion.md)
(bounded source hydration). This proposal requests composition seams, not new
identity, metadata, ACL, provenance or transaction subsystems.

| Reuse decision | Existing surface and observed limitation | Proposed action after approval |
| --- | --- | --- |
| REUSE | [`MdbxPersistentLexicalCandidateAdapter`](../src/agent_memory/infrastructure/mdbx/MdbxPersistentLexicalCandidateAdapter.cpp) BM25/window, numeric controls and legacy TF behavior | Keep scorer, typed identities and deterministic ordering; do not use `generate()`'s pre-admission final-limit cap |
| EXTEND | [`MdbxPersistentLexicalIndex::read_snapshot()`](../src/agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.cpp) opens its own transaction, reconstructs flat state and returns an opaque pin | Add a narrow optional-MDBX read under the operation-owned transaction; reuse decoder, not a cast of `read_transaction_owner` |
| EXTEND | Adapter `generate_impl()` obtains a local snapshot; the response retains only frontier values, not its pin | Score the supplied coherent lexical view without silently opening another snapshot; apply approved eligibility before candidate creation |
| REUSE / EXTEND | [`MdbxSnapshotRevisionBoundRetriever`](../src/agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.cpp) privately has `admit_in_transaction()`; public `admit_candidate()` opens a fresh transaction each time | Reuse admission checks in a batch under the query transaction, retaining full persistent observations |
| REUSE | [`MdbxKnowledgeUnitStore`](../src/agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp), [`MdbxProjectionStore`](../src/agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp), [`MdbxResourceManifestStorage`](../src/agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp) already expose typed transaction reads | Join these reads, not independent `find()` calls; verify context/namespace provenance |
| EXTEND | [`MdbxKb0RetrievalFacade`](../src/agent_memory/infrastructure/mdbx/MdbxKb0RetrievalFacade.cpp) uses the legacy rebuilt producer and snapshot final admission | Prefer minimal opt-in composition here; no parallel facade or replacement engine |
| EXTEND | [`make_revision_bound_retrieval_trace()`](../src/agent_memory/retrieval/RevisionBoundRetrievalTrace.cpp) copies every response identity, including stale observations | Authorize/redact before ordinary trace construction; existing diagnostic observations are not policy-safe product hits |
| REUSE / EXTEND | [`MdbxAuthoritativeArticleHydrator`](../src/agent_memory/infrastructure/mdbx/MdbxAuthoritativeArticleHydration.cpp) already proves exact Markdown bytes, but uses its own initial/final metadata snapshots | Reuse typed artifact join, catalog and byte verification; reconcile its frontier with the query, not merely append `hydrate()` |
| NEW, pending | No approved revision-qualified metadata mapping or implemented application access context in this route | Request owner-approved evidence only; no speculative authority implementation |

`admit_persistent_lexical_candidates()` calls the generic
`RevisionBoundLexicalRetriever` and separately rereads projections. It checks
resource generation and full projection version, but does not share the lexical
transaction. The snapshot retriever's public candidate seam accepts only the
reduced unit/revision/kind/score identity: simply substituting it would lose
those additional persistent-observation checks.

## Target flow and result boundary

```text
Query + approved immutable host policy context + total work budget
  -> validate supported predicates; pin one agreed query read frontier
  -> establish strict scope/access/metadata eligibility at that frontier
  -> persistent BM25 over that eligible universe, bounded ranked window
  -> canonical unit/projection/manifest + full persistent-observation checks
  -> exact source opening + access/revision/provenance revalidation
  -> verified bytes; canonical-ID deduplication; deterministic final K
  -> redacted trace + honest completeness/materialization
```

This is a semantic flow, not new classes. The execution owner requires the
strict frontier **before candidate/hit creation**. Physical posting visits may
precede per-row eligibility checks inside the trusted backend, but cannot expose
an unauthorized candidate envelope or apply a small unrestricted top-N and call
post-filtering equivalent. Metadata pushdown may use a conservative superset
only under the existing exact-validation and conservative-completeness rules.

Final K follows every mandatory admission check and canonical-ID deduplication.
Rejecting the best-scored unit must leave the next eligible unit selectable.
If source bytes are required, hydration failure cannot consume a returned-hit
slot as if it were a hydrated success. A bounded refill policy is needed when
the available window is insufficient; no continuation mechanism is approved here.

The three existing axes remain separate: `Current` is neither authorized nor
`Hydrated`; `SummaryOnly` is a declared intermediate/diagnostic outcome, not a
source-backed product hit. Legacy `Complete` means the bounded operation ran;
it is not a certificate of exhaustive admitted top-K. Do not reinterpret legacy
enums or counters (`admitted_count` counts freshness). The product entry point
needs an approved mapping to the existing query/route completion vocabulary;
if the current DTO cannot express a safe outcome, that is a blocker, not a
reason to return a successful empty response. No new status enum is proposed.

Eligibility changes candidate membership, not the BM25 statistic population:
retain the foundation's scope-local active-snapshot N/DF/length statistics.
Recomputing them for each filtered subset would be a separate scoring decision.
The access owner must also approve disclosure of resulting scores/aggregates;
scope-local statistics are not, by themselves, permission to expose them.

## Canonical-owner decisions

All three decisions are **OPEN**. Approval must name the owner contract, evidence
source and acceptance cases below before production implementation starts.

### M — Revision-qualified metadata authority

[`MetadataFilter`](../src/agent_memory/domain/MetadataFilter.hpp) already means
exact string equality and AND across **all** predicates; its matcher rejects
a missing key. Do not invent a comparison language. `KnowledgeUnitEnvelope`
has no metadata map. Which existing canonical record/version supplies each
supported key, and which typed reader can observe it at the query frontier?
Confirm case/normalization behavior without silently changing exact equality.

Distinguish supported-but-absent key (predicate does not match) from unsupported
key/type/operator (`UnsupportedQuery`) and unavailable/stale authority (explicit
non-success, not an empty match set). Until approved, retain `UnsupportedQuery`
for metadata-filtered queries. Neither copied posting metadata nor a mutable
external map becomes authority. Acceptance: A07–A08.

### A — Application-owned access admission

Refine the existing **planned**, not implemented, `AccessPolicy` /
`RetrievalAccessContext`; `RetrievalQuery` and `ScopeId` alone carry no principal
proof. Which immutable host context and policy identity/revision are consumed,
where does authoritative allow/deny evidence come from, and how is it pinned
to M/F? Specify per-record deny, missing/stale/error and authority-budget outcomes.
No ambient principal, scope-as-ACL inference or implicit allow is acceptable.

Decide whether a policy change after pinning preserves an explicitly permitted
pinned decision or requires revocation revalidation/abort; never combine newer
policy evidence with old ranking while claiming one frontier. An external policy
service needs an approved version/binding protocol or this route is unavailable.
Ordinary trace/error outputs must not contain denied IDs, text, locators or
sensitive backend diagnostics; aggregate counts also follow the policy owner.
Acceptance: A09–A11, A14, A16.

### F — One query frontier, publication and source-byte lifetime

Approve one short, operation-owned MDBX read transaction for lexical descriptor,
active epoch/statistics generation/publication sequence, units, projections,
manifests and M/A evidence in the **same context and namespace**. A shared
connection/path, admission-only snapshot or copied frontier is insufficient.
Confirm that the selected publication producer supplies coherent membership;
a snapshot alone cannot repair independently committed incomplete publication.
Collection publication sequence is not one resource's generation. Mixed or
missing evidence cannot become strict `Exact` or exhaustive product `Complete`.

For the first bounded source profile, reuse the existing
[`ResourceRevisionRef` artifact join](../src/agent_memory/infrastructure/mdbx/MdbxResourceArtifactBindingStore.hpp)
and [`catalog transaction reader`](../src/agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp).
No new resource-to-document map is needed for original Markdown. Other source
profiles remain unsupported until approved; URI/path similarity is not a join.

The article hydrator reads metadata, verifies bounded CAS bytes, then rechecks
metadata in a **fresh** snapshot, its own linearization point. Approve either
bounded reuse of the query snapshot/evidence or a revision-qualified
revalidation protocol that reconciles the whole query, including M/A and
membership/completeness. Dropping changed hits at a later snapshot alone does
not establish coherent ranking or fill K. Keep the existing standalone hydration
contract unchanged. CAS is a separate durability domain; never claim cross-domain
ACID. Missing, corrupt, changed or unverifiable bytes yield no hydrated hit.
Acceptance: A03–A06, A12–A16, A18.

## Minimal proposed API delta, not approved signatures

| Boundary | Smallest request and invariant |
| --- | --- |
| Optional MDBX lexical reader | Read the existing payload with a supplied operation-owned read transaction; validate connection provenance and descriptor, and reuse the existing decoder |
| Candidate adapter | Consume that lexical view plus approved eligibility evidence; preserve complete persistent resource/projection observations, work counts and truncation |
| Existing snapshot admission | Batch under the same transaction with full `ProjectionVersionRef`, resource generation and canonical checks; not repeated `admit_candidate()` calls |
| Existing facade | Explicit opt-in composition with approved M/A/F and bounded runtime context; keep existing entry points/TF/defaults and dependency-free core unchanged |
| Existing Markdown hydrator | Reuse metadata-validation/byte-verification logic under F's approved binding/lifetime rule; never manufacture `Hydrated` by changing an enum |

No signature is frozen by this table. Prefer internal/optional MDBX overloads;
do not introduce generic `ITransaction`, a public executor framework or another
coordinator. Confirm result/completion and installed-consumer compatibility
before selecting the facade entry-point shape.

The operation owns the transaction on its creating thread and holds context,
stores, tokenizer and borrowed evidence alive until reads finish. Readers,
cursors, views and the opaque pin cannot escape or cross threads. Returned
values/bytes must own their storage. An attached connection is host-owned:
no shutdown during in-flight work and no inferred liveness from `shared_ptr`.

Use the execution owner's runtime cancellation/deadline contract, not mutable
fields in the value query. Bound reconstruction (payload/active-state limits),
posting visits, candidate/authority reads, source opens, decoded/verified bytes,
refill rounds and total time/work together. Repeated terms consume actual work.
Budget/cancellation must release the transaction and return the approved explicit
non-complete outcome. No unbounded external call while holding an MDBX snapshot.

## Fail-closed acceptance matrix

`E1`–`E6` below are **existing component checks**, not new product-path proofs.
**SPEC** means a required future executable scenario, not a passing test.
The first PR adds scenarios only; unsupported authorities may later use explicit
test doubles to exercise composition, never a production policy or a duplicate
reference engine. Existing component coverage does not close a SPEC gate.

| Case | Fixture / intentional counterexample | Required observation | Existing evidence / product gap |
| --- | --- | --- | --- |
| A01 BM25 | Fixed token stream, repeated terms, unequal lengths, equal scores; filtered membership | Frozen numeric goldens within `1e-6`; exact-index parity within `1e-5`; stable canonical-ID order; filtering does not silently change scope-local statistics | E1 unfiltered subset; full/filtered product replay SPEC |
| A02 Empty/no-match | Empty text, zero limit, tokenless text; absent term in a valid query | Preserve declared unsupported/zero-limit semantics; genuine no-match is distinct from failed admission | E1/E2 have different entry-point edge semantics; unified product mapping SPEC |
| A03 Freshness | Replace/supersede projection or advance unit revision after generation | No stale text/bytes; full projection version, not just unit revision, checked at F | E1/E3; common generation/admission frontier SPEC |
| A04 Delete | Canonical unit erased/tombstoned while lexical row remains | No current product hit, no resurrection after reopen | E1/E2; product/reopen SPEC |
| A05 Source revision | Same URI with different resource generation/digest; stale artifact join | Reject substitution; no newer or similar source bytes | E1/E4/E5; shared query F SPEC |
| A06 Manifest/scope | Missing/inactive manifest or canonical scope differs from query | No admitted product hit; failure not silently relabelled a no-match | E1/E2/E3; policy-safe product outcome SPEC |
| A07 Metadata AND | Two predicates; one wrong or missing, then both matching | Only all exact conjuncts pass; missing supported key never ignored | Current route unsupported; authority-qualified positive/negative SPEC (M) |
| A08 Metadata evidence | Matching copied/stale metadata; unsupported predicate; authority unavailable | No allow from copied/stale evidence; explicit unsupported/failure instead of successful empty | E1/E2 unsupported subset; M/F integration SPEC |
| A09 Denied leader | Highest BM25 scorer denied, next scorer allowed, K=1 | Leader never becomes visible candidate/hit; next eligible verified unit wins without early-K loss | SPEC (A/F); E1's stale/scope rejection is not ACL proof |
| A10 Missing/error policy | Missing/stale allow, callback failure, authority budget exhausted | Fail closed, explicit non-success/incompleteness, never infer allow from scope | SPEC (A) |
| A11 Policy change | Allow evidence changes before return | Approved pin/revocation rule observed; no mixed-policy hit or trace leak | SPEC (A/F) |
| A12 Truncation | Cap candidates, postings or terms so better eligible matches may be omitted | No exhaustive `Complete`/`Exact`; mapped failure retained through admission | E1 candidate/posting subset; final product mapping SPEC |
| A13 Dedup/refill | Duplicate canonical ID or conflicting revision; leader rejected; insufficient window | One logical hit after checks; conflict rejected; bounded refill or explicit incomplete result | E1 duplicate rejection/window subset; product dedup/refill SPEC |
| A14 Mixed publication | Publish replacement between generation/admission/source open; interrupt membership publication | One approved coherent observation or explicit failure; no "snapshot-consistent" claim from admission alone | E3 final-admission race only; E4 hydration race only; whole-query SPEC |
| A15 Hydration | Current preview; missing/corrupt CAS, wrong quote/range/UTF-8 or verification cap | `SummaryOnly` never promoted; only authorized exact verified bytes count as hydrated hits | E4/E5/E6 bounded Markdown; authorized query-frontier integration SPEC |
| A16 Ordinary trace | Denied/stale protected leader and backend error containing sentinel ID/path/text | No protected IDs, text, locators or sensitive diagnostics in any ordinary result/trace/error channel | SPEC (A); existing trace is not redaction proof |
| A17 Work/lifetime | Exhaust each budget, cancel mid-read/refill, attempt foreign-context evidence | No hidden scan/refill or leaked pin; explicit outcome and no completeness upgrade | E1/E2 budget subset; cancellation/provenance/product envelope SPEC |
| A18 Reopen | Update/delete then close all handles; reopen same process, then distinct writer/reader processes | IDs/scores/order and eligibility/source bindings preserved; fail-closed faults still rejected | E1/E6 same-process subsets; full product fresh-process SPEC |

### Existing executable checks and their limits

CTest names below are registered in [`tests/CMakeLists.txt`](../tests/CMakeLists.txt).
Source inspection at the audited baseline establishes their assertions; the
accepted [integration report](https://github.com/LimiNode/agent-memory-cpp/pull/687#issuecomment-7084894114)
and its final-main checks are historical component evidence, not a new product
qualification run or a source rebind to this documentation commit.

| Key | CTest name / fixture | Evidence boundary |
| --- | --- | --- |
| E1 | `agent_memory_mdbx_persistent_lexical_candidate_adapter_test` ([source](../tests/infrastructure/mdbx/mdbx_persistent_lexical_candidate_adapter_test.cpp)) | Actual persisted BM25; independent score literals; canonical admission uses test-double stores; same-process lexical reopen |
| E2 | `agent_memory_revision_bound_retrieval_test` ([source](../tests/retrieval/revision_bound_retrieval_test.cpp)) | Generic admission/status/budget/unsupported checks, not MDBX snapshot or authorization |
| E3 | `agent_memory_mdbx_snapshot_revision_bound_retriever_test` ([source](../tests/infrastructure/mdbx/mdbx_snapshot_revision_bound_retriever_test.cpp)) | Real MDBX final admission/facade trace races; legacy rebuilt ranking, no persistent-generation frontier |
| E4 | `agent_memory_mdbx_authoritative_article_hydration_test` ([source](../tests/infrastructure/mdbx/mdbx_authoritative_article_hydration_test.cpp)) | Typed join, CAS/quote/span verification and final metadata races, not application access admission |
| E5 | `agent_memory_mdbx_verified_artifact_hydration_test` ([source](../tests/infrastructure/mdbx/mdbx_verified_artifact_hydration_test.cpp)) | Artifact digest/range/missing/recovery, not query membership |
| E6 | `agent_memory_mdbx_raw_markdown_product_ingestion_test` ([source](../tests/infrastructure/mdbx/mdbx_raw_markdown_product_ingestion_test.cpp)) | Existing import/rebuilt-candidate/byte-hydration composition and same-process reopen, not persistent BM25 or ACL |

The separate `agent_memory_flat_markdown_consumer` driver already exercises
fresh processes for a canonical/rebuilt-lexical path; it is not A18 evidence for
the proposed persistent BM25 route. E1's next-current-hit selection is a local
test loop, not a product final-K/refill implementation. Neither is promoted by
this proposal.

After normal MDBX-enabled test configuration from [Build and test](build-and-test.md),
build E1–E6 targets and run the focused set (PowerShell or POSIX shell):

```text
ctest --test-dir <new-task-build> -C Release --output-on-failure -R "^agent_memory_(revision_bound_retrieval|mdbx_(persistent_lexical_candidate_adapter|snapshot_revision_bound_retriever|authoritative_article_hydration|verified_artifact_hydration|raw_markdown_product_ingestion))_test$"
```

Do not execute SPEC rows through a fake engine and label them product evidence.
After owner approval, bind each to the real opt-in composition and use a small
independently enumerated eligible-set/numeric oracle plus negative mutations.

## Next slices and separate flat-reconstruction proposal

1. Review M/A/F and this matrix; record approved semantics in their existing
   owners. Keep this PR Draft until independently reviewed; do not merge without
   a separate decision. Do not interrupt AMCE/Conversation #690.
2. Only after approval, implement the minimal opt-in composition and actual
   A01–A18 checks. Preserve legacy entry points/defaults and installed C++17
   compatibility; no speculative production authority while decisions are open.
3. Qualify the whole query, not just scoring: GNU/MSVC, Linux MDBX, applicable
   MDBX ON/OFF and installed consumers, update/delete, same/fresh-process reopen,
   publication races, correctness/completeness and source hydration. New latency,
   allocation/memory and source-open measurements must bind source/binary/input
   identities; synthetic fixtures do not establish representative quality.

Separate performance proposal: first check selective traversal of the existing
`flat-v2` payload without new persisted bytes or a duplicate decoder. The value
contains dictionary and unit inputs, not independent token posting rows; a DBI
cursor/range API alone cannot make it posting-selective. Retain full reconstruction
as the baseline; assess selective decoding versus generation-qualified reuse
only with exact pin/invalidation/lifetime semantics and whole-call costs. If no
bounded layout-preserving option is viable, report that result before proposing
a physical-layout/migration review. No WAND/BMW, BM25F or new algorithm here.

Completion is reported separately: component subset **implemented/integrated**;
target product path **not implemented/integrated/correctness-qualified**;
representative quality/performance **not qualified**; M/A/F **owner-blocked**;
release eligibility **not established**. Historical importer incident
[#691](https://github.com/LimiNode/agent-memory-cpp/issues/691) stays open with
unknown root cause; no speculative importer fix or untargeted retest batch.
