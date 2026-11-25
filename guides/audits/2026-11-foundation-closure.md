# Foundation Closure Gate before durable Phase 4

## Accepted integration addendum (2026-11-25)

The owner has accepted the P4-0 design and authorized qualified #700/#699
integration followed by P4-1 in a separate Draft. The original inspected-source
receipt below remains historical, including its then-current HOLD conditions.
#700 is now integrated at `main@7a9be667b4f5657c1c50590dde6b0a4888acdd4b`, tree
`32fbfcf25ab070735aad9203f82b5e4da7447d88`. Final #700 `f7de2469` passed GNU/MSVC
64/64 tests each, Linux GNU15.2 Debug ASan/UBSan8/8 targeted codec/copy/store/import
controls, CI45528005943 and Packaging45528005671 (6/6). No wire, preimage or
golden changes. #699 is integrated at
`4418a58c41d6c4e8f42d311708f849e00e40e59f` (qualified head `3b6936b5`,
tree `7dc0bcb5e7cbb4c3a1c5b6a0aa52c0626680244f`); exact-head
CI45543670619/Packaging45543670719 and post-main
CI45544579308/Packaging45544580025 succeeded. See the [integration delta](2026-11-foundation-amxr-integration-delta.json)
and [verifier](verify-foundation-amxr-delta.py); historical ledgers/catalogues
are unchanged. Durable runtime was NOT IMPLEMENTED at that integration gate.
The subsequent [private P4-1 vertical](../experiments/2026-11-25-conversation-durable-p4-1.md)
is implemented under review, not a complete installed store or schema deployment.
Other byte API and numeric product work
remain open. The [release progress register](../markdown-technical-preview-release-candidate.md#current-progress-register-2026-11-25)
separates profiles, prior merged HOLDs and current qualification gaps without
expanding release scope. No release, migration, deployment or P4-1 merge is
authorized by this addendum.

- Date: 2026-11-24. Owner: storage/identity/conversation contracts.
- Scope: P4-0 review, not P4-1 authorization or product migration.
- Inspected main: `77a278bef338074227efa05365e83e967334f429`.
- Main tree: `00cbb85105cb57a5a455dbb0e380238edef74cfa`.
- Starting P4-0: Draft #699, `cd24c92fd894771f98a10bfac64eac71bc88cc5a`.
- Gate recommendation: **READY for owner P4-0 design review; P4-1 HOLD pending
  owner authorization and corrective integration**. Independent exact-head and
  CI receipts are attached to #699/#700, not implied by this recommendation.
  The physical proposal remains Draft, NOT DEPLOYED.

## 1. Findings and bounded corrections

The integrated Conversation foundation is not being rewritten. The confirmed
issues are mismatched proposed policy and incomplete API integration, not
evidence of corrupted persisted Conversation data. There is no deployed durable
Conversation schema to migrate. The [physical owner](../conversation-durable-physical-contract.md)
is refined in this PR, not duplicated here.

| Finding at the source/starting draft | Disposition before P4-1 |
| --- | --- |
| AMXR returns arbitrary bytes through string; AMCE and source_key stage those bytes through it | Separate corrective Draft: additive `_bytes` API using existing ByteBuffer/ByteView; preserve legacy functions, wire/digest/goldens. Source-key ownership moves directly into ByteBuffer |
| One namespace/profile, yet repeated namespace in every physical key | Select profile-qualified compact B layout, full logical refs retained in values, digest bound to namespace/incarnation; A/C comparison and open/attach/backup checks in physical owner |
| Durable totals inherit in-memory 65536 rows / 256 MiB ceilings | Replace with explicit durable u64 quotas, distinct immutable codec/bucket profile and bounded work/transaction/live-buffer policy. No reference validator weakening |
| Draft allocates UINT64_MAX, importer reserves it | Align durable next-entry sentinel with integrated importer; no runtime change or extra exhaustion flag |
| Bucket scan ceiling alone could let later admission strand an earlier mapping | Check post-insert aggregate row AND encoded value-byte bounds in the same transaction; immutable admission descriptor, full replay profile covers admitted buckets |
| Physical wire specification omitted C++ buffer ownership | New P4 key/value/witness APIs use ByteBuffer/ByteView and fixed arrays; named legacy infrastructure adapters only |
| An import-only vertical could falsely advertise all IConversationStore methods | Internal qualification backend until honest conformance, or bounded conformant full baseline; no fake NotFound/Complete/empty Current |

No fundamental binary-string UB finding was established. String supports
embedded NUL/FF; the issue is semantic API boundaries and redundant conversions.
No proof was found that all existing byte APIs or numeric physical stores are
already product defaults. Deferred refactors are explicit, not declared done.

## 2. Source inventory and reproducibility

The [binary inventory](2026-11-foundation-binary-api-inventory.md) supplies exact
file/symbol families, format owners, REPLACE/ADAPT/RETAIN/DEFER and next stages.
The [full source ledger](2026-11-foundation-source-inventory.json) covers 286
tracked production files, 209 string-bearing files and 2061 lexical sites.
The [current AST catalogue](2026-11-foundation-public-type-catalogue.json)
reuses the existing public catalogue extractor: 155 installed headers, 637
named declarations, 333 identity-name candidates, 75 public raw-string candidate
members. Names are candidates, not inferred entity semantics. Private/nested
types are labelled; optional MDBX declarations are included in the AST profile.
Historical `acd5ad55` catalogue and `a27e0c` delta remain unchanged.

```powershell
py -3 guides/audits/extract-foundation-closure.py --verify
# Regeneration requires Clang JSON AST plus C++17 standard headers:
py -3 guides/audits/extract-foundation-closure.py --clang <clang++> --mingw-root <mingw64>
```

The source ledger is generated from Git objects, not an unlabelled dirty tree.
The AST verifier checks every current source header against the source commit,
declaration hashes/offsets/counts and owner paths. It does not prove runtime
behavior, complete identity semantics, every macro profile or ABI compatibility.
Semantic decisions below and in the existing identity owner remain necessary.

## 3. Identity closure register at main

The [identity policy](../identity-policy.md) and its historical
[per-identity register](2026-11-domain-identity-public-api-audit.md#per-identity-decision-register)
remain owners. This is a dated status addendum, not a replacement catalogue or
a new universal allocator. All prior families remain accounted for here.

| Family | Category and actual state | Boundary retained / remaining work |
| --- | --- | --- |
| ExternalIdentityScalar/component/key, ExternalObjectRef | Portable external; IMPLEMENTED, AMXR integrated | Exact signed/unsigned/UTF-8/composite tuple, issuer namespace mandatory; no implicit legacy-ID/provider coercion |
| ConversationId, EntryId, ActorId + ConversationRef/EntryRef/ActorRef, EventRevision | Local allocated occurrence/revision, namespace-qualified; IMPLEMENTED values and in-memory import/store after #698 | Entry allocator 1..MAX-1; constructor does not allocate. Actor issuer remains host-owned. Durable occurrence allocator SPECIFIED, not deployed |
| KnowledgeUnitId | Local allocated nonzero u64; IMPLEMENTED | Product legacy store still serializes primary physical row ID as decimal; numeric typed store opt-in |
| TokenId, SectionId | Nonportable dictionary/resource-local numeric identity; IMPLEMENTED | Keep dictionary epoch/resource. Dictionary token text is a lookup key, not a global ID |
| DocumentStorageId, BlockStorageId | Backend-local physical u64; IMPLEMENTED opt-in | Separate monotonic sequences, reverse binding/no-reuse; lookup does not allocate; not source IDs |
| ResourceId, DocumentId, ChunkId, ContentBlockId | Logical opaque string identity; IMPLEMENTED/COMPATIBILITY_RETAINED | Preserve resource/document/revision scope and stable block identity; physical mapping must not rewrite portable IDs |
| SourceRevisionId, ArtifactBindingId | Portable source snapshot / immutable relationship; IMPLEMENTED compatibility | Preserve issuer, exact source/artifact/role; no local-counter substitution |
| ArtifactId | Content-addressed `sha256:` hex string; IMPLEMENTED | Text contract retained; algorithm-qualified digest arrays are already binary; binary lookup optimization independent |
| BlobDigest, ResourceBodyDigest, ContentHash, storage::CanonicalFlatDigest | Content-addressed binary arrays with algorithm/recipe; IMPLEMENTED | No occurrence allocation; keep digest preimages/recipe v0/v1/v2, collision verification and equality context |
| KnowledgeUnitKey, LexicalSegmentPhysicalKey | Typed composite content/physical identity; IMPLEMENTED | Scope/kind/hash or epoch/partition/segment widths and comparator retained |
| RepresentationId, SegmentSetId, SegmentId, EvidenceAnchorId | Logical derived identities; IMPLEMENTED values/subsets | Keep representation/source/revision/recipe qualification, no universal numeric conversion |
| MessageId, ThreadId, AuthorId | Legacy external/logical strings; COMPATIBILITY_RETAINED | Incomplete issuer context must not be guessed; new Conversation/AMXR models are additive, no mass migration |
| FactId, MemoryObjectId | Legacy logical APIs; COMPATIBILITY_RETAINED | Generic store/allocator not completed; do not create a competing source of truth |
| ScopeId, workspace/profile/publication IDs, schema/model/recipe/index/version labels | Authorization/descriptor/logical labels, not one global occurrence domain; RETAIN | Host admission remains separate; scopes/cursors are not capabilities |
| MetadataFilter.key, PromptTemplateBinding.key | Non-entity lookup keys; RETAIN text | No new allocator/AMXR conversion |
| DerivedRecordRef.key and publication guard key/value/token | Opaque physical/receipt bindings; ADAPT legacy exact bytes | May contain NUL/non-UTF-8; not normalized text, not new logical IDs |
| Table/row/column/cell IDs; transcript speaker/video IDs | Representation-local composite / externally scoped legacy labels; RETAIN | Existing typed locator/source context; portable adapter remains separate |
| Evaluation dataset/query/item labels and hex hashes | Non-entity interchange/fixture descriptors; RETAIN | Do not regenerate logical relevance fixtures merely because physical keys change |

This covers the historical raw-string exception families: descriptors/receipts,
nonentity lookup, opaque guard keys, representation-local table keys, media
external labels, evaluation labels and flat logical IDs. The new 75th public
raw-string candidate, ConversationSourceBinding::provider_revision_marker, is
an explicit opaque source marker, not a reason to turn all strings into numbers.
Consult exact AST anchors and its source owner. Hash/UUID-shaped
text is not parsed without its declared scheme. Binary digest and composite
physical representations already exist; no library-wide UUID entity is added.

### Default physical path versus opt-in integration

| Actual implementation | Physical behavior / current integration |
| --- | --- |
| [MdbxKnowledgeUnitStore.cpp](../../src/agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.cpp), Impl::id_key | `std::to_string(id.value())`; legacy primary table/string indexes still used by established product facades. NOT numeric migration |
| [MdbxKnowledgeUnitNumericKeyPilot.cpp](../../src/agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitNumericKeyPilot.cpp) | `KeyValueTable<uint64_t,string>` primary + used ledger; explicit pilot, opaque payload; not general unit product store |
| [MdbxKnowledgeUnitTypedStore.cpp](../../src/agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitTypedStore.cpp) | Numeric primary/used-ID tables, BE content/kind keys, typed validation; opt-in full unit subset. Decimal metadata next values remain textual values, NOT decimal primary keys |
| [MdbxStorageIdentityMapping.cpp](../../src/agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.cpp) | Exact logical forward map and numeric document/block reverse keys, independent sequences, retirement/no-reuse, transaction provenance; opt-in. Metadata counter text is explicit legacy representation |
| [MdbxCanonicalFlatStorage.cpp](../../src/agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.cpp) | v2 BE64x2 16-byte composite physical key, numeric heads, key/value integrity; opt-in shared identity/context. Earlier decimal-composite flat layout is historical/reject-old, not current v2 |

Existing [19-process numeric reopen gate](../experiments/2026-11-20-numeric-storage-reopen-gate.md)
qualified a narrow POST_NUMERIC_REJECT_OLD profile. Its evidence explicitly says
`migration_executed=false`, `post_migration_upgrade_proof=false`. This audit does
not rerun or relabel it as a product migration. Existing default C1/document
paths and numeric flat alternatives coexist intentionally; profile selection
and physical-schema rejection must be explicit.

### Concrete numeric product closure sequence (independent of P4-1)

1. Product wiring PR: explicit versioned profile selection at the existing
   unit/canonical product construction boundary; reuse typed unit store, mapping,
   flat storage and ONE MdbxStorageContext/transaction owner. No new universal
   store. Preserve IKnowledgeUnitStore behavior, provenance/current-frontier
   guards and installed/federated readers. Legacy remains explicit, not silently
   reinterpreted; DBI composition/peak reserve checked before open.
2. Owner chooses reject-old + fresh rebuild OR separately authorized migration.
   Retain real OLD fixtures/binary as required by that choice; copied logical
   data/new schema creation alone is NOT upgrade evidence. No migration runs in
   this assignment. Define backup/rollback/fencing/temporary map+DBI capacity.
3. Qualify numeric 2/10 order and 0/MAX/exhaustion; primary/content/kind maps,
   retirement/no-reuse, foreign transactions, rollback/partial publication,
   correction/current frontier, process reopen, wrong flags/schema, old rejection
   without mutation. Full decoded logical parity independently of raw snapshots.
4. Run installed and relocated product consumers, exact-generation upgrade and
   preview/retrieval consumers against the selected exact NEW source/dependency
   tree. Regenerate ONLY physical snapshot receipts; keep logical datasets/hashes.
   Record skipped lanes honestly, including post-migration proof if not run.
5. Review matched latency/allocations/RSS/page/write-amplification and the explicit
   default activation decision. Pilots remain opt-in until owner accepts this
   product evidence. Durable Conversation does not inherit decimal primary keys
   while waiting for this independent integration.

## 4. Reference/durable semantic parity

Actual semantic sources:
[store](../../src/agent_memory/storage/ConversationStore.cpp),
[importer](../../src/agent_memory/ingestion/ConversationSourceImporter.cpp),
[revision tests](../../tests/storage/conversation_store_revision_test.cpp),
[import tests](../../tests/ingestion/conversation_source_import_test.cpp),
[OOM tests](../../tests/ingestion/conversation_import_failure_test.cpp),
[external-copy owner](2026-11-24-external-identity-copy.md).

| Invariant | Reference observation / durable requirement |
| --- | --- |
| CAS before NoChange | correct checks expected revision before byte-equality no-op; durable preserves that order |
| Replay before new capacity | full source-key/witness lookup before new-entry checks; replay at quota returns original ref/generation |
| Original witness | same_witness includes producer, raw flags, source-shaped target, effective request/presence; persist losslessly, not only AMCE |
| Generation | one applied create/import/correction advances; refusal/no-op does not. Correction no-op reports current, replay original admission generation |
| Frozen unresolved target | late call does not rewrite original unresolved result; a new resolving observation is separate |
| Corrections/exact/history/Current | immutable revisions+receipt; exact old targets never replaced with latest, tombstone/edit ambiguity and explicit Restore unchanged |
| Pinned frontier | later corrections/appends invisible to existing pinned read; bounded fold must consume complete required input or refuse |
| Continuation | instance-bound public token; recreated store rejects it, no transaction/view escapes. Durable portable selector is later API review |
| No-reuse/exhaustion | importer rejects next_entry==MAX. Durable reserves MAX explicitly, aborted tentative IDs reusable but committed IDs never rebound |
| OOM | named external-ref/source/entry copies plus prepare-before-publication; no variant-copy workaround duplicated. Durable prepares owning response before commit |

Static parity is a design obligation, NOT execution proof for an unimplemented
adapter. P4-2/P4-3 run deterministic crash/race/CAS/reopen/frontier checks; the
corrective AMXR PR runs existing in-memory mutation/OOM regressions now.

## 5. Historical architecture maturity and gate classification

Labels describe the named slice, never universal product completion. B1 = MUST
CLOSE BEFORE P4-1; B4 = MUST CLOSE BEFORE PHASE 4 ACCEPTANCE; PI = INDEPENDENT
PRODUCT INTEGRATION; LC = LATER CAPABILITY.

| Decision and owner/source | Maturity | Time and disposition |
| --- | --- | --- |
| Canonical blocks vs independently decodable physical frames vs retrieval chunks; [canonical owner](../canonical-content-storage-roadmap.md), CanonicalContent, FramedZstdCodec, StructuralSegmentSet | IMPLEMENTED subsets / SPECIFIED separation | B1 retain distinct identities/ownership; no AMCE whole-conversation blob or chunk-as-evidence substitution |
| Stable ContentBlockId/block-local locators; CanonicalContentStore, TypedRepresentation/TL1; [locator owner](../typed-representation-locators.md) | IMPLEMENTED edits/history/typed locator values; PARTIAL cross-profile integration | PI; no new block allocator or locator format needed for journal |
| Canonical text plus unit primary_text; MdbxCanonicalContentStore, MdbxKnowledgeUnitStore | PARTIAL product transition; duplicated projection text exists | PI: preserve source binding, measure canonical-once hydration before removing payloads; not a reason to alter AMCE |
| Numeric physical mapping / product migration; five adapters above, evidence register | IMPLEMENTED opt-in / MISSING default migration evidence | PI; compact P4 numeric keys B1, legacy product conversion is not a P4-1 blocker |
| Shared context/transaction provenance; MdbxStorageContext, mapping/flat stores; [backend owner](../storage-backend-integration-roadmap.md) | IMPLEMENTED opt-in | B1 REUSE; actual durable flags/attach validation B4, no extra core connection layer |
| Typed record codecs, numeric vs bytewise comparator, version markers; flat/AMSI/numeric stores | IMPLEMENTED subsets; P4 SPECIFIED | B1 specify ByteBuffer, BE keys/default comparator, unknown-schema refusal; B4 execute literals/wrong flags/integrity/reopen |
| Raw/Zstd bodies, bounded reads; FileRawBlobStore, FramedZstdCodec, verified hydration | IMPLEMENTED narrow optional profiles; PARTIAL byte API | PI for byte facade; B1 artifacts stay separate, no partial range promoted to verified whole-artifact identity |
| Collection membership/order/ledger choice; canonical ID set/ledger, ordered blocks, source collision slots | IMPLEMENTED specific collections, not one universal structure | B1 preserve ordered payload/tuples, unique ledger membership, append-only collision slots; B4 bucket tests, no blanket serialized-list redesign |
| Versioned storage profiles and limits | IMPLEMENTED existing subsets; P4 SPECIFIED | B1 compact profile/quota/allocator decisions; B4 runtime exact descriptor/attach/budget/memory qualification |
| Ontology-neutral arbitrary derived knowledge; [representation owner](../derived-representations-and-codecs.md), integrated corrective audit | SPECIFIED general contracts; PARTIAL fixed typed values | LC: reuse provenance and schema/version ownership, no forced binary ontology or universal JSON |
| Managed Memory Blocks / Keyed Memory Facade; [foundations audit](2026-11-23-memory-foundations-corrective-audit.md), CanonicalContent/PromptComposition/profile history | SPECIFIED facade / IMPLEMENTED primitives / MISSING end-to-end facade | LC: named access, trusted mutation and required-inclusion separate guarantees; reuse existing revisions, no second truth store |
| Epistemic/temporal/provenance; [lifecycle owner](../memory-lifecycle-governance-roadmap.md), [runtime owner](../agent-runtime-integration-roadmap.md), Conversation times/source bindings | IMPLEMENTED exact evidence/time fields; SPECIFIED bitemporal/perspective; MISSING full query/service | B1 no timestamp->causality or observation->success/endorsement inference. General epistemic/temporal services LC |
| Stateful associative activation / question-conditioned views; [activation owner](../knowledge-activation-roadmap.md) | RESEARCH, not qualified product | LC, NOT RUN here; no new index/background runtime |
| Full namespace physical keys / MAX exhausted flag / reference-total durable ceilings in initial #699 | SUPERSEDED proposal only | B1 replaced by compact B / reserve-MAX / explicit durable quotas; no persisted migration required |

## 6. Public surface and smallest remaining PR sequence

IConversationImportSink::history exposes IConversationStore, whose advertised
methods include correct and read_current. It has no generic unsupported-read
outcome. Therefore P4-1 is internal until all advertised semantics are honest,
or supplies a bounded full oracle-conformant baseline. The history view may be
const, but that does not excuse false interface conformance. No new parallel
public Conversation API, fake empty/NotFound/Complete or runtime stub is added.

Ordered sequence:

1. Separate additive AMXR corrective Draft; legacy/golden/digest and installed
   consumers qualified; independent review. No disk changes.
2. Revised documentation-only #699, this audit/identity addenda/manifest and
   physical key/budget/parity decisions; independent exact-head review, then
   OWNER decision on P4-0 and any corrective integration. No automatic merge.
3. Owner-authorized P4-1 minimal internal or conformant durable vertical;
   P4-2 crash/race/transaction and P4-3 correction/frontier/interface parity.
4. P4-4 measured Current strategy and full memory/performance/security evidence.
   Phase 4 acceptance only with honest installed public behavior and qualification.
5. Independent numeric product/other byte-API integrations and later capabilities
   above proceed separately, not hidden prerequisites to implementing all memory.

## 7. Qualification receipt and residual risks

The corrective Draft is [#700](https://github.com/LimiNode/agent-memory-cpp/pull/700),
HEAD `8bc8ab493c3722bc63d64d2a6089213ca4ee2fdb`, tree
`7f1e2002ab69dc688d9c056f8fa6bb380d32e917`. GNU 15.2 and MSVC 19.44 C++17
Release each passed 64/64 tests at that source; six installed consumers passed
on each. An independent read-only reviewer found no blockers and re-executed
AMXR/AMCE/failure controls on both toolchains. Fresh sanitizer runs are NOT RUN
locally. Optional/relocated/remote CI lanes are reported in the PR receipt,
never inferred from a baseline core build.

AST/ledger regeneration and independent-mode `--verify` passed; DBI manifest
and validator self-test passed; changed Markdown local-link/anchor checks and
`git diff --check` passed. An independent Python calculation verified the 26-byte
revision literal, 46-byte source-key width and numeric ordering at
1/2/10/255/256/UINT64_MAX. This is a byte-arithmetic control, not runtime codec
or MDBX evidence. Final documentation exact-head review/CI are attached to #699.
Historical qualification is not relabelled as a fresh run.
Durable codec, bucket atomicity, map growth, concurrent attach, disk fanout,
Current performance, RSS and power-loss tests remain **NOT RUN** because no
durable adapter is implemented. Compact key/durable quota correctness must be
executed, not inferred from this design. Other legacy binary string interfaces
and numeric product integration remain explicit residual work, not CLOSED.

No merge/release, database migration, DBI deployment or P4-1 implementation is
performed or authorized by this report.
