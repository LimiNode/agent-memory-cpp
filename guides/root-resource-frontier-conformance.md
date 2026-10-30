# Root Resource Frontier and Fenced Derived Publication

Status: Draft design plus B1 read-snapshot implementation. B1 does not
implement fenced publication, change production retrieval, or make derived
records authoritative.

Baseline: `main@0ae237eee8084d09778280c73fb604231a275607`, after #570.

## Scope and boundary

The KnowledgeUnit-local admission slice in #569 proves only that the stored
source references of the requested units match one caller-declared ordered
list. It intentionally does not prove that those references still point at the
current root Resource revisions, that several inputs were read from one
consistent snapshot, or that a publication cannot race a source mutation.

This plan closes that contract gap on paper before an implementation PR is
started. It is graph-free and storage-facing. It does not add a scheduler,
agent runtime, summary generator, RAPTOR/MemWalker implementation, compact
scorer, lexical-agent work, or production retrieval activation.

## Existing owners and observed guarantees

| Existing owner/API | What it proves today | What it does not prove |
| --- | --- | --- |
| `ResourceManifest` / `IResourceManifestStorage` | A resource revision, lifecycle state and derived-record ownership can be persisted and read. | A read of several manifests is one snapshot; a manifest is not a derived-publication frontier. |
| `MdbxResourceManifestStorage::upsert_manifest_if_unchanged()` | One manifest replacement is conditional on its previously observed serialized value. | A root mutation cannot race a separate derived publication. |
| `MdbxResourceManifestStorage::upsert_manifest_if_unchanged_guarded()` | The conditional replacement and a durable value/token fence are checked in one MDBX write transaction. | The fence is not currently connected to `MdbxDerivedPublicationStore`. |
| `MdbxResourceImporter` | Resource import intent, fencing epoch, lease recovery, canonical/lexical/raw publication and manifest publication are guarded within its import profile. | It does not expose a reusable multi-input frontier snapshot for derived publication. |
| `IKnowledgeUnitStore` / `MdbxKnowledgeUnitStore` | Unit lifecycle, unit revision and revision-bound source summaries can be read; guarded unit writes reject a stale import token. | Sequential `find()` calls are not a consistent multi-unit snapshot and a stored `ResourceRevisionRef` is not authoritative root freshness. |
| `MdbxCanonicalContentStore` | Current and historical canonical document revisions are read separately; guarded create/edit operations reject a stale import token. | It is not a root-frontier resolver for a derived publication. |
| `IDerivedPublicationStore` / `MdbxDerivedPublicationStore` | Body, manifest and active marker are one durable storage transaction; `StoredMatch` is a storage comparison only. | It has no trusted root frontier, read snapshot, commit fence or query-time source revalidation. |
| `DerivedPublicationAdmission` | Bounded KnowledgeUnit-local admission and explicit non-authoritative statuses. | It cannot classify `A@1 -> K@1 -> D@1` as stale after `A@2` without a root frontier provider. |

## Contract-gap matrix

| Required guarantee | Existing API surface | Missing guarantee | Minimum implementation change |
| --- | --- | --- | --- |
| Root Resource freshness | `ResourceManifest::revision`, `find_manifest()` | Current root revision must be compared with every recorded input revision before `Current` read or commit. | Add a bounded root-frontier resolver returning immutable `(resource, generation, digest, lifecycle)` entries; reject any recorded revision that is not the current active entry. |
| Consistent multi-input snapshot | `MdbxStorageContext`, independent `find()` methods | Several stores can otherwise observe different commit points. | Open one read-only MDBX transaction on the shared context and have the frontier/unit/canonical adapters read through it, or return `Unavailable` when that transaction cannot be shared. |
| Fenced publication | Existing manifest and importer token guards | Derived body/manifest/marker are not checked against all observed root epochs in the same commit. | Carry the snapshot fence set into one writable commit transaction; re-read every expected root token and atomically publish or reject. Reuse existing token tables and guard encoding. |
| Query-time revalidation | `IDerivedPublicationStore::read()` caller values | A stale reverse-invalidation marker may leave an old publication looking current. | Read the committed publication and root frontier in one bounded read snapshot; return `Stale`/`Unavailable` before body or evidence hydration. |
| Exact provenance/evidence | `DerivedPublicationInput`, `SourceRefSummary`, canonical revision reads | The local input list is not proven complete causal lineage and derived body has no automatic canonical hydration. | Persist exact ordered root frontier and unit bindings; require an explicit evidence hydration step before a result is complete. |
| Historical reads | `ResourceManifest` and canonical `read_revision()` | Historical validity must not be confused with current freshness. | Keep `Historical` read mode separate; never substitute a newer root revision for a missing retained revision. |
| Scope isolation | `ScopeId` on units/publication request | The current derived store is single-scope and publication IDs are backend-local. | Keep scope in every frontier/publication key and reject cross-scope inputs; defer federated identity to a separate contract. |

