# Foundation binary API inventory

Date: 2026-11-24. Descriptive audit, not a new format specification.
Source: `main@77a278bef338074227efa05365e83e967334f429`;
tree `00cbb85105cb57a5a455dbb0e380238edef74cfa`.
The [closure report](2026-11-foundation-closure.md) owns gate disposition.

## Coverage and interpretation

The [source ledger](2026-11-foundation-source-inventory.json) enumerates ALL
286 production files and their hashes, including files without strings. It
records all 2061 lexical `std::string`/`std::string_view` sites in 209 files,
with exact line and source text. This deliberately includes text/comments;
it is discovery evidence, not 2061 binary defects. The
[AST catalogue](2026-11-foundation-public-type-catalogue.json) covers installed
named types, including optional headers, using the existing extractor.
Graph discovery returned no matching indexed symbols; tracked source/rg was
used rather than trusting a graph from another worktree/revision.

The following index groups binary sites by owning symbol/format. Internal
reader/writer members and append/read helpers belong to their listed codec
family, not separate formats. Text fields, names, paths, diagnostics, JSON,
hex digests, UTF-8 token/source/query content and ordinary ID labels are RETAIN
unless explicitly identified as opaque/binary below. Exact sites, including
private helpers, remain in the ledger; this table supplies their semantic
decision and owner. A lexical scan cannot prove alias/macro-free semantic
completeness; no ABI or absence-of-all-defects claim follows from it.

Decisions: REPLACE = new direct byte path needed before P4-1; ADAPT = named
compatible boundary preserving bytes; RETAIN = correct text/specialized/legacy
contract; DEFER = independent byte-API refactor, with its owning workstream.
ADAPT does not mean that an implicit binary-string conversion is a good model
for new code. All disk bytes/digest recipes below stay unchanged in this gate.
Paths in the tables are relative to `src/agent_memory/` at the source SHA.

## Core codecs and binary domain fields

