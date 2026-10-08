# Storage Backend Integration Roadmap

**Status:** Normative architecture contract and roadmap; documentation only. This
guide does not introduce production C++ APIs or claim that the context, bundle,
SQLite, or canonical-content integrations already exist.

This guide owns the backend integration boundary:

- how a host creates or attaches a persistent backend;
- connection/environment ownership and shutdown;
- how domain stores share one backend context;
- which operations may be atomic;
- how MDBX, SQLite and mixed topologies are composed;
- how backend errors and semantic conformance are reported.

It complements, and does not replace:

- [architecture.md](architecture.md) for dependency direction and layers;
- [canonical-content-storage-roadmap.md](canonical-content-storage-roadmap.md)
  for canonical content and edit semantics;
- [resource-reindexing.md](resource-reindexing.md) for resource generations
  and derived-record ownership;
- [mdbx-containers-extension-tz.md](mdbx-containers-extension-tz.md) for the
  physical MDBX manifest and upstream primitive contract;
- [milestones.md](milestones.md) for implementation scope and status;
- [dependencies.md](dependencies.md) for the flat CMake dependency graph.

## 1. Factual audit

The audit was performed against:

- agent-memory-cpp origin/main at
  45e723166e468ae5669a3e7fe74c8e2de3edc565;
- the checked-in external/mdbx-containers remote's main at
  2e8b914b8a1e3a683273ee7e471de764423b2308.

The upstream checkout at that revision exposes mdbxc::Connection,
mdbxc::Transaction, explicit transaction overloads on table wrappers,
KeyValueTable, KeyTable, KeyMultiValueTable, KeyOrderedMultiValueTable,
ValueTable, SequenceTable and TableSequence. Connection::shutdown() and
shutdown_for() provide coordinated lifecycle operations; Config includes
read_only, max_dbs, relative_to_exe and other environment settings. Transactions
and cursors remain thread-bound.

The current adapter slice is intentionally smaller:

| Current adapter | Current construction | Current tables/operations |
|---|---|---|
| src/agent_memory/infrastructure/mdbx/MdbxDocumentStorage.cpp | Impl calls mdbxc::Connection::create(make_config(options)) | One connection owns documents, chunks and document_chunks; upsert_document and erase_document use one local writable transaction across those tables |
| src/agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.cpp | Impl calls mdbxc::Connection::create(make_config(options)) | One connection owns the manifest table; wrapper methods use the table's own short transactions |
| src/agent_memory/infrastructure/mdbx/MdbxResourceIndexRecordOwnerStorage.cpp | Impl calls mdbxc::Connection::create(make_config(options)) | One connection owns document-owner and chunk-owner tables; wrapper methods use the table's own short transactions |

All three option structs currently carry a path, table prefix and relative_to_exe;
their local make_config functions set the pathname, max_dbs = 16, no_subdir and
relative-path behavior. They do not expose read_only, an attached connection,
a shared context, or a bundle/factory.

This yields the following factual limitation:

~~~text
MdbxDocumentStorage             -> Connection A
MdbxResourceManifestStorage     -> Connection B
MdbxResourceIndexRecordOwnerStorage -> Connection C
~~~

Even when the three adapters receive the same path, they do not share one
Connection or one caller-visible transaction boundary. Each adapter privately
owns its connection lifetime, and no adapter can pass a transaction to another
adapter through the dependency-free storage interfaces. This affects shared
environment ownership, multi-store atomic writes, shutdown coordination,
read-only propagation and transaction provenance.

This is an **architectural limitation of the current prototype adapter slice**.
It is not a production bug under the contracts that those prototype interfaces
currently expose. The new canonical-content and conversation implementations
must use the context model below instead of extending this per-store pattern.

The practical consequences are:

| Concern | Current prototype behavior | Architectural consequence |
|---|---|---|
| Shared environment ownership | Each adapter owns a private shared pointer to its own `Connection` | Same path does not create one shared lifecycle root |
| Multi-store atomicity | A document adapter can be atomic across its own tables only | Manifest, owner, document and projection writes cannot share one caller-visible transaction |
| Shutdown/lifetime | Adapter lifetime controls its private connection; no attach mode exists | Host worker shutdown and environment drain cannot be coordinated across stores |
| Read-only mode | Current option structs do not expose `Config.read_only` | A caller cannot select adapter-level read-only behavior through these APIs |
| Transaction provenance | Transactions are opened and closed inside store methods | The caller cannot establish or verify that several writes use one backend context |

