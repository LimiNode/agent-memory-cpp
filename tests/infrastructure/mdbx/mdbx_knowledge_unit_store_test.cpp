#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <stdexcept>
#include <string>

namespace {

    void expect(bool condition, const char* message) {
        if(!condition) throw std::runtime_error(message);
    }

    std::filesystem::path test_path() {
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        return std::filesystem::temp_directory_path() /
            ("agent_memory_m0_knowledge_units_" + std::to_string(nonce) + ".mdbx");
    }

    agent_memory::ContentHash make_content_hash(std::uint8_t seed) {
        agent_memory::ContentHash hash;
        for(std::size_t index = 0; index < hash.bytes.size(); ++index) {
            hash.bytes[index] = static_cast<std::uint8_t>(seed + index);
        }
        return hash;
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
        summary.quote_hash[0] = 0x42;
        summary.confidence = 0.9;
        summary.preview = "durable source preview";
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
        request.content_hash = make_content_hash(content_seed);
        request.content_hash_recipe_version = 1;
        request.sources.push_back(make_source(
            agent_memory::ResourceId{"resource:manual"},
            7,
            0x10
        ));
        request.created_at_ms = 100;
        request.observed_at_ms = 120;
        request.priority_weight = 0.5;
        return request;
    }

} // namespace

int main() {
    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);

    agent_memory::KnowledgeUnitId first_id;
    const auto key_hash = make_content_hash(0x01);
    {
        agent_memory::MdbxKnowledgeUnitStore store({
            path.string(), "agent_memory_test", false
        });

        const auto first = store.create_or_get_unit(
            make_request("tenant-a", 0x01, "first display")
        );
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

        const auto source_updated = store.update_mutable_fields(
            first.id,
            agent_memory::KnowledgeUnitMutablePatch{
                std::nullopt,
                std::vector<agent_memory::SourceRefSummary>{make_source(
                    agent_memory::ResourceId{"resource:manual"},
                    8,
                    0x20
                )},
                std::nullopt,
                std::int64_t{160},
                std::nullopt
            }
        );
        expect(source_updated.revision == 3,
               "source summary update must advance envelope revision");
        expect(source_updated.sources.front().resource_revision->generation == 8,
               "source summary update must replace the bound resource revision");

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
        expect(store.scan_by_kind(agent_memory::KnowledgeUnitKind::Chunk).size() == 2,
               "kind index must include both scoped chunk units");
        expect(store.scan_by_scope(agent_memory::ScopeId{"tenant-a"}).size() == 1,
               "scope index must not leak another tenant's unit");

        store.upsert(source_updated);
        const auto upserted = store.find(first.id);
        expect(upserted && upserted->revision == source_updated.revision,
               "upsert must preserve a valid immutable identity");
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
    }

    {
        agent_memory::MdbxKnowledgeUnitStore reopened({
            path.string(), "agent_memory_test", false
        });
        const auto restored = reopened.find(first_id);
        expect(restored && restored->display_text == "updated display",
               "knowledge-unit mutable state must survive reopen");
        expect(restored->sources.front().resource_revision->body_digest.bytes[0] == 0x20,
               "source body digest must survive reopen");
        const auto next = reopened.create_or_get_unit(
            make_request("tenant-a", 0x02, "second")
        );
        expect(next.id.value() > first_id.value(),
               "knowledge-unit ids must remain monotonic after reopen");
    }

    std::filesystem::remove(path, error);
    return 0;
}

#endif // AGENT_MEMORY_HAS_MDBX