## Selected minimal mechanism

The implementation PR should reuse the existing shared `MdbxStorageContext` and
durable per-resource fencing tokens. The selected boundary is a narrow
storage-level transaction owner, not a general-purpose coordinator:

1. A bounded read transaction captures a `RootResourceFrontierSnapshot` for all
   roots named by the ordered KnowledgeUnit inputs. It includes resource ID,
   active generation, body digest, lifecycle and the exact fence token observed
   for each root.
2. Unit and canonical reads used to construct the publication execute against
   that same snapshot. If a backend cannot participate in the transaction, the
   operation returns `Unavailable`; it must not claim snapshot consistency.
3. A single writable transaction re-checks the expected root tokens and writes
   the derived body, manifest, active marker and publication generation. Any
   token mismatch aborts the complete write and returns `FenceRejected` (or
   deactivates an already staged marker for recovery).
4. Reopen recovery treats an interrupted publication as non-current until a
   complete manifest and matching frontier witness are present. Query-time
   validation repeats the bounded frontier check, so reverse invalidation is an
   optimization rather than authority.

The existing `upsert_manifest_if_unchanged_guarded()` and guarded canonical/unit
operations are the proven primitives for steps 2–3. They are not, by
themselves, a cross-store publication mechanism. The implementation PR must
either expose a shared transaction-owner operation in the MDBX adapter or add
the smallest guarded method to the derived publication adapter; it must not
invent a runtime coordinator or a new DBI without a demonstrated need.

### Transaction feasibility matrix

The shared `MdbxStorageContext` currently shares a connection, not a transaction
scope. The public methods below open and commit their own transactions:

| Operation | Current transaction owner | Safe inside a future external snapshot? | Minimum change |
| --- | --- | --- | --- |
| `MdbxResourceManifestStorage::find_manifest()` | Adapter-owned read-only transaction | No; it cannot join another read frontier. | Add a transaction-scoped read primitive or a frontier reader owned by the adapter. |
| `MdbxResourceManifestStorage::upsert_manifest_if_unchanged_guarded()` | Adapter-owned writable transaction; checks one manifest and configured guard/token tables. | No for cross-store publication, yes as a single-root primitive. | Reuse its token comparison inside the one publication write owner. |
| `MdbxKnowledgeUnitStore::find()` / scans | Adapter-owned read-only transaction per call. | No; sequential calls can observe different commits. | Add a bounded snapshot read operation that accepts the owner transaction. |
| `MdbxKnowledgeUnitStore::create_or_get_units_guarded()` | One adapter-owned writable transaction; guards a resource token. | No for a derived publication spanning other stores. | Fence direct unit revision high-water values as well as root tokens in the publication write. |
| `MdbxCanonicalContentStore::read_current()` / `read_revision()` | Adapter-owned read-only transaction per operation. | No; current and historical reads can be split. | Add transaction-scoped canonical reads or make the frontier provider the transaction owner. |
| `MdbxCanonicalContentStore::commit_guarded()` | Adapter-owned writable transaction with one import guard. | No for a multi-store derived commit. | Reuse the guard check, but perform root, unit and publication writes under one owner. |
| `MdbxDerivedPublicationStore::read()` / `publish()` | Adapter-owned read/write transaction for storage rows. | No trusted frontier is consulted. | Add a guarded publish/read operation or an adapter-level transaction owner that includes its rows. |
| `MdbxStorageContext` | Owns or attaches the MDBX connection only. | No by itself. | Do not document a shared context as snapshot compatibility; prove transaction lifetime and thread ownership first. |