The current CMake path is otherwise aligned with the intended dependency
direction:

- AGENT_MEMORY_ENABLE_MDBX gates the MDBX sources;
- agent_memory_provide_mdbx_containers first reuses a parent-provided
  mdbx_containers::mdbx_containers target;
- it then uses the flat external/mdbx-containers source when present, or an
  installed package as fallback;
- flat external/libmdbx is made available before the source build when needed;
- the dependency is linked to the implementation target and does not appear in
  dependency-free domain/storage contract headers.

## 2. Domain boundary and non-goals

Domain contracts remain backend-neutral and dependency-free:

- ICanonicalContentStore;
- ICanonicalContentEditor;
- IConversationStore;
- IKnowledgeUnitStore;
- artifact, manifest, projection and index contracts;
- other coarse domain-specific stores.

The core must not add any of the following:

- IDatabase;
- IConnection;
- ITransaction;
- a SQL-shaped generic storage API;
- a KV-shaped generic storage API;
- a cross-backend TransactionGuard.

A backend transaction is an implementation mechanism for a coarse domain
operation. It must not leak through the domain surface merely to make MDBX and
SQLite look alike. SQLite is an alternative implementation of the same
semantic contracts, not a reason to erase each backend's useful transaction and
session model.

The storage backend integration layer is allowed to have backend-specific
types. They live under optional infrastructure headers and factories, not under
src/agent_memory/storage/ or the domain headers.

## 3. Backend-specific context

A production backend integration has a context/lifecycle root per logical
backend environment.

Conceptual MDBX shape:

~~~text
MdbxStorageContext
    shared mdbxc::Connection
    table namespace / prefix
    schema and profile identity
    DBI budget and environment configuration
    ownership mode and shutdown policy
~~~

Conceptual SQLite shape:

~~~text
SqliteStorageContext
    SQLite connection/session resources
    schema and profile identity
    journal/WAL and busy-timeout policy
    ownership mode and shutdown policy
~~~

These are design names, not existing public C++ classes.

The rule is **one shared Connection per logical MDBX environment/context**.
It is not one process-global singleton. Independent databases, workspaces or
tenants may have independent contexts, even when they are created by the same
application.

All stores that must participate in common MDBX snapshots or atomic writes are
constructed from the same MdbxStorageContext. Merely giving two adapters the
same filesystem path does not establish a shared context.

### 3.1 Owned MDBX mode

In owned mode, the agent-memory factory receives MDBX-specific options such as a
path, namespace, environment flags, map-growth policy and read-only setting. It
creates the mdbxc::Connection, creates the context, and is responsible for the
context lifecycle.

The context must remain alive until all table wrappers, stores, cursors and
transactions have stopped using it. Shutdown follows the mdbx-containers/MDBX
rules: a clean lifecycle may use disconnect() after all handles are gone;
coordinated stop may request shutdown() or shutdown_for(timeout).

Owned mode does not imply that every store opens its own environment. The
factory creates one context, then creates all requested stores from that
context.

### 3.2 Attached/shared MDBX mode

In attached mode, the host supplies an existing shared
std::shared_ptr<mdbxc::Connection> or an equivalent backend-specific handle.
This allows several application subsystems to use one MDBX environment and
lets agent-memory stores participate in the host's environment topology.

The connection must outlive every context, table wrapper and store that uses it.
A context may retain a shared C++ handle to keep the object alive, but lifecycle
authority remains with the host:

- agent-memory must not call shutdown() or disconnect() on a host-owned
  connection unexpectedly;
- the host coordinates worker stop, transaction drain and final shutdown;
- attached stores inherit the connection's read-only and environment settings;
- transactions and cursors obey the connection's thread-ownership rules.

The C++ handle lifetime and the environment's operational lifetime are separate
contracts. A `shared_ptr` keeps the connection object alive, but it cannot stop
the host from calling `shutdown()` or disconnecting the environment. The host
must not perform that shutdown while agent-memory operations are in flight. If
the environment has already been shut down, attached stores fail with the
backend's unavailable/shutdown category; they must not infer that a live
`shared_ptr` makes the environment operational.

