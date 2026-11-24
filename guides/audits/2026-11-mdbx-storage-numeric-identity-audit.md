# MDBX physical storage and numeric identity audit

**Audit snapshot:** `bfa01a22` (`feat: add opt-in flat canonical codecs`)

**Status:** source and test inventory. This document does not change a DBI,
codec, comparator flag, or runtime default. It records the evidence needed by
the storage architecture ADR and the disposition of historical evidence after
the numeric-identity work is integrated.

The physical manifest remains
[`guides/dbi-manifest.yaml`](../dbi-manifest.yaml), and the upstream/downstream
boundary remains [`guides/mdbx-stack-boundaries.md`](../mdbx-stack-boundaries.md).
The opt-in `StorageIdentityMapping` pilot is described in
[`guides/storage-identity-mapping.md`](../storage-identity-mapping.md). The
pilot is a backend-neutral codec and state machine; it is not yet a persistent
replacement for every textual key in the MDBX adapters.

## Executive result

For the `main@77a278bef338` status of integrated opt-in numeric/typed/mapping/flat
paths versus unchanged legacy product defaults, use the
[foundation register](2026-11-foundation-closure.md#3-identity-closure-register-at-main).
This earlier audit retains its source binding; it is not a migration receipt.

The repository has three different things that are easy to call a “string
key”, but must not be conflated:

1. a textual logical identity such as `ResourceId` or an external URI;
2. a `std::string` container holding canonical binary key bytes; and
3. a decimal rendering of an ID that is already numeric.

Only the third case is an unconditional numeric-key candidate. The audit found
it in `MdbxKnowledgeUnitStore` and in several rebuildable lexical rows. The
first case remains textual unless a measured, persistent identity dictionary
is adopted. The second case is already a binary key codec and must be changed
only with an ordering and compatibility proof.

The accepted direction is therefore **typed domain DTO → explicit mapper →
versioned record/key codec → MDBX**, with the transaction owner supplying the
ID mapping and commit boundary. A blanket replacement of
`KeyValueTable<std::string, std::string>` is not an architectural objective.

## Evidence and method

The inventory was made against the source and tests at `bfa01a22`:

- adapters under `src/agent_memory/infrastructure/mdbx/`;
- storage contracts and codecs under `src/agent_memory/storage/`;
- `guides/dbi-manifest.yaml` and
  [`guides/mdbx-containers-extension-tz.md`](../mdbx-containers-extension-tz.md);
- focused MDBX and storage tests under `tests/infrastructure/mdbx/` and
  `tests/storage/`.

“Implemented” below means that the named source path and a focused executable
test exist. It does not mean that a migration has been executed. “Static” is
source inspection; “test source” means that the fixture was inspected rather
than rerun in this documentation change.

## Physical DBI and codec matrix

The disposition column is normative for the numeric-identity migration. A
changed key layout must use a new physical schema/keyspace or an explicit
offline migration; comparator flags are never changed in place.

| DBI/keyspace and owner | Current physical form | Evidence path | Post-numeric disposition | Status |
|---|---|---|---|---|
| `knowledge_units` — `MdbxKnowledgeUnitStore` | `KnowledgeUnitId` is rendered with `std::to_string`; envelope is a versioned length-prefixed payload | `MdbxKnowledgeUnitStore.cpp`, `mdbx_knowledge_unit_store_test.cpp` | **Migration.** Export logical envelopes, allocate/restore stable numeric IDs, write a new schema/keyspace, verify counts/digests, then reopen. Old textual rows are not reinterpreted as numeric rows. | Implemented pilot path; migration not implemented |
| `content_key_to_unit_id` — knowledge-unit store | Content key maps to a decimal ID string | `MdbxKnowledgeUnitStore.cpp` | **Rebuild** from migrated `knowledge_units` in the same unit of work. A missing or duplicate reverse mapping is an error, not a cache miss. | Implemented derived mapping |
| `knowledge_units_by_kind` — knowledge-unit store | Scope/kind/index rows contain decimal ID values and versioned index markers | `MdbxKnowledgeUnitStore.cpp` | **Rebuild** after the primary migration. Preserve monotonic ordering and reject a schema marker that is not explicitly supported. | Implemented derived index |
| `agent_memory_canonical_*` — `MdbxCanonicalContentStore` | Document/revision/body keys are delimiter-separated textual IDs and decimal revisions; payloads have explicit C1 versions | `MdbxCanonicalContentStore.cpp`, `mdbx_canonical_content_store_test.cpp` | **Migration.** Canonical history and no-reuse ledgers are retained. A new key codec may use numeric surrogates, but export/import must preserve logical document IDs, revisions, digests and ledger history. | Implemented C1 profile |
| `agent_memory_unit_projections` — `MdbxProjectionStore` | Versioned composite binary key carried in a `std::string`; projection payload is versioned | `MdbxProjectionStore.cpp`, `mdbx_projection_store_test.cpp` | **Rebuild** from canonical units after a primary migration. Keep bytewise ordering rules; do not replace the codec merely because the C++ container is `std::string`. | Implemented derived profile |
| `agent_memory_unit_projections` derived key family — `MdbxDerivedPublicationStore` | Length-prefixed publication body/manifest/active-marker rows in the shared DBI | `MdbxDerivedPublicationStore.cpp`, `mdbx_shared_unit_projections_conformance_test.cpp` | **Rebuild** from canonical inputs. Preserve the tested shared-key-family boundary; a future split requires a separate schema migration and ownership proof. | Implemented experimental profile |
| `agent_memory_resource_manifests` — `MdbxResourceManifestStorage` | Textual `ResourceId` key; payload decoder accepts v1–v6 and writes the current version | `MdbxResourceManifestStorage.cpp`, `mdbx_resource_manifest_storage_test.cpp` | **Retained legacy reader + explicit upgrade.** `ResourceId` remains a logical external/text identity. Upgrade payloads to the current version only through the existing decoder; unknown versions fail closed. | Implemented profile |
| `agent_memory_documents`, `chunks`, `document_chunks` — `MdbxDocumentStorage` | Textual IDs and length-prefixed document/chunk/list payloads | `MdbxDocumentStorage.cpp`, `mdbx_document_storage_test.cpp` | **Retained legacy reader.** This adapter is not the KB-0 canonical path. Do not silently reinterpret its tables as the normalized knowledge-unit schema. | Implemented legacy profile |
| `agent_memory_resource_index_*_owners` — ownership adapter | Textual physical IDs; generation and schema numbers are decimal fields in a framed payload | `MdbxResourceIndexRecordOwnerStorage.cpp` | **Rebuild** when the owning resource/index profile is rebuilt; otherwise retain the legacy reader. No standalone numeric migration is justified without a measured hot path. | Implemented legacy profile |
| `agent_memory_artifact_*` — `MdbxArtifactCatalog` | Textual artifact/binding IDs with versioned records and a schema marker | `MdbxArtifactCatalog.cpp`, `mdbx_artifact_catalog_test.cpp` | **Migration of canonical catalog records** if an internal artifact identity becomes numeric; raw CAS objects and SHA-256 digests are retained byte-for-byte. Derived receipts may be rebuilt. | Implemented provenance profile |
| `agent_memory_resource_import_*` — importer fencing/catalog | Resource IDs remain textual; fencing epochs and hashes are framed numeric fields; intent payloads have v3/v4 generations | `MdbxResourceImporter.cpp`, `mdbx_resource_importer_test.cpp` | **Retained legacy reader + explicit upgrade** for supported intent generations. Mapping allocation and fencing updates remain in the caller-owned transaction; no codec-owned write transaction. | Implemented profile |
| `agent_memory_durable_lexical_catalog` — durable lexical memory | Scope/resource keys are textual; catalogue and pending records are versioned | `MdbxDurableLexicalDocumentMemory.cpp`, `mdbx_durable_lexical_document_memory_test.cpp` | **Reject old unbound layouts; rebuild derived entries** from canonical units and projections. Existing negative legacy fixtures remain negative controls. | Implemented profile |
| `agent_memory_lexical_posting_segments` — flat/segmented lexical adapters | Versioned KV payloads and decimal token/unit/segment suffixes; layout identity is part of the descriptor | `MdbxPersistentLexicalIndex.cpp`, `MdbxSegmentedLexicalIndex.cpp`, segmented conformance tests | **Rebuild** for a selected layout. Old flat-v2/seg-v2 rows are experimental evidence, not silently upgraded into a different layout. Unknown layout/schema is rejected. | Implemented experiment |
| `agent_memory_prompt_profiles` — prompt profile history | Textual profile ID + revision history key, versioned profile payload and head row | `MdbxPromptAssemblyProfileStorage.cpp`, `mdbx_prompt_assembly_profile_storage_test.cpp` | **Retained textual key codec.** Profile IDs are caller-visible logical names; migrate only if a separate identity dictionary proves useful. Historical revisions remain immutable. | Implemented profile |
| `global_unit_id_to_local_id` / `StorageIdentityMapping` | Explicit numeric mapping records; AMSI v1 binary codec; allocation starts at one and never reuses | `StorageIdentityMapping.cpp`, `storage_identity_mapping_test.cpp`, `guides/storage-identity-mapping.md` | **Canonical pilot for internal identity.** Integrate only through a storage unit of work, with atomic primary/index publication and snapshot-consistent reverse resolution. | Implemented backend-neutral pilot |

## Numeric identity rules

The following rules apply to any future persistent mapping:

- public/domain DTOs do not contain an MDBX connection, codec object or
  dictionary pointer;
- logical identity and physical identity are separate collision domains;
- zero is reserved, allocation is monotonic, and IDs are never reused while
  retained history can reference them;
- `lookup` and `resolve` are read-only; `intern` is write-transaction scoped;
- primary records, reverse mappings and secondary indexes commit or abort in
  one MDBX environment transaction;
- imported/exported records carry an explicit remapping table; a numeric value
  from another database is never assumed to be local;
- two read snapshots are not mixed when they could expose a primary row and a
  dictionary row from different revisions.

For a single `uint32_t`/`uint64_t` key, `MDBX_INTEGERKEY` may be used when the
upstream table contract supports it. For a bytewise composite key, use the
existing canonical composite codec and document its sort order. Native integer
keys and big-endian bytewise components are separate cases; applying one rule
to both would make range order an accident of the machine representation.

## Fixture, test and evidence disposition

The migration must preserve the distinction between an old fixture and a new
fixture. Existing bytes are evidence of the old contract and must not be
silently regenerated.

| Artifact class | Current examples | Required disposition after numeric migration |
|---|---|---|
| Canonical golden bytes | `mdbx_knowledge_unit_store_test.cpp` legacy rows; canonical C1 corruption/digest fixtures; `storage_identity_mapping_test.cpp` AMSI v1 bytes | Keep old bytes under an `old`/legacy label, add a new numeric-key fixture with a stable digest, and test old→new export/import plus new reopen. |
| Reopen tests | Knowledge-unit, canonical-content, projection/publication, prompt-profile, importer, artifact-catalog and lexical reopen cases | Keep semantic assertions; add a migrated-database reopen step. A passing reopen on a freshly created new DB is not migration evidence. |
| Upgrade/legacy tests | Resource manifest v1–v6; importer fencing v3/v4; legacy knowledge-unit/index fixtures; durable lexical legacy catalog | Keep supported decoders as explicit retained-reader tests. Unsupported or unbound layouts must assert typed rejection (`MigrationRequired`/`UnsupportedSchema`), not `NotFound`. |
| Rebuildable index fixtures | `content_key_to_unit_id`, kind index, projections, lexical flat/seg rows, reverse indexes | Recreate from migrated canonical records and compare logical rows, order, generation and scope. Old index bytes are disposable after the rebuild receipt. |
| Historical commit-based experiments | Storage layout and lexical benchmark notes under `guides/experiments/` and receipts tied to old commits | Freeze the commit, layout, dependency pins and workload. Mark the result historical when physical key cost or reopen timing changes; do not combine old and new numbers. Rerun only a named comparison. |
| Technical preview documents | `technical-preview-preflight.md`, `markdown-technical-preview*.md`, `first-release-profile.md` and release decision reports | Preserve the old exact-commit snapshot. After a numeric migration changes the preview path, generate a new exact-head preflight; until then mark the old preview as historical/not qualified for the new schema. |
| Unrelated model/evaluation fixtures | `tests/eval/fixtures/*` and embedding research receipts | No change unless the fixture embeds a storage-layout claim. Do not regenerate numerical retrieval evidence merely because MDBX keys changed. |

## Migration acceptance gate

The numeric pilot is not ready to become a default until one focused migration
run records all of the following:

1. old schema detection and an explicit `MigrationRequired`/upgrade decision;
2. export of every canonical logical identity, immutable revision, digest and
   retained ledger entry;
3. atomic allocation/import of mappings and primary records, with no ID reuse;
4. rebuilt reverse/secondary indexes with scope and ordering checks;
5. old and new fixture digests, record counts and negative-corruption results;
6. close/reopen of the migrated environment and a second read snapshot; and
7. a regenerated technical-preview/release matrix tied to the exact commit.

The migration must leave the old database available as a recovery artifact until
the verification receipt is accepted. A green unit-test run on only newly
created data does not close this gate.

## Not established by this audit

This audit does not claim that MDBX integer-key tables, a persistent dictionary
wired into every adapter, crash injection across external CAS, or physical
random access to the current monolithic canonical body already exist. Those are
separate implementation slices. The present evidence establishes the
allocation/codec pilot and the boundaries for completing them without changing
logical identity or provenance semantics.