Consequently, merely passing one `MdbxStorageContext` to all adapters is not an
implementation of a consistent frontier. The implementation PR must first
prove that the selected transaction owner can keep one read transaction alive
across all participating table reads and one write transaction across root
token checks, direct unit revision checks and derived publication rows. A unit
mutable-field update that advances a unit revision must not bypass the fence
even when its root token is unchanged.

The current DBI budget is not changed by this planning slice. The executable
fixture uses one resource-manifest table plus two uniquely prefixed test-only
guard/token tables (three DBIs total) in an isolated temporary MDBX
environment. Its pre-existing sentinel is an independently owned sibling
directory and is checked after MDBX cleanup, so the fixture cannot accidentally
pass by deleting the sentinel itself. It does not alter `guides/dbi-manifest.yaml`
or production table names. An implementation PR must show its DBI-slot
accounting before adding any table.

## B1 implementation slice: consistent frontier snapshot

`MdbxResourceFrontierSnapshotReader` is the first implementation slice of
this plan. It owns one read-only transaction on the shared
`MdbxStorageContext`, reads every requested KnowledgeUnit and its root
`ResourceManifest` through that transaction, and retains the transaction owner
on the returned snapshot. The adapter-scoped `find_in_transaction()` methods
are intentionally narrow; passing a shared context to independent `find()`
calls remains insufficient and is not used here.

The B1 result is available only when all inputs agree on scope, active unit
lifecycle, direct unit revision, root generation and SHA-256 body digest. A
missing or inactive root, malformed provenance, cross-scope unit, unit
revision mismatch or root mismatch fails closed with an explicit status. The
reader materializes no derived body and performs no writes. The retained
snapshot is thread-bound and must be destroyed on its creating thread, as
required by MDBX transaction ownership.

The focused MDBX fixture covers a successful pinned read, a root mutation
observed by a subsequent read, scope and unit-revision rejection, and inactive
unit rejection. B2 must still add the single writable transaction that
re-checks this frontier and publishes derived rows; it is deliberately not
part of the B1 implementation.

## Required conformance scenarios

| Scenario | Required result | Current status |
| --- | --- | --- |
| `A@1 -> K@1 -> D@1`, then `A@2` | `D@1` is `Historical` only; `Current` read is `Stale` even if K still stores A@1. | #569 demonstrates the local-match limitation; implementation fixture required. |
| One of several roots mutates | Whole publication is stale/rejected; no partial current result. | Design requirement. |
| Mutation between snapshot and commit | Fence rejects the write; prior publication remains visible. | Existing primitive negative fixture; cross-store fixture deferred to implementation PR. |
| Mutation between admission and retrieval | Query-time revalidation returns `Stale` before body/evidence hydration. | Design requirement. |
| Interrupted reverse invalidation | Old marker cannot authorize current data after reopen; historical read remains available. | Design requirement. |
| Reopen/recovery after partial publication | Incomplete body/manifest/marker is not current and is either rolled back or explicitly recoverable. | Storage-only recovery exists; frontier witness gate deferred. |
| Cross-scope input | Admission rejects the input and does not leak derived bytes. | #569 covers unit-local cross-scope rejection. |
| Missing/unavailable root manifest | Return `Unavailable` (not `StoredMatch` or `Current`). | Design requirement. |
| Retained historical evidence | Exact old revision can be read historically; a newer revision is never substituted. | Canonical storage primitive exists; integration fixture deferred. |

## Executable evidence in this planning PR

`agent_memory_mdbx_resource_frontier_fencing_test` exercises the existing
manifest guard without adding production code. It proves that a root manifest
update prepared with token `t1` is rejected after the durable token advances to
`t2`, and that retrying with `t2` succeeds. This is the smallest executable
negative fixture that the current API can support honestly.

