# Publication writer activation gate

**Date:** 2026-10-25

**Base:** `main` at `1e2806d21bc36fa6baeca536aaccec0bf535502c`

**Research branch:** `research/publication-writer-activation-gate`

**Status:** Draft research; no coordinated profile activation and no merge performed

## Decision

The current repository can support a bounded, host-controlled coordinated
profile, but it cannot prove global writer admission. A durable marker or a
process-local mutex does not make an older binary consult the marker, and the
legacy stores below still expose direct write methods. The safe deployment
precondition is therefore external quiescence: stop all writers, migrate or
reopen every writer through one admitted profile, and only then enable
coordinated publication. This document deliberately does not add a
`WriterRegistry`, `ActivationManager`, or another production abstraction.

The result is **experimental-ready / activation-blocked / not
production-ready**.

## Writer-entry-point inventory

The matrix records the implementation currently reachable from `main`. “Fenced”
means the concrete method checks a durable guard in the same write transaction;
it does not mean that every caller is forced through that method.

| Entry point | Durable rows / DBI | Transaction owner | Supported admission | Fencing check | Bypass |
| --- | --- | --- | --- | --- | --- |
| `MdbxCanonicalContentStore::create_document`, `commit` | `canonical_heads`, `canonical_revisions`, `canonical_bodies`, `canonical_ledgers` | store opens and commits its own writable transaction | direct legacy profile | only `*_guarded` variants | ordinary methods are unfenced |
| `MdbxKnowledgeUnitStore::upsert`, `erase`, `create_or_get_unit*`, `update_mutable_fields` | `knowledge_units`, `knowledge_content_keys`, `knowledge_kind_index` | store-owned transaction | direct legacy profile | guarded single/batch create paths check token table | ordinary upsert/erase/update and unguarded batch |
| `MdbxProjectionStore::put`, `delete_revision` | `projections` | store-owned transaction | direct legacy profile | none | every public write method |
| `MdbxPersistentLexicalIndex::upsert`, `erase`, `clear` | `lexical_posting_segments` flat-v2 state row | store-owned transaction, or caller-owned transaction only for the internal adapter seam | lexical-only profile | no coordinator fence in this baseline | every public write method |
| `MdbxSegmentedLexicalIndex::upsert`, `erase`, `clear` | `lexical_posting_segments` segmented-v1 namespace rows | store-owned transaction | isolated segmented profile | no coordinator fence | every public write method |
| `MdbxResourceManifestStorage::upsert_manifest`, conditional variants, `erase_manifest` | `resource_manifests` | store-owned transaction | manifest profile | guarded conditional variant checks guard/token | unguarded upsert/erase |
| `MdbxResourceImporter::import` | `resource_import_fencing_schema`, `resource_import_intents`, `resource_import_fencing_tokens`, `resource_import_bindings`, `resource_import_epochs`, plus composed lexical/raw/manifest rows | importer-owned transactions; recovery reopens and replays intents | lexical-only and explicit raw/projection profiles | durable intent, owner lease, epoch, token, and profile checks | direct child stores and legacy APIs remain callable |
| `MdbxDocumentStorage::upsert_document`, `erase_document` | `documents`, `chunks`, `document_chunks` | legacy adapter-owned connection and transaction | legacy document profile | none | every public write method; no shared context option |
| `MdbxResourceIndexRecordOwnerStorage::upsert_document_owner`, `upsert_chunk_owner` | `resource_index_document_owners`, `resource_index_chunk_owners` | legacy adapter-owned connection and transaction | legacy ownership profile | none | every public write method |
| `MdbxDurableLexicalDocumentMemory::upsert_text*` | durable catalog, canonical child rows, lexical child rows, importer intent/fencing rows | facade-owned transactions; guarded overload propagates a durable guard | durable lexical profile | only `upsert_text_guarded` | unguarded `upsert_text` and direct children |
| `MdbxCanonicalContentRouter::commit` | selected workspace canonical tables | routed store owns transaction | workspace routing profile | follows selected store only | direct store access and legacy workspace objects |
| Publication coordinator/write scope (PR #544–#554, not in this `main`) | coordinator metadata plus canonical/unit/projection/lexical/manifest rows | coordinator-owned transaction | experimental coordinated profile | participant guards and profile/fencing checks in the PR stack | all legacy entry points above remain outside its admission boundary |

The coordinator row is intentionally marked “not in this `main`”: PR #545 and
#546–#548 are merged, while #544 and #549–#554 are still a separate stacked
Draft chain. The chain must not be described as a repository-wide writer gate.

## Threat-model results

| Scenario | What the current code proves | What it cannot prove |
| --- | --- | --- |
| Two cooperating writers in one process | MDBX serializes writable transactions; guarded methods reject stale durable tokens | that an unguarded writer will register or stop |
| Two cooperating writers in different processes | MDBX transaction serialization and durable token comparison | process-wide admission or discovery of all writer binaries |
| Old binary unaware of the profile marker | nothing; old methods continue to write | global exclusion of legacy binaries |
| Writer holding an old transaction at activation | MDBX transaction lifetime rules apply | quiescence and draining of an already-running writer |
| Stale owner after takeover | importer intent lease/epoch/token checks reject stale recovery | protection for unrelated direct stores |
| Crash during activation | importer intent recovery is bounded and testable | an atomic transition across every independent adapter |
| Writer starts after activation | no admission hook is installed in legacy constructors | preventing a new legacy process from opening the DB |
| Direct MDBX mutation through a legacy adapter | legacy writes remain available | detection or fencing of arbitrary old APIs |
| Rollback and retry | individual guarded transactions are atomic/idempotent where tested | one global rollback protocol across profiles |

## Feasibility boundary

The strongest implementable guarantee without changing every writer API is
host-controlled registration plus durable fencing for the participating
profile. A marker-only design is insufficient: an old executable does not read
it. A process-local mutex is also insufficient across processes. A complete
global gate would require one of the following, none of which is implemented
here:

1. every writable constructor and method accepts a shared admission capability;
2. a host-owned writer registry controls process startup and drains old owners;
3. all legacy write paths are removed or made unreachable before activation;
4. deployment guarantees external stop/restart/quiescence for every writer.

The fourth option is the only currently supportable deployment rule. It must be
combined with enforced writer ownership for the entire active period: after
activation, the deployment owner must continuously control which binaries may
open the environment for writing. A one-time stop/start is not sufficient if
an old binary can be launched again later. This requirement must be recorded in
the runbook and acceptance checklist.

## Deployment-profile guarantees

| Deployment profile | Quiescence at activation | Ownership after activation | Achievable guarantee | Required external mechanism |
| --- | --- | --- | --- | --- |
| Single-process host controlled by one owner | Stop all writers and drain/abort open write transactions before activation | Host keeps the only process admission path and prevents legacy restart | Bounded coordinated profile can be made operationally safe | Supervisor-owned process lifecycle and restart policy |
| Controlled multi-process host | Stop and drain every process before activation | Supervisor or service owner authorizes every later writer start | Coordinated writers can be restricted to one profile; legacy bypass is deployment-fenced | Service manager policy, binary allow-list, and writer ownership lease |
| Unmanaged legacy processes | Cannot prove quiescence or drain | Cannot prevent a legacy binary from reopening and writing later | No safe activation guarantee; remain activation-blocked | External owner must first establish process and binary control |

MDBX transaction serialization is a separate property from both columns. It
orders transactions that reach MDBX; it does not establish quiescence and does
not grant or revoke writer ownership.

## Minimal executable pilot

No pilot is claimed in this research PR. A test that substitutes cooperative
in-process participants would only test the coordinator, not an unaware old
binary. A valid future pilot must use two processes or two independently
opened environments and must exercise:

1. legacy writer before activation;
2. externally quiesced transition;
3. admitted coordinated writer;
4. stale writer after activation (must be rejected or prevented from running);
5. fresh read and frontier verification;
6. crash/restart at each activation boundary.

If the stale process cannot be controlled by the deployment harness, the
correct result is “activation unsupported”, not a simulated success.

## Integration plan for PR #544–#554

The current `main` includes the merged lexical physical/segmented line through
`1e2806d2`. The publication chain is now based on that `main` after the
guarded restack:

| PR | Current head | Current base | Required action |
| --- | --- | --- | --- |
| #544 | `b7fff7002efd412d3e2a2630f160a86ac6303fbc` | `main@1e2806d2` | restacked; preserve plan conformance and rerun exact-head CI |
| #549 | `be4c11f5459640d25bf382797b2b852d83faba72` | #544@b7fff700 | restacked; rerun exact-head CI |
| #550 | `45c870c45e5ce20526e62e3ffd277c576cabe76c` | #549@be4c11f5 | restacked; preserve DBI/schema scope and rerun exact-head CI |
| #551 | `38c7473e1f2b2c5e4a76172e2b052b4cc33f27e9` | #550@45c870c4 | restacked; preserve participant scope and rerun exact-head CI |
| #552 | `e64630ab7ca0523bf1bf26e3fd5d6bbd41f3cdcf` | #551@38c7473e | restacked; rerun exact-head CI |
| #553 | `18548d1fa65b25c7906e50214712199b9cac0aeb` | #552@e64630ab | restacked; preserve pinned-read scope and rerun exact-head CI |
| #554 | `7fc8f7ab2679f11eb9b73c653ceba448526eaf61` | #553@18548d1f | restacked; rerun exact-head CI |

The chain is independent of the segmented backend at the source-contract
level, but #544/#552/#553 touch lexical publication concepts and must be
reviewed for DBI and terminology overlap after restack. No force-push should
be performed while another writer is changing `main`; one integration owner
must sequence the restack and keep every PR Draft.

The only permitted squash sequence is:

`#544 → #549 → #550 → #551 → #552 → #553 → #554`

After each squash, retarget the next Draft PR to the new `main`, verify that
its base contains exactly the prior squash, and run exact-head CI before
continuing. #555 remains an independent documentation research PR and is not
part of the functional publication chain.

### Integration readiness snapshot (2026-10-26)

This is a read-only snapshot. The listed CI runs are exact-head runs for the
current remote heads; they do not replace the CI required after restack.

| PR | HEAD | Base | CI | Conflicts now | Ready for guarded restack | Blocker |
| --- | --- | --- | --- | --- | --- | --- |
| #544 | `b7fff700` | `main@1e2806d2` | `40265763503` SUCCESS | none | yes | ready for guarded squash |
| #549 | `be4c11f5` | `#544@b7fff700` | `40265811788` SUCCESS | none | yes | ready for guarded squash |
| #550 | `45c870c4` | `#549@be4c11f5` | `40265840967` SUCCESS | none | yes | ready for guarded squash |
| #551 | `38c7473e` | `#550@45c870c4` | `40265877101` SUCCESS | none | yes | ready for guarded squash |
| #552 | `e64630ab` | `#551@38c7473e` | `40265928511` SUCCESS | none | yes | ready for guarded squash |
| #553 | `18548d1f` | `#552@e64630ab` | `40265958122` SUCCESS | none | yes | ready for guarded squash |
| #554 | `7fc8f7ab` | `#553@18548d1f` | `40266029291` SUCCESS | none | yes | ready for guarded squash |
| #555 | `642e8f51` | `main@1e2806d2` | `40267332754` SUCCESS | none | independent | research-only; not in functional chain |
| #556 | `bfe90484` | `main@1e2806d2` | `40261838499` SUCCESS | none | no | segmented-v2 research; keep outside coordinator stack |

The current remote `main` is `1e2806d21bc36fa6baeca536aaccec0bf535502c`.
The simulated merges above are evidence that Git can construct a clean tree;
they are not a substitute for resolving and testing each scoped diff on its
new base. PR #556 belongs to the first agent's segmented-v2 line and must not
be retargeted, restacked, or edited as part of this coordinator handoff.

Required checks after each restack are exact-head CI, MDBX Release CTest,
header self-sufficiency, documentation checks, and a clean mergeability
status. Merging is deliberately outside this research PR.

### Conflict and overlap audit

The guarded restack must inspect these files and boundaries explicitly:

| Surface | Expected overlap | Resolution rule |
| --- | --- | --- |
| `CMakeLists.txt`, `tests/CMakeLists.txt` | #544–#554 add coordinator test sources; #545–#548 add segmented sources and tests | retain both registrations; do not duplicate a source or target |
| `guides/dbi-manifest.yaml` and MDBX table names | segmented-v1 and flat-v2 both use lexical physical storage names with different row namespaces | preserve the merged physical-segment contract; coordinator remains independent and must not relabel segmented rows |
| lexical APIs and descriptors | #552/#553 refer to projection/lexical publication; #545–#548 define segmented codec and adapter APIs | keep coordinator participant contracts on the existing persistent lexical baseline; no implicit segmented-v1 integration |
| `MdbxPublicationReadScope` and `MdbxSegmentedLexicalIndex` | no direct source dependency is expected | if a conflict appears, retain the coordinator's bounded target identity contract and the segmented adapter's explicit lexical-only boundary |
| experiment notes | both lines record independent research | keep separate notes; link the integration decision instead of copying commits or claims |

The scoped diff audit must also verify that each PR contributes only its
declared layer: plan conformance, transaction ownership, metadata coordinator,
authoritative participants, projection/lexical participants, pinned reads, and
the corrective boundary tests. A commit that merely reintroduces an ancestor's
files is a restack error, not a harmless duplicate.

## Activation preconditions

Before any coordinated profile is enabled in deployment:

- identify and stop every process using the database;
- verify no writable transaction remains open;
- migrate legacy fencing state where required, offline;
- start only binaries that use the admitted profile;
- retain an external rollback plan that stops and restarts all writers;
- verify a fresh pinned read and frontier after activation;
- treat any unknown writer binary as an activation blocker.

## Conclusion

The coordinator stack is a useful bounded experiment and can be integrated as
an experimental baseline after its guarded restack. It is not a global writer
admission mechanism. The missing property is operational and architectural,
not something that can be repaired by adding another marker or wrapper around
one adapter. This research PR adds zero production LOC; its 186 lines are an
auditable deployment and integration record.