The exact constructor and ownership marker are intentionally deferred. The
contract is about lifetime authority and transaction provenance, not a
particular signature.

### 3.3 Public header decision for attached connections

Two designs were considered:

| Design | Host integration | ABI/dependency isolation | Ownership clarity | Shared-environment ability |
|---|---|---|---|---|
| Typed MDBX-only entry point exposing std::shared_ptr<mdbxc::Connection> | direct and simple for hosts already using mdbx-containers | dependency is confined to an optional MDBX header; core remains clean | explicit | direct |
| Opaque/PImpl-only entry point | requires a bridge, factory or opaque adapter to attach an existing connection | strongest header and ABI isolation | ownership is easier to obscure | possible, but indirect |

The chosen direction is a **typed MDBX-specific construction/attach surface**:
a future optional header may include mdbx-containers types and accept an existing
std::shared_ptr<mdbxc::Connection>. That dependency is allowed to remain in
infrastructure/mdbx; it must not enter domain or dependency-free storage headers.
Concrete stores may still use PImpl to keep implementation details and
serialization out of their public class layout.

This choice optimizes existing-environment sharing and makes ownership visible.
Exact signatures, ABI policy and package-export details remain deferred until a
real attached use case is implemented.

## 4. mdbx-containers role

mdbx-containers is the recommended MDBX implementation substrate. The
agent-memory layer should use its existing primitives where their durable
semantics fit:

- mdbxc::Connection;
- mdbxc::Transaction;
- KeyValueTable;
- KeyTable;
- KeyMultiValueTable;
- KeyOrderedMultiValueTable;
- ValueTable;
- SequenceTable;
- TableSequence;
- range, bulk and explicit shared-transaction overloads.

The project adds the domain layer above those primitives:

- domain serialization and payload validation;
- schema/profile versioning and migrations;
- table names, namespace rules and DBI manifests;
- Document, Conversation, KnowledgeUnit, SourceRef, ContentBlock and related
  semantics;
- resource ownership, generation publication and retrieval projections;
- coarse operations that decide which tables must commit together.

mdbx-containers must not learn agent-memory domain types. A downstream pattern
such as a small transaction helper may remain application-local until a second
independent consumer proves a generic upstream contract. This guide does not
turn mdbx-containers-extension-tz.md into an automatic upstream backlog.

The current upstream API already supports the essential composition pattern:

~~~cpp
auto txn = connection->transaction(mdbxc::TransactionMode::WRITABLE);
documents.insert_or_assign(document_key, document_bytes, txn);
manifest.set(manifest_value, txn);
txn.commit();
~~~

This is a conceptual illustration. It does not prescribe the future
agent-memory schema or public API.

## 5. Shared MDBX topology

The desired topology is:

~~~text
MdbxStorageContext
       |
       +-- MdbxCanonicalContentStore
       +-- MdbxConversationStore
       +-- MdbxKnowledgeUnitStore
       +-- MdbxManifestStore
       +-- MdbxProjectionStore
       +-- other domain stores
~~~

Stores in one context may have separate table wrappers and DBIs, but they use
one shared connection when they need common snapshots or transactions. Table
names and DBIs remain owned by the selected memory profile and its physical
manifest. A namespace/prefix is a naming boundary, not a substitute for a
context.

A host may intentionally create multiple contexts for independent databases or
workspaces. No global singleton, implicit path-based registry or automatic
cross-context transaction is required.

## 5.1 Multi-context topology and workspace routing

A process may intentionally host several independent backend contexts. A context
is the lifecycle, transaction and snapshot boundary for one logical backend
environment; it is not a process-global singleton and it is not automatically
a shard of every other context.

The durable topology contract is:

```text
logical workspace / tenant / memory profile
        -> explicit placement binding
        -> one backend context
        -> domain stores and projections in that context
```

A placement binding is application-owned composition metadata. It names the
logical workspace or tenant scope, backend/profile, context identity and
routing generation. It must be explicit and versioned; filesystem paths,
database filenames and incidental object construction order are not logical
identity. A mutable logical corpus must not be actively written through two
contexts unless an explicit replication/publication protocol owns that split.