The multi-input snapshot, cross-store atomic publication, query-time
revalidation and interrupted-invalidation scenarios are deliberately listed as
implementation-PR gates. Adding tests that merely call independent `find()`
operations would falsely claim a guarantee that the current API does not
provide.

The B1 implementation fixture is
`agent_memory_mdbx_resource_frontier_snapshot_test`. It exercises the
transaction-scoped manifest/unit reads and verifies that a materialized
snapshot retains its read transaction owner. It does not claim write-side
fencing or any current derived-publication outcome.

## Proposed read outcome contract

The future public read API must keep historical evidence and current eligibility
as separate outcomes. These names are design targets, not types added by this
PR:

| Outcome | Meaning | Required evidence |
| --- | --- | --- |
| `Historical` | The retained publication was built from the exact recorded input and root revisions. Current frontier agreement is not required. | All referenced historical unit/resource/canonical evidence is readable under the requested policy. |
| `Current` | The publication is eligible for a current retrieval result. | One consistent read frontier proves every direct unit revision and root resource is active, current and accessible. |
| `Stale` | The publication is known to be valid historically but at least one required current revision or fence no longer matches. | The mismatching root/unit identity and expected/observed generations are diagnosable without materializing unauthorized body text. |
| `Unavailable` | Freshness or historical validity cannot be established, or required evidence is missing/inaccessible. | No derived body, locator or source metadata is returned as if it were current. |

`StoredMatch` remains a storage-only comparison from the #569 slice. It must
not be promoted to `Current` by an adapter merely because the body and caller
inputs have matching bytes. A missing retained historical revision is not a
request to substitute the newer root revision.

## Acceptance gates for the implementation PR

- [ ] `A@1 -> K@1 -> D@1 -> A@2` returns `Stale` for `Current` and exact D@1 for
      `Historical`.
- [ ] A single read frontier covers all roots and all direct unit bindings;
      no sequential-find implementation is accepted as a snapshot.
- [ ] A root mutation between preparation and commit rejects the entire write;
      no body, marker or manifest becomes current.
- [ ] Query-time revalidation runs after interrupted reverse invalidation and
      before any derived body, locator or evidence bytes are materialized.
- [ ] Provenance preserves exact root revisions, unit revisions and original
      evidence references; no newer revision is silently substituted.
- [ ] Reopen/recovery, missing roots, inactive roots, cross-scope inputs and
      storage failures have explicit outcomes and no content leakage.
- [ ] No graph backend, scheduler, new runtime, compact scorer or production
      retrieval default is required.
- [ ] DBI/layout changes are justified against `guides/dbi-manifest.yaml`; the
      preferred implementation reuses existing manifest/token tables.

## Cost envelope

For `U` KnowledgeUnits and `R <= U` distinct root Resources, the target work is
bounded as follows:

| Input size | Read frontier | Commit fence/check | Expected retained witness |
| --- | --- | --- | --- |
| Typical: `U=8`, `R<=8` | One read transaction, `O(U+R)` point reads | One write transaction, `O(R)` token checks plus publication rows | `O(U+R)` length-prefixed metadata |
| Large: `U=64`, `R<=64` | Same transaction shape, no full-store scan | One write transaction, `O(R)` checks | Bounded by configured frontier budget |
| Admission maximum: `U=4096` | Bounded point reads and serialization; reject on byte/request budget before I/O amplification | One fenced write transaction or fail closed if the backend cannot hold it | Must remain below the existing frontier budget (1 MiB in #569) |

The estimates exclude body generation and canonical evidence hydration. Those
costs must be measured separately. A benchmark claiming constant work for a
changing root set is invalid; receipts must report `U`, `R`, point reads,
decoded bytes, fence checks and transaction count.

## Non-goals and sequencing

This Draft PR is a design/conformance gate only. It does not activate derived
retrieval, add a coordinator to the library, or merge an implementation. The
next implementation PR should be independently reviewed against this matrix;
only after that should compact scoring or multi-level retrieval be scheduled.