| Exact file(s), symbol family | Binary role / owner | Decision and next stage |
| --- | --- | --- |
| `domain/conversation_codec.hpp/.cpp`: serialize/deserialize_conversation_envelope, serialize/deserialize_conversation_entry; Writer, Reader, serialize_value | AMCE v1; conversation event contract. ByteBuffer/ByteView already; text adapters for textual fields | RETAIN. Nested Writer::external/read_external move to additive AMXR bytes in corrective PR |
| `domain/ConversationEnvelope.hpp`: ConversationOpaquePayload::bytes, ConversationMetadataExtension::bytes, tool call_id | Arbitrary bytes, already ByteBuffer; event owner | RETAIN; no binary-string migration needed |
| `storage/ConversationStore.cpp`: canonical ByteBuffer records; `ingestion/ConversationSourceImporter.cpp`: SourceKey::external, source_key, external budget helper | Canonical AMCE and AMXR source identity; store/import owner | RETAIN byte storage; REPLACE source_key string-to-vector staging with direct AMXR ByteBuffer |
| `domain/ExternalObjectRef.hpp/.cpp`: serialize_external_object_ref, deserialize_external_object_ref; put_uint/text/scalar/key, Reader, digest, operator< | Standalone AMXR v1; identity-policy | REPLACE for new consumers via additive `_bytes` functions; ADAPT old signatures unchanged. Shared encoder, no binary->string->binary staging |
| `domain/Sha256.hpp/.cpp`: Sha256Accumulator::update, sha256 | Arbitrary hash input in string_view, fixed byte-array output; SHA owner | ADAPT bounded zero-copy byte-to-char view at existing hash boundary. Independent additive hash API can follow; no changed digest preimage |
| `domain/KnowledgeUnit.cpp`: compute_chunk_content_hash, local canonical/append/append_be | Mixed length-framed text and binary digest/numbers; content recipe v2 owner | DEFER internal buffer migration to unit-codec integration; RETAIN exact existing preimage, no rehash/migration in this gate |
| `domain/TypedRepresentation.hpp/.cpp`: serialize_typed_locator, deserialize_typed_locator; put/get, put/get_u64/span/opt | TL1 locator contains binary u64, not plain text; typed-representation-locators | DEFER additive byte codec to locator integration; retain legacy bytes/embedding into bindings |
| `domain/Artifact.hpp`: ArtifactBindingId, SourceRevisionId; `domain/Resource.hpp`: DerivedRecordRef::key; `domain/Conversation.hpp`: source provider_revision_marker; `domain/KnowledgeUnit.hpp`: ScopeId | Opaque legacy bindings may contain NUL/non-UTF-8; containing owner retains exact bytes | ADAPT declared legacy fields, NOT blanket UTF-8 validation or reinterpretation. New private witness encodes exact bytes through ByteView; no public DTO break |
| `domain/Conversation.hpp/.cpp`: ConversationImportEvidence::record_locator, ConversationSourceBinding::adapter_recipe | Required UTF-8 text by EntryValidator::text, despite length-framed wire representation | RETAIN string. Do NOT treat all locator/recipe spellings as arbitrary bytes or pass TL1 binary without an explicit textual adapter |
| `storage/StorageIdentityMapping.hpp/.cpp`: encode_storage_identity_record, decode_storage_identity_record/document/block, StorageIdentityRecordCodec; encode_record/decode_record helpers | AMSI v1 framed binary; storage-identity-mapping | DEFER additive bytes to numeric integration. Existing mapping remains a bounded legacy interface; P4 compact keys do not depend on AMSI |
| `storage/IDerivedPublicationStore.hpp`, `storage/DerivedPublicationAdmission.hpp/.cpp`: DerivedPublicationRequest::dependency_frontier, DerivedPublicationUnitSnapshot::dependency_frontier, current_dependency_frontier, append_bounded/field | Legacy exact frontier binding, textual unit-local framing or binary root-frontier prefix depending on producer; linked-derived-knowledge | ADAPT public exact-byte token; DEFER cohesive byte-facing frontier API with snapshot/publication integration. body/source_digest remain declared text/hex |
| `storage/CanonicalFlatStorage.hpp/.cpp`: CanonicalFlatStorageCodec::encode/decode_block/structure, Reader::bytes, block_logical_bytes, structure_logical_bytes, digest_for, append_* | AMFB/AMFS records, checksums/digests; canonical-flat-storage-roadmap | DEFER byte codec to canonical numeric integration; preserve goldens/preimages and disk versions |
| `storage/FramedZstdCodec.hpp/.cpp`: FramedZstdEncoded/Decoded/Range::bytes; encode_framed_zstd, decode_framed_zstd, decode_framed_zstd_range, Reader/append helpers | Framed physical representation and decoded arbitrary blob bytes; raw-blob-storage/Framed Zstd owner | DEFER cohesive additive byte API to artifact storage integration; keep codec_id/diagnostics text. Partial range remains UnverifiedPartial |
| `storage/FileRawBlobStore.hpp/.cpp`: RawBlobReadResult::bytes, FileRawBlobStore::put/read_range/read_range_bounded, file stream buffers | Arbitrary CAS bodies; raw-blob-storage owner | DEFER coordinated raw/hydration byte API, not only one field replacement; keep paths and diagnostic strings text |
| `lexical/LexicalSegmentEncoding.hpp/.cpp`: LexicalSegmentEncodingResult::bytes, encode/decode_lexical_segment_row, Writer::m_bytes/take, Reader::m_bytes; lexical_posting_membership_witness::input | ALSR row and binary witness preimage; lexical-segment-physical-contract | DEFER additive byte row codec to lexical physical integration; retain checksum/golden/witness bytes |
| `ingestion/StructuralSegmentSet.cpp`: digest_bytes, build_structural_segment_set descriptor/preimage | Digest embedded in segment identity recipe; structural-segment-set | ADAPT internal hash boundary now; DEFER owned preimage ByteBuffer to segment recipe maintenance, preserving exact IDs |
| `retrieval/RevisionBoundRetrievalTrace.cpp`: query_material, append_u32_hex, hex_digest | Material is textual float hex/labels; trace owner | RETAIN text, not raw float bytes despite memcpy into integer before hex formatting |
| `index/BinarySignature.hpp/.cpp`, `embedding/embedding_types.hpp`: BinarySignature words, FloatEmbedding values | Bit-packed uint64 vectors / numerical float vectors; index/embedding owner | RETAIN specialized representations; do not convert to string OR flatten to ByteBuffer indiscriminately |
| `index/*BinaryEncoder.cpp/.hpp`, `eval/AutoencoderBinaryArtifact.cpp`, `eval/PrecomputedEmbeddingDataset.cpp` | Numerical artifacts use byte/float vectors, streams and JSON/text descriptors; evaluation/index owners | RETAIN specialized vectors and JSON/hex labels. Research artifact headers/weights are not a production Conversation format |