A routing generation is a versioned placement precondition, not by itself a
write fence. For the initial routing slice, a placement is immutable after
workspace creation; hot relocation is unsupported. A future placement change
may be introduced only through an explicit handoff or migration protocol that
fences or drains writers holding the old generation before the new context
accepts authoritative writes. Writers must validate the binding at write
admission and publication, and a stale writer must not publish to the previous
home. A generation check is not treated as an atomic cross-context commit.

The following cases are distinct:

| Topology | Guarantee |
|---|---|
| Several stores in one context | They may share one backend transaction or read snapshot when the domain operation requires it. |
| Independent contexts of the same backend | They have separate lifecycle, transaction and snapshot boundaries; no cross-context ACID is implied. |
| Contexts of different backends | They share domain semantics only through backend-neutral contracts and conformance; no backend transaction crosses the boundary. |
| One logical query over several contexts | It is federated retrieval with per-context frontiers, provenance and partial/unavailable outcomes. |

A future domain router may resolve a workspace or tenant to a context, but it
belongs above individual stores and below the application composition root. It
must not be a generic `IDatabase`, `MultiDatabase`, implicit path registry or
automatic cross-context transaction manager. The first useful router is
domain-specific (for example, a workspace storage registry or a canonical
content router) and must declare its routing key, placement generation,
read/write policy and failure semantics.

Identity and placement remain separate layers:

- logical object identity is stable across physical relocation;
- logical scope or workspace is the namespace and ownership boundary;
- physical context identity identifies the current storage home;
- placement generation identifies a routing/publication version.

Physical context identity and placement generation are routing or provenance
metadata; they are never part of durable logical object identity. Where a domain
has only local IDs, a cross-context reference carries the owning logical scope
together with that local ID. Where it has a global logical identity, references
use that identity and retain placement provenance separately. Relocation must
not silently reinterpret an existing reference as a different object. Federation
deduplicates by canonical logical identity, or by the `(logical scope, local ID)`
pair when that is the domain identity; a shared context boundary does not make
records independent evidence. A local `KnowledgeUnitId("42")` or document key
is never assumed globally unique. A router must fail closed on an unknown, stale
or ambiguous placement rather than guessing from a path or silently searching
every context.

The C1 MDBX reference slice implements this contract with
`MdbxWorkspaceStorageRegistry` and `MdbxCanonicalContentRouter`. A registry
binding is create-only and retains one `MdbxStorageContext` plus one
workspace-local canonical store. Router calls carry the workspace key and
expected generation explicitly; unknown workspaces, stale generations and
duplicate bindings throw before a domain operation is dispatched. Reopening
two independent MDBX files therefore requires rebuilding the same explicit
bindings, and does not infer routing from filenames. The implementation and
integration coverage are limited to canonical content in the R0 slice; this is
not a general federation, relocation or cross-context transaction facility.

Cross-context writes use an explicit publication protocol:

```text
intent / idempotency key
    -> commit in context A
    -> commit in context B or publish an outbox
    -> receipt / reconciliation record
```

If a later step fails, the result is partial or pending publication and remains
observable. It is not reported as one atomic commit. Recovery is retry/reconcile
under the same idempotency key; compensation is a domain policy, not rollback
across independent environments. Cross-context read results likewise retain
one read frontier and completion status per context. A union or RRF merge is
not evidence of a shared snapshot.

The topology contract is backend-neutral. MDBX uses one
`MdbxStorageContext` per environment; SQLite or another backend may use its
native context/session boundary. A backend implementation is interchangeable
only for the domain semantics it claims and only after the applicable
conformance cases pass.

This section defines the topology and boundaries only. It does not select a
sharding algorithm, placement-balancing policy, replication transport,
cross-context transaction protocol or public router ABI.

## 6. Transaction ownership and atomicity

### 6.1 Single-store operation

A store may open a short backend transaction internally for a single-store
operation. If the store touches several of its own logical tables, it uses one
transaction for that operation. The current MdbxDocumentStorage already
follows this pattern for its document, chunk and document-to-chunk tables.

### 6.2 Same-context multi-store operation

When one domain operation must change several stores atomically, the backend
implementation owns one transaction from the common context:

~~~text
one mdbxc::Transaction
    -> canonical revision
    -> blocks/body descriptors
    -> manifest and ownership records
    -> projection publication/invalidation
    -> one commit
~~~

