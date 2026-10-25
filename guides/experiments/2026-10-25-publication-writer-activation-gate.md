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
explicitly recorded in the runbook and acceptance checklist.

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
`1e2806d2` (`#545` and `#546–#548`). The publication chain still has the old
base `4018fb12`:

| PR | Current head | Current base | Required action |
| --- | --- | --- | --- |
| #544 | `9cdba9b1a956353a98b9e8b4e9c83fdb1631330a` | `4018fb12` | guarded restack onto current `main`; inspect overlap with segmented lexical files |
| #549 | `f079224e754d08595005a8070a1f88c363d18b94` | #544 | restack after #544 and rerun exact-head CI |
| #550 | `89f898eea090ed074f44b72fb3c2567f7a61b218` | #549 | restack after #549 and rerun exact-head CI |
| #551 | `2b2246a620d6ed6020bfdfac3acd1eb9bc42c4c7` | #550 | preserve participant scope; rerun exact-head CI |
| #552 | `0d0c89140baa4f50affdcfe8f7d516a5ccb59d0c` | #551 | inspect projection/lexical overlap with segmented backend |
| #553 | `d5e82f731bdb0fb0f2d1673a95d7f414e45986d5` | #552 | preserve pinned-read scope; rerun exact-head CI |
| #554 | `6c1a2f523ed6f345e7a1f03018e1d3ad810d63b4` | #553 | apply the cross-context corrective commit, then rerun exact-head CI |

The chain is independent of the segmented backend at the source-contract
level, but #544/#552/#553 touch lexical publication concepts and must be
reviewed for DBI and terminology overlap after restack. No force-push should
be performed while another writer is changing `main`; one integration owner
must sequence the restack and keep every PR Draft.

Required checks after each restack are exact-head CI, MDBX Release CTest,
header self-sufficiency, documentation checks, and a clean mergeability
status. Merging is deliberately outside this research PR.

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
one adapter.