## MDBX infrastructure and hydration

All `mdbxc::KeyValueTable<..., std::string>` value bindings, bytewise
`KeyValueTable<std::string,...>` keys, borrowed callback payloads and range
sentinels in these families are ADAPT infrastructure boundaries. A table name,
text marker or decimal counter is not thereby arbitrary binary. Private
binary-string codecs are DEFER to the named owner unless separately marked.
They must NOT be copied as a new Phase 4 core codec design.

| Exact file family, binary symbols | Owner / decision and integration stage |
| --- | --- |
| `MdbxArtifactCatalog.hpp/.cpp`: ArtifactCatalogIngestRequest::body, ArtifactCatalogReadResult::bytes; Writer::m_value/finish, Reader; revision_prefix/key, encode_artifact/binding/intent/receipt, Impl tables, hydration buffers | Artifact provenance/catalog. DEFER body/hydration byte DTOs with FileRawBlobStore; ADAPT existing tables and binary catalog keys |
| `MdbxVerifiedArtifactHydration.hpp/.cpp`: VerifiedArtifactMaterializationResult::bytes, hydration copies | verified-artifact-hydration. DEFER coordinated raw result API; retain diagnostic/source labels text, verification before authoritative output |
| `MdbxAuthoritativeArticleHydration.hpp/.cpp`: MdbxAuthoritativeArticleHydrationResult::bytes, body result propagation | kb0-revision-bound-retrieval. DEFER with verified hydration; no hidden text-only assumption in generic bytes result |
| `MdbxCanonicalContentStore.hpp/.cpp`: publication-guard key/expected_value/expected_token; key, Reader::view/bytes/raw_bytes/m_payload, encode_revision/logical_body/body_payload/ledger, EncodedBody::payload; Impl heads/revisions/bodies/ledgers/guards | canonical-content-storage-roadmap. DEFER private C1/body codecs with additive byte API; ADAPT public guard legacy bytes, tables; preserve raw/Zstd logical identity |
| `MdbxCanonicalFlatStorage.hpp/.cpp`: canonical_flat_composite_key, EnvelopeReader::bytes/take, envelope_header/open_envelope/seal, append_digest; Impl::find/put and tables | canonical-flat-storage-roadmap. ADAPT BE64x2 key/table contract; DEFER private envelope codec API with flat integration. Numeric key bytes are already correct |
| `MdbxKnowledgeUnitStore.hpp/.cpp`: encode_envelope_payload/decode_envelope_payload, PayloadReader::m_payload, serialize/deserialize_envelope, content_key/index_key/index_entry_key/lower_bound/upper_bound, Impl::m_units/m_content_keys/m_kind_index/guards | knowledge-units-roadmap. DEFER byte payload API; ADAPT legacy physical indexes. `id_key` is decimal TEXT, not binary/numeric product integration |
| `MdbxKnowledgeUnitNumericKeyPilot.hpp/.cpp`: put(payload), find output; records/used_ids/metadata | numeric pilot owner. ADAPT existing arbitrary opaque payload API and uint64 tables; DEFER new byte-facing overloads to numeric integration, no default-path switch |
| `MdbxKnowledgeUnitTypedStore.cpp`: content_key, index_prefix/row_key, decode_index_id, append_u16/u64_be; Impl::records/used_ids/content_keys/kind_index | typed numeric owner. ADAPT compact keys/table payload from shared unit codec; DEFER owned bytes with unit-codec integration, preserve integer comparator |
| `MdbxStorageIdentityMapping.cpp`: logical/reverse keys, AMSI record payloads, records/document_reverse/block_reverse/metadata | storage-identity-mapping. ADAPT uint64 reverse tables and legacy codec boundary; metadata decimal next values are text, not decimal physical row IDs |
| `MdbxDerivedPublicationStore.hpp/.cpp`: serialize_manifest/marker, read_string/payload helpers, base_key/field_key, table | linked-derived-knowledge. DEFER private binary record codecs; ADAPT table keys. `body` remains declared publication text, digest_token is hex text |
| `MdbxProjectionStore.cpp`: serialize/deserialize, projection_prefix/key, read_string, projections table | projection-aware-lexical-retrieval. DEFER record byte API; ADAPT binary key and table boundary |
| `MdbxDocumentStorage.cpp`: serialize_document/chunk/string_list, Reader::m_payload, document/chunk/list tables | document-storage. DEFER legacy codecs with document integration; ADAPT tables. Lists here represent ordered membership/association, not a new global identity allocator |
| `MdbxResourceManifestStorage.hpp/.cpp`: publication guard key/expected_value/token; serialize_manifest, PayloadReader::m_payload; manifests/guards | resource-reindexing. DEFER binary manifest codec; ADAPT legacy guard bytes and tables |
| `MdbxResourceIndexRecordOwnerStorage.cpp`: serialize_owner, PayloadReader::m_payload, m_documents/m_chunks | resource-reindexing. DEFER private codec; ADAPT table boundary |
| `MdbxResourceArtifactBindingStore.cpp`: Writer::bytes, Reader::m_value, key_for, encode/decode, table | resource-artifact-binding owner. DEFER codec; ADAPT revision key/table boundary |
| `MdbxResourceImporter.cpp`: PendingImport, serialize_intent, fencing_token, payload/token fields, make_owner_id, reader payload, m_intents/m_tokens/m_bindings/m_epochs | resource-publication owner. ADAPT fixed 32-byte binary fencing token and publication guards (not text/UUID parsing); DEFER private preimage/intent codec to importer integration |
| `MdbxDurableLexicalDocumentMemory.hpp/.cpp`: guard key/expected_value/token; serialize_entry/pending, PayloadReader, catalog keys/values | durable-lexical-document-memory. ADAPT guard/table boundary; DEFER private codecs. block_text/source_uri remain text |
| `MdbxPersistentLexicalIndex.cpp`: serialize_state, PayloadReader::m_payload, state_key/legacy_state_key, Impl table/key | persistent-lexical-index-foundation. DEFER state codec; ADAPT existing keys/table, retain hex-component namespace recipe |
| `MdbxSegmentedLexicalIndex.cpp`: row_key, marker_prefix, serialize_partition_metadata/descriptor/unit/snapshot/collection_stats/token_stats/u64, Reader::m_bytes, membership witness raw string, table | segmented-lexical-mdbx-adapter. DEFER cohesive row codecs; ADAPT lexical row/witness storage. Text dictionary/token values remain text |
| `MdbxExactIndexGenerationStore.cpp`: bytes, digest, Reader, encode_descriptor/row, binding_for, key/required/completion/progress, table | index-lifecycle/exact-semantic owner. ADAPT private binary digest/binding/table values; DEFER byte codec maintenance; retain fixed float little-endian contract and equality witnesses |
| `MdbxPromptAssemblyProfileStorage.cpp`: Writer::m_bytes/finish, Reader::m_bytes, serialize_profile, profile_key, revision_value, history/heads | prompt-composition-roadmap. DEFER additive record-byte API; ADAPT keys/tables; profile IDs/template/source blocks are text, not blob bytes |
| `MdbxResourceFrontierSnapshot.hpp/.cpp`: identity_token, make_frontier_identity, Impl::identity, append_* | resource-frontier owner. ADAPT exact legacy binary frontier token; DEFER ByteBuffer identity token to dependent publication/retrieval API integration |
| `MdbxCurrentDerivedPublicationReader.cpp`, `MdbxRawMarkdownProductIngestion.cpp` digest_token/hex_bytes; `MdbxStorageContext.cpp`, workspace/router/federated/snapshot readers | RETAIN textual digests/prefixes/paths/diagnostics; borrowed existing opaque tokens ADAPT to their producer above. These are consumers, not independent binary formats |

MDBX paths in the table are under `infrastructure/mdbx/`. The source ledger
also covers non-codec `.hpp` declarations and private members that repeat these
types. Headers are not omitted because a feature is OFF in the default build.

## Admission rule for subsequent PRs

New public binary APIs and private Phase 4 codecs use ByteBuffer/ByteView (or
fixed arrays). Existing text/legacy APIs are not silently broken. A deferred
change must test null/empty/lifetime, embedded 00/FF, exact size/overflow, error
atomicity, golden parity and installed consumers of both old/new entry points.
API-only modernization does not authorize a disk migration or digest change.
There is no claim that vector is inherently faster than string; the confirmed
AMXR improvement is semantic typing and removal of source-key staging copies.