The transaction may be passed internally to table wrappers through their
explicit transaction overloads. It is not exposed as a generic domain
parameter, and a caller must not assemble a cross-store operation by passing
backend transactions between unrelated public interfaces.

A future commit_document_revision() may therefore atomically publish a
revision, blocks, body/frame descriptors, a change set, the active-revision
pointer, manifest/ownership records and projection invalidation state, subject
to the selected profile's DBI and transaction-size limits.

### 6.3 Same-context consistent read snapshot

Read consistency has the same backend boundary as write atomicity. A domain
operation that reads several stores as one logical view must not let each store
open an unrelated read transaction:

```text
read active revision
read blocks/body descriptor
read manifest/ownership
read projection generation
        -> one backend-owned read-only transaction/snapshot
        -> one coherent result
```

The minimum contract is:

| Read shape | Required behavior |
|---|---|
| Single-store read with no cross-store consistency requirement | The store may open one short internal read-only transaction. |
| Same-context consistent domain read | The coordinator opens one backend-owned read-only transaction/snapshot and all participating stores use it internally. |
| Independent reads explicitly allowed by the operation | Separate short snapshots are permitted, but the operation must say that a mixed generation is acceptable. |
| Cross-backend read (MDBX + SQLite/CAS/external index) | No common ACID snapshot is implied; use a declared frontier/version/point-in-time policy and validate each side. |

For MDBX, the shared `MdbxStorageContext` opens one read-only
`mdbxc::Transaction` from its connection and passes it to table wrappers through
internal overloads. The transaction remains thread-bound and must not outlive
the read operation. The domain interface receives a domain result or read
context owned by the backend coordinator, never a generic `ITransaction`.
SQLite uses its native read transaction/session semantics under the same rule;
it does not have to imitate an MDBX transaction type.

Canonical materialization, generation-publication verification and retrieval
hydration should use this shared snapshot whenever they read the active
revision, body/block descriptors, manifest or projection generation together.
Long-lived snapshots, caller-created backend transactions and a `shared_ptr` to
a connection as a substitute for snapshot consistency are out of scope. A
snapshot is a consistency boundary, not a new storage API.

### 6.4 Cross-backend operation

There is no implicit ACID transaction across:

- MDBX and SQLite;
- MDBX and an external vector service;
- MDBX and a file/object store;
- two independent MDBX contexts.

Such workflows use the appropriate publication protocol:

- generation publication;
- outbox/job;
- durable intent and receipt;
- retry and idempotency;
- reconciliation;
- staged publication.

A type named TransactionGuard must not suggest that these systems share one
commit. If a future operation crosses backends, its contract must state which
side is canonical and how incomplete external work is repaired.

### 6.5 ResourceIndexer boundary

ResourceIndexer currently composes IDocumentStorage,
IResourceManifestStorage, IResourceIndexRecordOwnerStorage, an embedder and
a vector index. Those interfaces do not provide one shared cross-store
transaction. The prototype therefore uses compensation, snapshot restoration,
owner repair evidence and pending-reclaim records around independently
committed operations.

That machinery is an honest prototype boundary. It is not the template for
canonical-content storage. A future coarse domain operation should move the
correctness-critical publication into one backend-owned transaction when all
records share one context; external indexes and side effects still require
generation/outbox/reconciliation protocols.

## 7. Coarse domain operations

Transaction boundaries belong to the operation that knows the semantic
invariant. Examples include:

- commit a canonical document revision;
- append a conversation event and its branch/reply indexes;
- publish a resource generation and its required manifest;
- create a knowledge unit with components and mandatory projections;
- replace an ownership manifest and invalidate derived records.

The implementation may use several physical tables, but callers see a
domain-oriented operation and a domain error/result. Gate B now provides
research evidence for incremental dependency frontiers; it does not choose the
production API or DBI layout. Exact operation names, batch limits and DBI
deltas remain deferred until the M1b canonical-text design.

Parsing, embedding, ANN construction, large reindex work and external network
calls must happen outside the short publication transaction. The operation
publishes only prepared, revision-guarded data.

## 8. Storage composition

A convenience composition layer is useful, but it is not a universal database
facade.

Conceptual shape:

~~~text
StorageBundle
    canonical_content
    conversations
    knowledge_units
    artifacts
    projections
~~~

The bundle exposes domain-oriented interfaces. Conceptual factories are:

