#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <locale>
#include <stdexcept>
#include <string>
#include <sstream>
#include <vector>

namespace {

    void expect(bool condition, const char* message) {
        if(!condition) throw std::runtime_error(message);
    }

    std::filesystem::path test_path() {
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        return std::filesystem::temp_directory_path() /
            ("agent_memory_m0_knowledge_units_" + std::to_string(nonce) + ".mdbx");
    }

    agent_memory::ResourceBodyDigest make_body_digest(std::uint8_t seed) {
        agent_memory::ResourceBodyDigest digest;
        for(std::size_t index = 0; index < digest.bytes.size(); ++index) {
            digest.bytes[index] = static_cast<std::uint8_t>(seed + index);
        }
        return digest;
    }

    agent_memory::SourceRefSummary make_source(
        const agent_memory::ResourceId& resource_id,
        std::uint64_t generation,
        std::uint8_t digest_seed
    ) {
        agent_memory::SourceRefSummary summary;
        summary.resource_id = resource_id;
        summary.resource_revision = agent_memory::ResourceRevisionRef{
            resource_id,
            generation,
            make_body_digest(digest_seed)
        };
        summary.uri = "https://example.test/source#section";
        summary.excerpt = agent_memory::TextRange{4, 12};
        summary.confidence = 0.9;
        summary.preview = "durable source preview";
        summary.quote_hash = agent_memory::compute_quote_hash(summary.preview);
        return summary;
    }

    agent_memory::KnowledgeUnitCreateRequest make_request(
        std::string scope,
        std::uint8_t content_seed,
        std::string display
    ) {
        agent_memory::KnowledgeUnitCreateRequest request;
        request.kind = agent_memory::KnowledgeUnitKind::Chunk;
        request.scope_id = agent_memory::ScopeId{std::move(scope)};
        request.primary_text = "The durable lexical memory slice.";
        request.display_text = std::move(display);
        request.content_hash_recipe_version = 2;
        request.sources.push_back(make_source(
            agent_memory::ResourceId{"resource:manual"},
            7,
            content_seed
        ));
        request.created_at_ms = 100;
        request.observed_at_ms = 120;
        request.priority_weight = 0.5;
        request.content_hash = agent_memory::compute_chunk_content_hash(
            request.scope_id, request.primary_text, request.sources,
            request.content_hash_recipe_version
        );
        return request;
    }

    agent_memory::KnowledgeUnitCreateRequest make_request_for_source(
        std::string scope,
        agent_memory::ResourceId resource_id,
        std::uint64_t generation,
        std::uint8_t content_seed,
        std::string display
    ) {
        agent_memory::KnowledgeUnitCreateRequest request;
        request.kind = agent_memory::KnowledgeUnitKind::Chunk;
        request.scope_id = agent_memory::ScopeId{std::move(scope)};
        request.primary_text = "The durable lexical memory slice.";
        request.display_text = std::move(display);
        request.content_hash_recipe_version = 2;
        request.sources.push_back(make_source(
            resource_id,
            generation,
            content_seed
        ));
        request.created_at_ms = 100;
        request.observed_at_ms = 120;
        request.priority_weight = 0.5;
        request.content_hash = agent_memory::compute_chunk_content_hash(
            request.scope_id, request.primary_text, request.sources,
            request.content_hash_recipe_version
        );
        return request;
    }

    agent_memory::KnowledgeUnitEnvelope make_envelope(
        std::string scope,
        std::uint8_t content_seed,
        std::uint64_t id,
        std::string display
    ) {
        auto request = make_request(std::move(scope), content_seed, std::move(display));
        agent_memory::KnowledgeUnitEnvelope envelope;
        envelope.id = agent_memory::KnowledgeUnitId{id};
        envelope.kind = request.kind;
        envelope.scope_id = std::move(request.scope_id);
        envelope.primary_text = std::move(request.primary_text);
        envelope.display_text = std::move(request.display_text);
        envelope.sources = std::move(request.sources);
        envelope.created_at_ms = request.created_at_ms;
        envelope.updated_at_ms = request.created_at_ms;
        envelope.observed_at_ms = request.observed_at_ms;
        envelope.revision = 1;
        envelope.content_hash = *request.content_hash;
        envelope.content_hash_recipe_version = request.content_hash_recipe_version;
        envelope.priority_weight = request.priority_weight;
        return envelope;
    }

