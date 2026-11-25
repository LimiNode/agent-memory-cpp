# Storage Product Integration: Phase 1 Audit

Status: source audit and implementation proposal, 2026-11-21. Owner: storage,
ingestion and retrieval integration. This is not a production activation or a
migration qualification. The proposed operation is in
[the product integration ADR](storage-product-integration-adr.md); migration is
separately scoped in [the migration plan](storage-numeric-migration-plan.md).

## Baseline and evidence boundary

The audited runtime is `main@acd5ad5545386b3feeaa79f8a587855b60801b4d`.
Its `src` tree is `87a70c0f850266e549458be40f05760febb1050f` and its `external`
tree is `b816ddeccafa0375c15b977e71d4a36f6728f59f`:

- libmdbx: `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`;
- mdbx-containers: `c2a2326273a3d85f29283e2cd34942c7b8d72cbd`.

The sixteen storage integration batch PRs are merged. They include documentation,
tests and research as well as runtime changes. The source-bound integration
ledger and final-main qualification are in [report PR #674](https://github.com/LimiNode/agent-memory-cpp/pull/674).
That report has its own review/merge boundary; this audit grants no merge authority.

Evidence categories used below:

- **IMPLEMENTED**: established from the named interfaces and implementation;
- **TESTED**: a named execution at a stated source, not inferred from source;
- **PROPOSED**: a new contract requiring review and implementation;
- **BLOCKED**: an unmet acceptance condition, not an assertion that no possible
  future composition can satisfy it.

The MCP graph was consulted first. Its root index omitted the integrated typed,
flat and KB-0 adapters, so current source was inspected directly. Graph absence
is not treated as evidence of missing implementation.

## Actual paths, not one assumed pipeline

```text
ObservedResource (original Markdown, scope, resource identity)
  -> MdbxRawMarkdownProductIngestion
       -> MdbxResourceImporter
            -> durable lexical document memory
            -> legacy MdbxKnowledgeUnitStore + Original projections
            -> active ResourceManifest, guarded by importer fencing
       -> File CAS + artifact catalog
       -> ResourceArtifactBinding
  -> MdbxSnapshotRevisionBoundRetriever
       -> bounded ProjectionAwareLexicalRetriever candidate production
       -> legacy unit/projection/manifest admission in one MDBX snapshot
  -> MdbxAuthoritativeArticleHydrator
       -> metadata observation -> verified CAS bytes -> final metadata observation

Separate opt-in route:
  Markdown fixture -> fixture parser -> MdbxCanonicalFlatStorage
       -> persistent document/block identity mapping + immutable revisions
  host materialization -> existing Raw Markdown product import/retrieval
       -> fixture-specific comparison (not an atomic canonical publication)

Separate compatible store, not injected into the first route:
  IKnowledgeUnitStore -> MdbxKnowledgeUnitTypedStore
```

`MdbxKb0RetrievalFacade` adds an observational trace around the snapshot
retriever. It does not hydrate flat blocks or change that retriever's stores.
`ResourceIndexer` is another, document/vector-oriented composition; it is not
the transaction owner of the Raw Markdown or flat routes above.

## Ownership and physical map

Paths in this table are relative to `src/agent_memory/`. They can be inspected at
the exact baseline above; they are not future class names.

| Stage / source | Physical state | Identity / revision owner | Transaction, authority and recovery boundary |
| --- | --- | --- | --- |
| `ingestion/ResourceImporter.hpp`: `ObservedResource` | Input DTO, no persistence | Host supplies explicit `ScopeId`, globally qualified resource identity and original UTF-8 | Credentials and authorization remain host-owned. A supplied scope is a claim, not authenticated authority. |
| `infrastructure/mdbx/MdbxResourceImporter.{hpp,cpp}` | Import intent/fencing records; durable lexical/document records; legacy raw units, projections and manifests | Resource generation, body digest and importer profile; raw KU occurrences have their own IDs/revisions | Several guarded transactions, not one canonical/index commit. Constructor recovery and retry use persisted intent, lease and fencing epoch. Pending resources are hidden by importer search. |
| `infrastructure/mdbx/MdbxRawMarkdownProductIngestion.{hpp,cpp}` | Above state plus catalog, CAS and resource/artifact join | Exact resource generation/digest, derived opaque source-revision ID and artifact binding | Importer -> catalog/CAS -> join -> final coherent metadata observation. `Published` means current at that observation, not forever, not full query coverage. Interrupted publication can be `Incomplete`/`Conflict`. |
| `infrastructure/mdbx/MdbxKnowledgeUnitStore.{hpp,cpp}` | Legacy KU DBI family and codec | KU ID, immutable content key, lifecycle and envelope revision | Used directly by the importer, snapshot retriever and article hydrator. Fenced raw publication is a profile-specific operation; not a typed-store selector. |
| `infrastructure/mdbx/MdbxKnowledgeUnitTypedStore.{hpp,cpp}` | `ku_typed_v1`, used-ID, metadata, content and kind indexes | Nonzero numeric KU allocation; used IDs survive erase; envelope bytes reuse the existing versioned codec | Each mutation owns one writer across all its indexes. `find` owns its own read transaction. No caller-transaction typed `find`/write API is exposed. Unknown/missing-on-nonempty schema fails closed. |
| `infrastructure/mdbx/MdbxStorageIdentityMapping.{hpp,cpp}` | Persistent forward/reverse mappings, allocation sequences and retired entries | Textual document/block identities -> separate physical numeric domains | Transaction overloads support checked composition. Retired mappings remain addressable for history; live snapshot replacement is rejected. Import into a fresh namespace is explicit. |
| `infrastructure/mdbx/MdbxCanonicalFlatStorage.{hpp,cpp}` | Numeric head, immutable structure and block-version DBIs plus identity mapping | Logical `DocumentId`/`ContentBlockId`; independent document and block revisions | `create_document`/`commit` check and publish in their own writer. Reads own a snapshot per operation. Expected-head conflict is checked inside the writer, not only an instance mutex. No public caller-transaction flat read/commit seam. |
| `infrastructure/mdbx/MdbxCanonicalContentStore.{hpp,cpp}` | Legacy C1 current/history and used-block state | Same dependency-free canonical content/edit contract, different physical profile | Local transaction atomicity and historical reads. Navigation budgets do not prohibit decoding an otherwise valid large C1 document. Does not automatically feed the importer. |
| `infrastructure/mdbx/MdbxProjectionStore.{hpp,cpp}` | Versioned, scope/unit/kind-keyed projection history | Unit-at-build, projection revision and derivation fingerprint | Has snapshot read overload. A stored projection is not itself active publication; manifest and unit admission are still required. |
| `retrieval/ProjectionAwareLexicalRetriever.{hpp,cpp}` | Bounded in-memory lexical query index built from persisted units/projections | Typed candidate KU/revision/kind paired with legacy hit | Accepts `IKnowledgeUnitStore`, so typed-store candidate production is composable. It is not the final same-snapshot numeric/flat authority. Budgets reject incomplete rankings rather than silently truncate work. |
| `infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.{hpp,cpp}` | No new canonical DBI; reads legacy units, projections and manifests | Candidate identity is a claim; current envelope/root is re-read | Final admission shares one read transaction. Ranking is not a snapshot-consistent top-K promise. Stale candidates are not refilled. Flat canonical head is not part of this read. |
| `infrastructure/mdbx/MdbxAuthoritativeArticleHydration.{hpp,cpp}` | Legacy unit/projection/manifest, typed resource/artifact join, catalog and File CAS | Exact source revision/digest, byte range and Original projection | Complete CAS digest verification is bounded separately from returned bytes. Final metadata revalidation is its linearization point; failed checks yield no hydrated bytes. Does not consult the flat head or typed numeric units. |
| `ingestion/ResourceIndexer.{hpp,cpp}` | Supplied document/manifest/owner stores plus vector index | Resource generation and derived-record ownership | Compensation, restoration and pending-reclaim logic over separate interface calls. Not a cross-backend transaction and not the selected product publication owner. |

[`MdbxStorageContext`](../src/agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp)
retains one connection; it neither automatically joins arbitrary adapter calls
into one transaction nor supplies authorization. Transactions/cursors are
thread-bound. Attached context lifetime and host shutdown authority are distinct.

## Source-confirmed integration gaps

### G1. Numeric candidate compatibility is not numeric final admission

The generic `ProjectionAwareLexicalRetriever` can receive the typed adapter.
However, `MdbxSnapshotRevisionBoundRetriever::retrieve_detailed` and
`admit_candidate` construct `MdbxKnowledgeUnitStore`, and the article hydrator
also owns that legacy store. Reusing a numeric ID in both families does not join
their namespaces. A typed-only unit is not made visible to the current product
facade by implementing `IKnowledgeUnitStore` alone.

### G2. Canonical and resource revisions are not the same authority

`SourceRefSummary` binds a resource revision/digest and an excerpt. It does not
carry a canonical document/block revision binding. `CanonicalDocumentRevision`
does not carry `ScopeId` as an enforced field. The flat editor does not update a
resource manifest or invalidate Original projections when its head advances.
Equal decimal revision numbers, shared paths or arbitrary metadata strings do
not establish the missing join, ownership or authorization.

### G3. Existing consumer proves a narrower scenario

[`flat_markdown_consumer.cpp`](../tests/infrastructure/mdbx/flat_markdown_consumer.cpp)
uses a real fixture, flat editing, real Raw Markdown retrieval and fresh-process
reopen. Its `visible` helper checks exact host metadata and rendered bytes. It
does not substitute a fake search implementation. Nevertheless:

- its parser is expressly one heading and two paragraphs, not general Markdown;
- Raw publication still writes the legacy KU family;
- canonical, raw publication and the final metadata-binding edit commit
  separately;
- it pins an explicit canonical revision, not a joint current-publication head;
- its arbitrary metadata joins are host checks, not a durable, enforced
  canonical publication contract or an authorization service;
- it cannot establish the complete interruption/multiwriter acceptance matrix
  requested for numeric product integration.

This useful integrated experiment is retained. It is not renamed as the new
numeric product qualification.

### G4. Snapshot composition and crash recovery need an operation owner

Flat and typed stores open their own transactions. Mapping/projection/manifest
read overloads exist, but no operation currently co-observes a flat head, typed
unit, authorization binding and published resource/projection tuple. No durable
intent currently owns **that entire tuple**. Importer/catalog intents own their
existing narrower profiles; they must not be reinterpreted as a new schema.

This is a current-main gap, not a claim that transaction/read participants have
never been implemented. The unmerged #549–#554 coordinator stack contains real
experimental participants and a pinned target read. Its exact-head
[differential reuse audit](experiments/2026-11-21-publication-stack-differential-audit.md)
maps those to main, records #555's legacy-writer gate and distinguishes
`TargetExact` from the stronger numeric/flat product contract. Evaluate that
implementation selectively before creating new seams; do not merge it whole.

## Currentness and failure matrix

The right column is the **required proposed product result**, not a claim that
current adapters already provide it. Body means any canonical/private payload,
including a preview or citation capable of exposing it.

| Condition | Existing evidence / limit | Proposed allowed observation |
| --- | --- | --- |
| Canonical commit before lexical publication | Separate commits today | `CommittedPendingPublication`; current retrieval rejects old tuple without body. Never report product `Published` yet. |
| Previous index generation | Existing admission compares legacy KU/root versions, not flat head | Old candidates may be ranked, but final same-snapshot flat/typed admission rejects them; no refill/top-K claim. |
| Process stops between publications | Importer/catalog have recovery, flat has its own atomic head | New operation's durable intent must resume or explicitly abandon; fresh-process queries fail closed until matching publication. |
| KU exists but canonical document does not | Possible independent state | No body; `Unavailable`/`Missing`, never URI reconstruction. |
| Document withdrawn but old candidate remains | No canonical document-delete API in the flat contract | Product publication withdrawal must be atomic with a tombstone/visibility change; reject old candidates. Do not pretend block deletion deletes a document. |
| Reader pinned before writer commits | Existing MDBX MVCC read snapshot | Old coherent result is allowed only at the declared old linearization point. A subsequent new read must see updated state or reject. |
| Concurrent writers with one expected revision | Flat writer checks head inside serialized transaction | Exactly one commit succeeds, other conflicts; no double-publish for one expected state. |
| Explicit historical request | Flat/C1 retain revision history | Return exact authorized historical revision with historical status; never substitute current data or label it `Current`. |
| Wrong / revoked scope | Current envelope scopes are equality boundaries, not credentials | Host-authorized scope plus exact stored scope checked at final admission; no body/preview across the boundary. |
| Missing mapping or schema marker | Integrated stores reject inconsistent state | Fail closed; no fallback to legacy DBIs or allocation of replacement IDs during read. |
| Corrupt digest / absent historical block | Integrity checks exist inside individual stores | Corrupt/missing with no body; do not rebuild evidence silently from a projection. |

## Phase 3 disposition

**BLOCKED for the requested full numeric product acceptance contract.** This is
not a failure of the existing storage primitives or a prohibition on an
independently reviewed weaker experiment. The smallest missing prerequisites
are:

1. An explicit scope/resource/canonical-revision/publication binding whose
   authority and persistence are reviewed, not inferred from metadata strings.
2. A narrow backend-owned same-snapshot read seam for typed units and flat
   head/block authority, composed with existing projection/manifest reads.
3. A coarse operation that atomically admits a canonical change and its durable
   publication intent, and later conditionally publishes the prepared tuple.
4. A reviewed version/DBI budget for any new binding/intent record. The current
   work order expressly forbids changing persisted schemas; no such change is
   made here.

The ADR compares minimal host composition with bounded coordinated publication,
lists the API/DTO deltas and the exact fifteen-step acceptance sequence. That
proposal must be reviewed before implementation. No replacement retrieval,
private DBI access disguised as an adapter, global coordinator framework or
unqualified success receipt is added to bypass these prerequisites.

## Bounded concurrency and reliability qualification

Source audit covered flat multi-instance head conflicts, identity-mapping
transaction provenance/lock ordering, typed-store allocation/index writes and
snapshot retriever read/write ordering:

- Flat's instance mutex is not its cross-instance correctness mechanism; the
  expected revision is checked in the serialized MDBX writer.
- Mapping transaction overloads validate connection/transaction provenance and
  do not acquire the owned-write instance lock while a caller holds its writer.
- Typed state/index mutations share one writer, but separate public reads are
  not a common cross-store snapshot. `scan_by_scope` visits kind scans; it does
  not become a same-snapshot publication query.
- Test hooks, optional non-owning metrics and adapter destruction/shutdown must
  not race ordinary calls. Their existence does not grant arbitrary thread
  safety or lifecycle guarantees.
- Existing flat concurrency tests rely on the registered process-level CTest
  timeout, including a blocked writer. No timeout is raised by this audit.

The prior main qualification remains source-bound evidence from #674:
seven configurations / 570 executions, Linux MDBX 97/97, ASan/UBSan 8/8 and
installed-consumer qualification. It is not a newly rerun matrix for this
documentation change. The previous long-TEMP MSVC failure and flat sanitizer
timing margin remain limitations; a successful run is not general timing proof.

The bounded TSan attempt built and executed four actual storage tests on the
exact runtime baseline above: three passed and the flat-storage test failed
with a race diagnostic in pinned libmdbx. **TSan qualification: NOT_QUALIFIED**;
this is an executed diagnostic attempt, not `NOT_EXECUTED` and not a passing
storage qualification. Pinned libmdbx explicitly warns that it is incompatible
with TSan, and GCC warns that its `atomic_thread_fence` instrumentation is
unsupported. Neither warning proves the reported race is false. The pre-lock
`txn->owner` read and the concurrent owner write require dependency-level
analysis before disposition. No suppression, blind retry or dependency patch
was applied. See the [applicability note](experiments/2026-11-21-storage-tsan-applicability.md)
for the commands, exact diagnostic, results, retained local artifact hashes
and limits. The separate standalone runtime probe passed, but is not a storage
test.

### Performance limits retained

No full long benchmark is rerun. Historical measured-source SHA and retained
observations are unchanged. Flat is not qualified as universally faster than C1;
`commit` still loads the previous full revision, structure validation includes
active and historical identities, and logical counters do not measure physical
page I/O or production serving tails. The separate CI corrective PR selects
quick benchmark gates, not a multi-hour sweep or production throughput gate.

## Review and implementation order

1. Independently finalize #674 without merging it under the old authorization.
2. Review this audit/ADR and the separately delivered identity/API proposals.
3. Review the small independent canonical benchmark CI corrective.
4. After Identity Architecture, review the historical coordinator reuse matrix,
   authorize only the missing bounded transaction/binding prerequisites, then
   implement the proposed opt-in product slice with the complete
   negative/fresh-process matrix. No coordinator is implemented in this phase.
5. Only after that qualification, review the separate real migration plan.

### Old Draft differential audit

The read-only comparison used actual main above, not a trial merge or the old
drafts' triple-dot statistics as an effective current-main diff:

| Draft | Exact observed HEAD | Unique contribution and disposition |
| --- | --- | --- |
| [#649](https://github.com/LimiNode/agent-memory-cpp/pull/649) | `78bd797e929386299ecbeb290636f28b8976e125` | Historical canonical storage completion audit. Identity/revision/encoding and no-reuse invariants remain useful; its absent/contract-only flat/navigation gap rows are stale after #654/#662. Preserve as historical, or refresh current-status analysis; do not automatically merge or close. |
| [#651](https://github.com/LimiNode/agent-memory-cpp/pull/651) | `514b483c7c04614b99d98a3be9b225b80fee32cb` | Unique backend-neutral **C1** DTO/codec extraction; `CanonicalContentStorage.{hpp,cpp}` is absent from current main. It is not a duplicate of the merged flat codec. Needs an explicit public-wire-boundary decision and a small refreshed extraction, not wholesale old adapter replacement. |

Both have old merge base `caaacc67d367a13717c2da96a1e2ce6da9bd6524`
with the audited main. Original patch statistics are not current integration
evidence. No fresh build, trial merge, branch mutation, closure or requested
review dismissal was performed for these drafts.

A refreshed #651 must preserve later C1 protections:

- [Current revision decoder](https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.cpp#L248)
  checks the navigation-only block bound and `n <= remaining / 9` before reserve.
  The old draft's generic one-byte feasibility check is not equivalent.
- [Ordinary body decoder](https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.cpp#L536)
  admits otherwise valid large bodies without the navigation ceiling, checking
  the minimum trailer and `n <= (remaining - 36) / 8` before allocation.
- Keep exact-revision navigation, deferred text selection, integrity checks,
  sequential metadata key/value decode, current shared SHA-256 and optional
  externally synchronized metrics. Do not reintroduce the draft's private SHA.
- Require persisted-byte fixtures, C1 codec parity and current navigation/large
  body tests. Old round-trip tests alone do not qualify extraction on main.

The useful C1 extraction is not a dependency of numeric product publication.
It should proceed only as an independently reviewed compatibility change if its
public API benefit remains justified. R6 #678/#679 and all second-agent work
remain unchanged.