~~~text
create_mdbx_storage_bundle(options)
create_sqlite_storage_bundle(options)
~~~

The MDBX factory creates one context and shares it among the stores it returns.
The SQLite factory uses its native connection/session strategy. A custom bundle
may later combine MDBX canonical content, a file/object CAS for large
artifacts, SQLite for a structured projection, and an external vector service.

A bundle records topology; it does not promise that all fields share one
transaction. Atomicity is guaranteed only inside one backend context and only
for operations whose domain contract says so. The bundle is a roadmap
composition contract, not a production API in this pass.

## 9. Configuration separation

Generic semantic/profile configuration and backend-specific options remain
separate. There is no giant universal StorageOptions.

Conceptual MDBX-specific options include:

- path or an attached connection handle;
- table namespace/prefix;
- read_only;
- MDBX environment flags;
- DBI budget;
- map-growth and relative-path policy;
- owned versus attached lifecycle mode.

Conceptual SQLite-specific options include:

- database path or connection/session handle;
- read_only;
- WAL/journal choice where relevant;
- busy timeout;
- schema namespace and version strategy;
- owned versus attached lifecycle mode.

An attached context inherits the connection's environment and read-only mode;
it cannot silently reopen the same path with different flags. Backend options
must not force SQLite to imitate MDBX flags or make MDBX expose SQL concepts.

## 10. Error boundary

The semantic error boundary has three layers:

1. **Domain outcome:** not found, invalid input, revision conflict,
   incompatible revision, stale publication or retention conflict.
2. **Backend category:** unavailable, I/O failure, corrupt data, read-only,
   busy/locked, capacity/map limit or shutdown/lifecycle failure.
3. **Diagnostic detail:** backend code, operation, table/DBI, path or profile,
   generation and the original backend exception where available.

Ordinary domain callers should branch on the first two layers and must not parse
raw MDBX codes or SQLite strings to implement semantic behavior. The exact
error value/exception type is deferred; a large backend-specific exception
hierarchy is explicitly out of scope.

## 11. Capability and conformance boundary

A backend is first-class for a domain store only after it passes the same
semantic conformance suite. MDBX remains the reference/default backend.
Conformance compares observable behavior, not physical implementation details.

The first planned second-backend profile is ConversationStore. MDBX/SQLite
parity must cover:

- append and read;
- revision/supersede;
- ordering;
- branch/reply identity;
- optimistic conflict;
- snapshot/materialization;
- reopen/durability;
- atomic publication;
- deletion and retention semantics.

The suite must not compare physical row IDs, DBI/page layout, SQL query plans,
or a particular table wrapper. A backend may use different indexes and
transaction/session internals while preserving domain semantics.

## 12. CMake and dependency contract

The current CMake topology remains the contract:

1. AGENT_MEMORY_ENABLE_MDBX is opt-in.
2. A parent-provided mdbx_containers::mdbx_containers target is reused first.
3. Otherwise the flat external/mdbx-containers source is used when present.
4. Installed-package lookup is the fallback when no local source is selected.
5. libmdbx and mdbx-containers remain sibling dependencies under external/.
6. The dependency-free core target does not expose MDBX types transitively;
   typed MDBX integration is an optional exported target.

No CMake or source changes are part of this docs pass. A future attached
connection header may include mdbx-containers types directly because it is an
MDBX-specific optional surface. Once that header is installed, its package and
include dependency is part of the consumer contract; it cannot be described as
a purely `PRIVATE` implementation dependency.

The preferred target topology is conceptual and does not fix exact CMake names:

```text
agent_memory::agent_memory
    dependency-free domain/storage contracts

agent_memory::mdbx
    optional typed MDBX integration
    PUBLIC/INTERFACE -> mdbx_containers::mdbx_containers
```

The optional integration target owns the installed MDBX-specific attach header,
exports the transitive mdbx-containers include/link requirement and makes the
package configuration discover that dependency when the target is consumed.
The core target remains usable without `AGENT_MEMORY_ENABLE_MDBX`; consumers
that pass `std::shared_ptr<mdbxc::Connection>` explicitly opt into the backend
target. Making mdbx-containers PUBLIC on the core target is an alternative, but
would unnecessarily enlarge the dependency surface for hosts that never use
MDBX. Exact target names, export-file mechanics and ABI policy remain
implementation work for the attached vertical slice.