    std::string encode_legacy_index(const std::vector<std::uint64_t>& ids) {
        std::string payload;
        const auto append = [&payload](std::string_view value) {
            payload += std::to_string(value.size());
            payload.push_back(':');
            payload.append(value.data(), value.size());
        };
        append(std::to_string(ids.size()));
        for(const auto id : ids) append(std::to_string(id));
        return payload;
    }

    std::string legacy_index_key(
        std::string_view scope,
        std::string_view kind
    ) {
        std::string key;
        const auto append = [&key](std::string_view value) {
            key += std::to_string(value.size());
            key.push_back(':');
            key.append(value.data(), value.size());
        };
        append(scope);
        append(kind);
        return key;
    }

    std::string serialize_legacy_envelope(
        const agent_memory::KnowledgeUnitEnvelope& envelope
    ) {
        std::string payload;
        const auto append = [&payload](std::string_view value) {
            payload += std::to_string(value.size());
            payload.push_back(':');
            payload.append(value.data(), value.size());
        };
        const auto append_uint = [&append](std::uint64_t value) {
            append(std::to_string(value));
        };
        const auto append_int = [&append](std::int64_t value) {
            append(std::to_string(value));
        };
        const auto append_bytes = [&append](const std::uint8_t* bytes, std::size_t size) {
            append(std::string_view{reinterpret_cast<const char*>(bytes), size});
        };
        append("agent_memory.knowledge_unit.v1");
        append_uint(envelope.id.value());
        append(agent_memory::to_string(envelope.kind));
        append(envelope.scope_id.value());
        append(envelope.primary_text);
        append(envelope.display_text);
        append("active");
        append_uint(envelope.sources.size());
        for(const auto& source : envelope.sources) {
            append(source.resource_id.value());
            append("revision_bound");
            append("1");
            append(source.resource_revision->resource_id.value());
            append_uint(source.resource_revision->generation);
            append("sha256");
            append_bytes(
                source.resource_revision->body_digest.bytes.data(),
                source.resource_revision->body_digest.bytes.size()
            );
            append(source.uri);
            append_uint(source.excerpt.offset);
            append_uint(source.excerpt.length);
            append_bytes(source.quote_hash.data(), source.quote_hash.size());
            std::ostringstream confidence;
            confidence.imbue(std::locale::classic());
            confidence << std::setprecision(std::numeric_limits<double>::max_digits10)
                       << source.confidence;
            append(confidence.str());
            append("original");
            append(source.observed_at_ms ? "1" : "0");
            if(source.observed_at_ms) append_uint(*source.observed_at_ms);
            append(source.preview);
        }
        append_int(envelope.created_at_ms);
        append_int(envelope.updated_at_ms);
        append_int(envelope.observed_at_ms);
        append_uint(envelope.revision);
        append_bytes(envelope.content_hash.bytes.data(), envelope.content_hash.bytes.size());
        append_uint(envelope.content_hash_recipe_version);
        std::ostringstream priority;
        priority.imbue(std::locale::classic());
        priority << std::setprecision(std::numeric_limits<double>::max_digits10)
                 << envelope.priority_weight;
        append(priority.str());
        append_uint(envelope.supersedes.size());
        for(const auto id : envelope.supersedes) append_uint(id.value());
        append(envelope.superseded_by ? "1" : "0");
        if(envelope.superseded_by) append_uint(envelope.superseded_by->value());
        append(envelope.derived_from ? "1" : "0");
        if(envelope.derived_from) append_uint(envelope.derived_from->value());
        return payload;
    }

    std::string legacy_content_key(const agent_memory::KnowledgeUnitEnvelope& envelope) {
        std::string key;
        const auto append = [&key](std::string_view value) {
            key += std::to_string(value.size());
            key.push_back(':');
            key.append(value.data(), value.size());
        };
        append(agent_memory::to_string(envelope.kind));
        append(envelope.scope_id.value());
        append(std::string_view{
            reinterpret_cast<const char*>(envelope.content_hash.bytes.data()),
            envelope.content_hash.bytes.size()
        });
        return key;
    }

} // namespace

