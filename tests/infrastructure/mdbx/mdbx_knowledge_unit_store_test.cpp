#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
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
        envelope.content_hash = request.content_hash;
        envelope.content_hash_recipe_version = request.content_hash_recipe_version;
        envelope.priority_weight = request.priority_weight;
        return envelope;
    }

} // namespace

int main() {
    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);

    agent_memory::KnowledgeUnitId first_id;
    {
        agent_memory::MdbxKnowledgeUnitStore store({
            path.string(), "agent_memory_test", false
        });

        auto first_request = make_request("tenant-a", 0x01, "first display");
        const auto key_hash = first_request.content_hash;
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