## 13. Capability state and deferred work

### Already implemented

- Backend-neutral storage interfaces in src/agent_memory/storage/.
- The current prototype MDBX adapters for documents, manifests and
  ResourceIndexer owner records.
- Per-adapter MDBX connections and per-operation transactions.
- Optional CMake wiring and flat dependency lookup.
- mdbx-containers primitives used by the adapters.
- Prototype ResourceIndexer compensation and pending-reclaim behavior.
- The canonical-content and semantic-decision roadmap contracts from PRs #471
  and #472.

### Contract only

- MdbxStorageContext and SqliteStorageContext concepts.
- Owned and attached MDBX construction modes.
- One shared connection per logical environment/context.
- Same-context multi-store atomicity owned by coarse domain operations.
- Same-context consistent reads use one backend-owned read-only
  transaction/snapshot when the operation requires a coherent view.
- Cross-backend publication/outbox/reconciliation boundary.
- Multi-context topology, explicit workspace/tenant placement and routing-boundary semantics.
- StorageBundle topology and factory concepts.
- Backend error categories and semantic conformance requirements.
- Typed MDBX-specific attached-connection header boundary.
- Optional package/export target for the typed MDBX header and its transitive
  mdbx-containers dependency.

### Roadmap only

- ICanonicalContentStore implementation and its MDBX body/block tables.
- ConversationStore and its SQLite implementation.
- Knowledge-unit, artifact and projection bundles.
- Read-only propagation and attached/shared connection APIs.
- Read-snapshot coordinator and backend-specific snapshot/session plumbing.
- Installed package/export mechanics for the optional MDBX integration target.
- Context-aware resource importer and canonical revision publication.
- Conformance suites and reopen/crash tests for the new stores.

### Intentionally deferred until M1b implementation/design

- Exact context, factory, bundle and coarse-operation signatures.
- Canonical-content DBI/profile deltas and transaction-size limits.
- Public ownership markers and destructor/shutdown policy details.
- SQLite driver/session schema and WAL policy.
- Error value/exception type names.
- Production routing/placement implementation, replication transport and any cross-context atomicity.
- Production artifact/file-CAS integration and external vector publication.

The merged Gate B screen is evidence for the dependency/frontier model, not a
production implementation gate for these signatures.

## 14. Decision table

| Question | Decision |
|---|---|
| Default backend | MDBX |
| MDBX implementation substrate | mdbx-containers |
| Connection ownership | Owned or attached backend context |
| Stores per environment | Shared context/connection |
| Same-backend atomicity | Backend-owned transaction |
| Same-context consistent read | Backend-owned read-only transaction/snapshot |
| Cross-backend atomicity | Not implied |
| Core generic IDatabase | No |
| SQLite role | First-class alternative plus separately external SQL source |
| First SQLite conformance target | ConversationStore |
| Global singleton connection | No; one connection per logical environment/context |
| Attached MDBX surface | Typed MDBX-specific header; keep it out of core/domain headers |
| Typed MDBX package surface | Optional exported integration target with a transitive mdbx-containers dependency |
| Bundle semantics | Convenience topology; no universal transaction promise |
| Multi-context topology | Explicit placement and domain-specific routing; no path-based or implicit global registry |
| Cross-context writes | Publication/outbox/reconciliation; never one atomic transaction |
| Cross-context reads | Per-context frontiers and completion; federated result semantics |
| Read-only behavior | Owned mode configures it; attached mode inherits it |
| Physical frame/table identity | Backend detail; never a domain identity |

## 15. Verification and next step

This pass must remain documentation-only:

- re-audit the listed source files and the pinned/current dependency snapshots;
- run git diff --check;
- validate relative Markdown links;
- do not add production code;
- open a draft PR;
- do not merge without a separate explicit request.

The first follow-up implementation should add one domain-specific routing seam
for an explicit workspace placement registry and prove unknown/stale placement,
per-context read frontier, and interrupted cross-context publication cases before
introducing any broader router or sharding abstraction.

After this contract is reviewed, implementation work can be scheduled against
the canonical-content Gate B/M1b design. The first implementation should create
a context-aware MDBX vertical slice and prove same-context atomic publication
before adding a production StorageBundle or SQLite adapter.