int main() {
    {
        const auto legacy_path = test_path();
        std::error_code legacy_error;
        std::filesystem::remove(legacy_path, legacy_error);
        const auto legacy_first = make_envelope(
            "legacy-scope", 0x51, 10, "legacy first"
        );
        const auto legacy_second = make_envelope(
            "legacy-scope", 0x52, 100, "legacy second"
        );
        {
            auto context = agent_memory::MdbxStorageContext::open(
                legacy_path.string(), false, agent_memory::kDefaultMdbxMaxDbs
            );
            mdbxc::KeyValueTable<std::string, std::string> legacy_units(
                context->connection(), "agent_memory_legacy_knowledge_units"
            );
            mdbxc::KeyValueTable<std::string, std::string> legacy_content_keys(
                context->connection(), "agent_memory_legacy_content_key_to_unit_id"
            );
            mdbxc::KeyValueTable<std::string, std::string> legacy_index(
                context->connection(), "agent_memory_legacy_knowledge_units_by_kind"
            );
            legacy_units.insert_or_assign(
                std::to_string(legacy_first.id.value()),
                serialize_legacy_envelope(legacy_first)
            );
            legacy_units.insert_or_assign(
                std::to_string(legacy_second.id.value()),
                serialize_legacy_envelope(legacy_second)
            );
            legacy_units.insert_or_assign("@next_id", "101");
            legacy_content_keys.insert_or_assign(
                legacy_content_key(legacy_first), std::to_string(legacy_first.id.value())
            );
            legacy_content_keys.insert_or_assign(
                legacy_content_key(legacy_second), std::to_string(legacy_second.id.value())
            );
            legacy_index.insert_or_assign(
                legacy_index_key("legacy-scope", "chunk"),
                encode_legacy_index({10, 100})
            );
        }
        {
        agent_memory::MdbxKnowledgeUnitStore migrated({
            legacy_path.string(), "agent_memory_legacy", false
        });
        expect(migrated.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"legacy-scope"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{100}
               }, "legacy serialized index must remain readable");
        const auto restored_first = migrated.find(legacy_first.id);
        expect(restored_first && restored_first->primary_text == legacy_first.primary_text,
               "legacy envelope must deserialize from the real fixture");
        const auto restored_key = migrated.find_by_content_key({
            legacy_first.kind, legacy_first.scope_id, legacy_first.content_hash
        });
        expect(restored_key && *restored_key == legacy_first.id,
               "legacy content-key mapping must survive upgrade");
        const auto duplicate = migrated.create_or_get_unit(
            make_request("legacy-scope", 0x51, "duplicate legacy")
        );
        expect(duplicate.id == legacy_first.id,
               "legacy content-key deduplication must return the original id");
        expect(migrated.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"legacy-scope"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{100}
               }, "legacy deduplication must not append a duplicate index entry");
        const auto migrated_unit = migrated.create_or_get_unit(
            make_request("legacy-scope", 0x53, "migrated")
        );
        expect(migrated_unit.id.value() == 101,
               "legacy allocator frontier must be preserved during migration");
        expect(migrated.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"legacy-scope"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{100},
                   agent_memory::KnowledgeUnitId{101}
               }, "legacy index must migrate before appending new entries");
        expect(migrated.erase(migrated_unit.id),
               "migrated index entry must be erasable");
        expect(migrated.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"legacy-scope"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{100}
               }, "migrated index erase must preserve remaining order");
        }
        {
            auto context = agent_memory::MdbxStorageContext::open(
                legacy_path.string(), false, agent_memory::kDefaultMdbxMaxDbs
            );
            mdbxc::KeyValueTable<std::string, std::string> untouched_index(
                context->connection(), "agent_memory_legacy_knowledge_units_by_kind"
            );
            untouched_index.insert_or_assign(
                legacy_index_key("untouched-scope", "chunk"),
                encode_legacy_index({10})
            );
        }
        {
            agent_memory::MdbxKnowledgeUnitStore v2_reader({
                legacy_path.string(), "agent_memory_legacy", false
            });
            expect(v2_reader.scan_by_scope_and_kind(
                       agent_memory::ScopeId{"untouched-scope"},
                       agent_memory::KnowledgeUnitKind::Chunk
                   ) == std::vector<agent_memory::KnowledgeUnitId>{
                       agent_memory::KnowledgeUnitId{10}
                   }, "untouched legacy group must remain readable after v2 activation");
        }
        const auto rollback_path = test_path();
        {
            auto context = agent_memory::MdbxStorageContext::open(
                rollback_path.string(), false, agent_memory::kDefaultMdbxMaxDbs
            );
            mdbxc::KeyValueTable<std::string, std::string> units(
                context->connection(), "agent_memory_rollback_knowledge_units"
            );
            mdbxc::KeyValueTable<std::string, std::string> index(
                context->connection(), "agent_memory_rollback_knowledge_units_by_kind"
            );
            index.insert_or_assign(
                legacy_index_key("rollback-scope", "chunk"),
                encode_legacy_index({10})
            );
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            index.erase(legacy_index_key("rollback-scope", "chunk"), txn);
            index.insert_or_assign(
                legacy_index_key("rollback-scope", "chunk") + std::string(8, '\0'),
                "10",
                txn
            );
            units.insert_or_assign("@knowledge_units_by_kind_schema_version", "2", txn);
        }
        {
            agent_memory::MdbxKnowledgeUnitStore rollback_check({
                rollback_path.string(), "agent_memory_rollback", false
            });
            expect(rollback_check.scan_by_scope_and_kind(
                       agent_memory::ScopeId{"rollback-scope"},
                       agent_memory::KnowledgeUnitKind::Chunk
                   ) == std::vector<agent_memory::KnowledgeUnitId>{
                       agent_memory::KnowledgeUnitId{10}
                   }, "aborted index migration must leave the legacy index intact");
        }
        std::filesystem::remove(rollback_path, legacy_error);
        {
            auto context = agent_memory::MdbxStorageContext::open(
                legacy_path.string(), false, agent_memory::kDefaultMdbxMaxDbs
            );
            mdbxc::KeyValueTable<std::string, std::string> mixed_index(
                context->connection(), "agent_memory_legacy_knowledge_units_by_kind"
            );
            mixed_index.insert_or_assign(
                legacy_index_key("legacy-scope", "chunk"),
                encode_legacy_index({10})
            );
        }
        {
            agent_memory::MdbxKnowledgeUnitStore mixed_check({
                legacy_path.string(), "agent_memory_legacy", false
            });
            bool rejected_mixed_format = false;
            try {
                (void)mixed_check.scan_by_scope_and_kind(
                    agent_memory::ScopeId{"legacy-scope"},
                    agent_memory::KnowledgeUnitKind::Chunk
                );
            } catch(const std::runtime_error&) {
                rejected_mixed_format = true;
            }
            expect(rejected_mixed_format,
                   "v2 marker with a legacy key must fail closed");
        }
        {
            auto context = agent_memory::MdbxStorageContext::open(
                legacy_path.string(), false, agent_memory::kDefaultMdbxMaxDbs
            );
            mdbxc::KeyValueTable<std::string, std::string> mixed_index(
                context->connection(), "agent_memory_legacy_knowledge_units_by_kind"
            );
            mixed_index.erase(legacy_index_key("legacy-scope", "chunk"));
        }
        std::filesystem::remove(legacy_path, legacy_error);
    }

    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);

    agent_memory::KnowledgeUnitId first_id;
    {
        agent_memory::MdbxKnowledgeUnitStore store({
            path.string(), "agent_memory_test", false
        });

        auto first_request = make_request("tenant-a", 0x01, "first display");
        const auto key_hash = *first_request.content_hash;
        const auto first = store.create_or_get_unit(std::move(first_request));
        first_id = first.id;
        expect(first_id.value() == 1, "first knowledge-unit id must start at one");
        expect(first.revision == 1, "new knowledge unit must start at revision one");
        expect(first.sources.size() == 1, "source summary must be persisted inline");
        expect(first.sources.front().resource_revision->generation == 7,
               "source revision generation must be retained");

        auto duplicate_request = make_request("tenant-a", 0x01, "ignored duplicate");
        const auto duplicate = store.create_or_get_unit(std::move(duplicate_request));
        expect(duplicate.id == first.id, "same content key must deduplicate");
        expect(duplicate.display_text == "first display",
               "dedupe must not overwrite the existing envelope");

        auto no_assertion_request = make_request(
            "tenant-no-assertion", 0x02, "storage computes the hash"
        );
        no_assertion_request.content_hash.reset();
        const auto no_assertion_unit = store.create_or_get_unit(
            std::move(no_assertion_request)
        );
        expect(
            no_assertion_unit.content_hash
                == agent_memory::compute_chunk_content_hash(
                    no_assertion_unit.scope_id,
                    no_assertion_unit.primary_text,
                    no_assertion_unit.sources,
                    no_assertion_unit.content_hash_recipe_version
                ),
            "M0 storage must compute content hash without a caller assertion"
        );

        auto bad_assertion_request = make_request(
            "tenant-bad-assertion", 0x03, "rejected hash assertion"
        );
        bad_assertion_request.content_hash = agent_memory::ContentHash{};
        bool rejected_bad_assertion = false;
        try { (void)store.create_or_get_unit(std::move(bad_assertion_request)); }
        catch(const std::invalid_argument&) { rejected_bad_assertion = true; }
        expect(rejected_bad_assertion,
               "M0 must reject a mismatched optional content hash assertion");

        auto legacy_request = make_request("tenant-legacy", 0x70, "legacy preview");
        legacy_request.sources.front().reference_mode =
            agent_memory::SourceReferenceMode::LegacyPreviewOnly;
        legacy_request.sources.front().resource_revision.reset();
        legacy_request.sources.front().excerpt = agent_memory::TextRange{};
        legacy_request.sources.front().quote_hash = {};
        legacy_request.content_hash = agent_memory::compute_chunk_content_hash(
            legacy_request.scope_id,
            legacy_request.primary_text,
            legacy_request.sources,
            legacy_request.content_hash_recipe_version
        );
        bool rejected_legacy_create = false;
        try { (void)store.create_or_get_unit(std::move(legacy_request)); }
        catch(const std::invalid_argument&) { rejected_legacy_create = true; }
        expect(rejected_legacy_create,
               "M0 create must reject legacy preview-only Chunk provenance");

        auto legacy_envelope = make_envelope(
            "tenant-legacy-upsert", 0x71, 900, "legacy preview upsert"
        );
        legacy_envelope.sources.front().reference_mode =
            agent_memory::SourceReferenceMode::LegacyPreviewOnly;
        legacy_envelope.sources.front().resource_revision.reset();
        legacy_envelope.sources.front().excerpt = agent_memory::TextRange{};
        legacy_envelope.sources.front().quote_hash = {};
        legacy_envelope.content_hash = agent_memory::compute_chunk_content_hash(
            legacy_envelope.scope_id,
            legacy_envelope.primary_text,
            legacy_envelope.sources,
            legacy_envelope.content_hash_recipe_version
        );
        bool rejected_legacy_upsert = false;
        try { store.upsert(std::move(legacy_envelope)); }
        catch(const std::invalid_argument&) { rejected_legacy_upsert = true; }
        expect(rejected_legacy_upsert,
               "M0 upsert must reject legacy preview-only Chunk provenance");

        auto same_bytes_other_resource = make_request_for_source(
            "tenant-a",
            agent_memory::ResourceId{"resource:other"},
            7,
            0x01,
            "same bytes, other resource"
        );
        const auto other_resource_unit = store.create_or_get_unit(
            std::move(same_bytes_other_resource)
        );
        expect(other_resource_unit.id != first.id,
               "equal body bytes from different resources must not deduplicate");
        expect(other_resource_unit.sources.front().resource_id ==
                   agent_memory::ResourceId{"resource:other"},
               "deduplicated identity must retain the other resource provenance");

        auto same_resource_next_generation = make_request_for_source(
            "tenant-a",
            agent_memory::ResourceId{"resource:manual"},
            8,
            0x01,
            "same bytes, next generation"
        );
        const auto next_generation_unit = store.create_or_get_unit(
            std::move(same_resource_next_generation)
        );
        expect(next_generation_unit.id != first.id,
               "different resource generations must not deduplicate");
        expect(next_generation_unit.sources.front().resource_revision->generation == 8,
               "generation-specific identity must retain its provenance revision");

        auto mismatched_identity = make_request("tenant-a", 0x01, "different");
        mismatched_identity.primary_text = "different canonical text";
        mismatched_identity.content_hash = duplicate.content_hash;
        bool rejected_mismatched_identity = false;
        try { (void)store.create_or_get_unit(std::move(mismatched_identity)); }
        catch(const std::invalid_argument&) { rejected_mismatched_identity = true; }
        expect(rejected_mismatched_identity,
               "caller hash assertion must not hide a different Chunk identity");

        const auto key = agent_memory::KnowledgeUnitKey{
            agent_memory::KnowledgeUnitKind::Chunk,
            agent_memory::ScopeId{"tenant-a"},
            key_hash
        };
        const auto by_key = store.find_by_content_key(key);
        expect(by_key && *by_key == first.id, "content-key lookup must return the unit id");

        const auto updated = store.update_mutable_fields(
            first.id,
            agent_memory::KnowledgeUnitMutablePatch{
                std::string{"updated display"},
                std::nullopt,
                std::nullopt,
                std::int64_t{140},
                std::nullopt
            }
        );
        expect(updated.id == first.id, "mutable update must preserve unit id");
        expect(updated.revision == 2, "mutable update must advance envelope revision");
        expect(updated.display_text == "updated display",
               "mutable display field must be persisted");

        const auto priority_only = store.update_mutable_fields(
            first.id,
            agent_memory::KnowledgeUnitMutablePatch{
                std::nullopt, std::nullopt, std::nullopt, std::nullopt, 0.7
            }
        );
        expect(priority_only.revision == updated.revision,
               "priority-only patch must not advance envelope revision");

        auto non_identity_source = make_source(
            agent_memory::ResourceId{"resource:manual"},
            7,
            0x01
        );
        non_identity_source.uri = "https://example.test/source#updated";
        non_identity_source.preview = "updated durable source preview";
        non_identity_source.quote_hash = agent_memory::compute_quote_hash(
            non_identity_source.preview
        );
        const auto source_updated = store.update_mutable_fields(
            first.id,
            agent_memory::KnowledgeUnitMutablePatch{
                std::nullopt,
                std::vector<agent_memory::SourceRefSummary>{non_identity_source},
                std::nullopt,
                std::int64_t{160},
                std::nullopt
            }
        );
        expect(source_updated.revision == 3,
               "source summary update must advance envelope revision");
        expect(source_updated.sources.front().uri == "https://example.test/source#updated",
               "non-identity source summary update must be persisted");

        bool rejected_identity_source_update = false;
        auto changed_identity_source = make_source(
            agent_memory::ResourceId{"resource:manual"},
            8,
            0x20
        );
        try {
            (void)store.update_mutable_fields(
                first.id,
                agent_memory::KnowledgeUnitMutablePatch{
                    std::nullopt,
                    std::vector<agent_memory::SourceRefSummary>{changed_identity_source},
                    std::nullopt,
                    std::nullopt,
                    std::nullopt
                }
            );
        } catch(const std::invalid_argument&) {
            rejected_identity_source_update = true;
        }
        expect(rejected_identity_source_update,
               "source revision changes must not mutate Chunk identity in place");

        const auto lifecycle_unit = store.create_or_get_unit(
            make_request("tenant-lifecycle", 0x72, "lifecycle replay")
        );
        const auto deprecated_lifecycle_unit = store.update_mutable_fields(
            lifecycle_unit.id,
            agent_memory::KnowledgeUnitMutablePatch{
                std::nullopt,
                std::nullopt,
                agent_memory::KnowledgeUnitLifecycleState::Deprecated,
                std::nullopt,
                std::nullopt
            }
        );
        store.upsert(deprecated_lifecycle_unit);

        const auto combined_patch_unit = store.create_or_get_unit(
            make_request("tenant-combined-patch", 0x73, "combined patch")
        );
        const auto combined_patch_result = store.update_mutable_fields(
            combined_patch_unit.id,
            agent_memory::KnowledgeUnitMutablePatch{
                std::string{"combined display"},
                combined_patch_unit.sources,
                std::nullopt,
                std::nullopt,
                std::nullopt
            }
        );
        expect(combined_patch_result.revision == combined_patch_unit.revision + 1,
               "combined display and unchanged sources must advance revision once");

        bool rejected_invalid_quote_hash = false;
        auto invalid_quote_request = make_request("tenant-a", 0x55, "invalid quote");
        invalid_quote_request.sources.front().quote_hash[0] = static_cast<std::uint8_t>(
            invalid_quote_request.sources.front().quote_hash[0] ^ 0xffU
        );
        invalid_quote_request.content_hash = agent_memory::compute_chunk_content_hash(
            invalid_quote_request.scope_id,
            invalid_quote_request.primary_text,
            invalid_quote_request.sources,
            invalid_quote_request.content_hash_recipe_version
        );
        try { (void)store.create_or_get_unit(std::move(invalid_quote_request)); }
        catch(const std::invalid_argument&) { rejected_invalid_quote_hash = true; }
        expect(rejected_invalid_quote_hash,
               "create must reject a source summary with a stale quote hash");

        bool rejected_overlong_primary_text = false;
        auto overlong_request = make_request("tenant-a", 0x66, "overlong");
        overlong_request.primary_text.assign(1025, 'x');
        overlong_request.content_hash = agent_memory::compute_chunk_content_hash(
            overlong_request.scope_id,
            overlong_request.primary_text,
            overlong_request.sources,
            overlong_request.content_hash_recipe_version
        );
        try { (void)store.create_or_get_unit(std::move(overlong_request)); }
        catch(const std::invalid_argument&) { rejected_overlong_primary_text = true; }
        expect(rejected_overlong_primary_text,
               "create must reject primary text beyond the bounded M0 seed");

        bool rejected_multi_source = false;
        auto multi_source_request = make_request("tenant-a", 0x33, "multi-source");
        multi_source_request.sources.push_back(make_source(
            agent_memory::ResourceId{"resource:second"},
            1,
            0x44
        ));
        multi_source_request.content_hash = agent_memory::compute_chunk_content_hash(
            multi_source_request.scope_id,
            multi_source_request.primary_text,
            multi_source_request.sources,
            multi_source_request.content_hash_recipe_version
        );
        try { (void)store.create_or_get_unit(std::move(multi_source_request)); }
        catch(const std::invalid_argument&) { rejected_multi_source = true; }
        expect(rejected_multi_source,
               "M0 must reject multi-source Chunk identity until canonical hydration exists");

        bool rejected_empty_sources = false;
        try {
            (void)store.update_mutable_fields(
                first.id,
                agent_memory::KnowledgeUnitMutablePatch{
                    std::nullopt,
                    std::vector<agent_memory::SourceRefSummary>{},
                    std::nullopt,
                    std::nullopt,
                    std::nullopt
                }
            );
        } catch(const std::invalid_argument&) {
            rejected_empty_sources = true;
        }
        expect(rejected_empty_sources,
               "chunk update must reject removal of its required source summary");

        const auto other_scope = store.create_or_get_unit(
            make_request("tenant-b", 0x01, "other scope")
        );
        expect(other_scope.id != first.id,
               "the same content hash in another scope must not deduplicate");
        expect(store.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"tenant-a"},
                   agent_memory::KnowledgeUnitKind::Chunk).size() == 3,
               "kind scan must remain scope-aware");
        expect(store.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"tenant-b"},
                   agent_memory::KnowledgeUnitKind::Chunk).size() == 1,
               "kind scan must find only the requested scope");
        expect(store.scan_by_scope(agent_memory::ScopeId{"tenant-a"}).size() == 3,
               "scope index must not leak another tenant's unit");

        store.upsert(source_updated); // exact replay is accepted
        const auto upserted = store.find(first.id);
        expect(upserted && upserted->revision == source_updated.revision,
               "upsert must preserve a valid immutable identity");
        auto revision_rollback = source_updated;
        revision_rollback.revision = 1;
        bool rejected_revision_rollback = false;
        try { store.upsert(revision_rollback); } catch(const std::invalid_argument&) {
            rejected_revision_rollback = true;
        }
        expect(rejected_revision_rollback, "upsert must reject revision rollback");
        auto mutable_upsert = source_updated;
        mutable_upsert.display_text = "mutable through upsert";
        bool rejected_mutable_upsert = false;
        try { store.upsert(mutable_upsert); } catch(const std::invalid_argument&) {
            rejected_mutable_upsert = true;
        }
        expect(rejected_mutable_upsert, "upsert must reject mutable changes");
        auto recipe_mutation = source_updated;
        recipe_mutation.content_hash_recipe_version = 1;
        bool rejected_recipe_mutation = false;
        try { store.upsert(recipe_mutation); } catch(const std::invalid_argument&) {
            rejected_recipe_mutation = true;
        }
        expect(rejected_recipe_mutation, "upsert must reject recipe mutation");
        auto lifecycle_revival = source_updated;
        lifecycle_revival.lifecycle_state = agent_memory::KnowledgeUnitLifecycleState::Deprecated;
        bool rejected_lifecycle_revival = false;
        try { store.upsert(lifecycle_revival); } catch(const std::invalid_argument&) {
            rejected_lifecycle_revival = true;
        }
        expect(rejected_lifecycle_revival, "upsert must reject lifecycle mutation");
        auto created_mutation = source_updated;
        created_mutation.created_at_ms += 1;
        bool rejected_created_mutation = false;
        try { store.upsert(created_mutation); } catch(const std::invalid_argument&) {
            rejected_created_mutation = true;
        }
        expect(rejected_created_mutation, "upsert must reject created-at mutation");
        auto identity_mutation = source_updated;
        identity_mutation.primary_text = "identity mutation";
        bool rejected_identity_mutation = false;
        try {
            store.upsert(std::move(identity_mutation));
        } catch(const std::invalid_argument&) {
            rejected_identity_mutation = true;
        }
        expect(rejected_identity_mutation,
               "upsert must reject primary-text identity mutation");
        expect(store.erase(other_scope.id), "erase must remove an existing unit");
        expect(!store.find(other_scope.id), "erased unit must not remain readable");
        bool rejected_id_reuse = false;
        try {
            store.upsert(other_scope);
        } catch(const std::invalid_argument&) {
            rejected_id_reuse = true;
        }
        expect(rejected_id_reuse, "erased knowledge-unit ids must never be reused");

        const auto ordered_10 = make_envelope("tenant-order", 0x10, 10, "ten");
        const auto ordered_100 = make_envelope("tenant-order", 0x20, 100, "one hundred");
        const auto ordered_50 = make_envelope("tenant-order", 0x30, 50, "fifty");
        store.upsert(ordered_10);
        store.upsert(ordered_100);
        store.upsert(ordered_50);
        expect(store.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"tenant-order"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{100},
                   agent_memory::KnowledgeUnitId{50}
               }, "kind scan must preserve insertion order, not numeric order");
        store.upsert(ordered_50);
        expect(store.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"tenant-order"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ).back().value() == 50,
               "exact replay must not reorder an existing index entry");
        expect(store.erase(agent_memory::KnowledgeUnitId{100}),
               "ordered unit must be erasable");
        expect(store.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"tenant-order"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{50}
               }, "erase must remove only the erased index member");
        bool rejected_ordered_reinsert = false;
        try { store.upsert(ordered_100); }
        catch(const std::invalid_argument&) { rejected_ordered_reinsert = true; }
        expect(rejected_ordered_reinsert,
               "an erased explicit id must remain permanently reserved");

        bool rejected_conflicting_explicit_id = false;
        auto conflicting_id = ordered_10;
        conflicting_id.id = agent_memory::KnowledgeUnitId{102};
        try { store.upsert(conflicting_id); }
        catch(const std::invalid_argument&) { rejected_conflicting_explicit_id = true; }
        expect(rejected_conflicting_explicit_id,
               "conflicting content-key upsert must fail before mutating allocator state");
        expect(!store.find(agent_memory::KnowledgeUnitId{102}),
               "failed explicit upsert must leave no partial envelope row");
    }

    {
        agent_memory::MdbxKnowledgeUnitStore reopened({
            path.string(), "agent_memory_test", false
        });
        const auto restored = reopened.find(first_id);
        expect(restored && restored->display_text == "updated display",
               "knowledge-unit mutable state must survive reopen");
        expect(restored->sources.front().resource_revision->body_digest.bytes[0] == 0x01,
               "source body digest must survive reopen");
        expect(restored->priority_weight == 0.7,
               "fractional priority weight must round-trip losslessly");
        const auto next = reopened.create_or_get_unit(
            make_request("tenant-a", 0x02, "second")
        );
        expect(next.id.value() == 101,
               "explicit high ids must advance the allocator frontier atomically");
        expect(reopened.scan_by_scope_and_kind(
                   agent_memory::ScopeId{"tenant-order"},
                   agent_memory::KnowledgeUnitKind::Chunk
               ) == std::vector<agent_memory::KnowledgeUnitId>{
                   agent_memory::KnowledgeUnitId{10},
                   agent_memory::KnowledgeUnitId{50}
               }, "index insertion order must survive reopen");
    }

    std::filesystem::remove(path, error);
    return 0;
}

#endif // AGENT_MEMORY_HAS_MDBX
