#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitTypedStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path(const char* suffix) {
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        (std::string{"agent_memory_typed_"} + suffix + std::to_string(nonce) + ".mdbx");
}

ResourceBodyDigest digest(std::uint8_t seed) {
    ResourceBodyDigest value;
    for(std::size_t index = 0; index < value.bytes.size(); ++index) {
        value.bytes[index] = static_cast<std::uint8_t>(seed + index);
    }
    return value;
}

SourceRefSummary source(std::string resource, std::uint8_t seed) {
    SourceRefSummary result;
    result.resource_id = ResourceId{std::move(resource)};
    result.resource_revision = ResourceRevisionRef{
        result.resource_id,
        7,
        digest(seed)
    };
    result.uri = "https://example.test/source";
    result.excerpt = TextRange{4, 12};
    result.preview = "typed persistent source";
    result.quote_hash = compute_quote_hash(result.preview);
    result.confidence = 0.9;
    return result;
}

KnowledgeUnitCreateRequest request(std::string scope, std::string text, std::uint8_t seed) {
    KnowledgeUnitCreateRequest value;
    value.kind = KnowledgeUnitKind::Chunk;
    value.scope_id = ScopeId{std::move(scope)};
    value.primary_text = std::move(text);
    value.display_text = value.primary_text;
    value.content_hash_recipe_version = 2;
    value.sources.push_back(source("resource:typed", seed));
    value.created_at_ms = 10;
    value.observed_at_ms = 11;
    value.priority_weight = 0.5;
    value.content_hash = compute_chunk_content_hash(
        value.scope_id,
        value.primary_text,
        value.sources,
        value.content_hash_recipe_version
    );
    return value;
}

KnowledgeUnitEnvelope envelope(
    std::string scope,
    std::string text,
    std::uint64_t id,
    std::uint8_t seed
) {
    const auto value = request(std::move(scope), std::move(text), seed);
    KnowledgeUnitEnvelope result;
    result.id = KnowledgeUnitId{id};
    result.kind = value.kind;
    result.scope_id = value.scope_id;
    result.primary_text = value.primary_text;
    result.display_text = value.display_text;
    result.sources = value.sources;
    result.created_at_ms = value.created_at_ms;
    result.updated_at_ms = value.created_at_ms;
    result.observed_at_ms = value.observed_at_ms;
    result.revision = 1;
    result.content_hash = *value.content_hash;
    result.content_hash_recipe_version = value.content_hash_recipe_version;
    result.priority_weight = value.priority_weight;
    return result;
}

} // namespace

int main() {
    try {
        const auto path = test_path("store_");
        std::error_code error;
        std::filesystem::remove(path, error);

        MdbxKnowledgeUnitTypedStoreOptions options;
        options.path = path.string();
        options.table_prefix = "typed store";
        KnowledgeUnitEnvelope first;
        KnowledgeUnitEnvelope second;
        {
            MdbxKnowledgeUnitTypedStore store(options);
            first = store.create_or_get_unit(request("scope-a", "unit ten", 1));
            const auto replay = store.create_or_get_unit(request("scope-a", "unit ten", 1));
            expect(replay.id == first.id, "content-key deduplication allocated a second id");
            second = store.create_or_get_unit(request("scope-a", "unit two", 2));
            const auto isolated = store.create_or_get_unit(request("scope-b", "unit ten", 1));
            expect(isolated.id != first.id, "scope isolation was lost");
            expect(store.find_by_content_key(
                KnowledgeUnitKey{first.kind, first.scope_id, first.content_hash}
            ) == std::optional<KnowledgeUnitId>{first.id}, "content index lookup failed");
            const auto ids = store.scan_by_scope_and_kind(ScopeId{"scope-a"}, first.kind);
            expect(ids.size() == 2U && ids[0] == first.id && ids[1] == second.id,
                   "by-kind index did not preserve numeric order");

            auto patch = KnowledgeUnitMutablePatch{};
            patch.display_text = "updated display";
            const auto updated = store.update_mutable_fields(first.id, std::move(patch));
            expect(updated.revision == first.revision + 1U,
                   "mutable retrieval update did not advance revision");

            auto invalid = envelope("scope-a", "different identity", first.id.value(), 4);
            bool immutable_rejected = false;
            try {
                store.upsert(std::move(invalid));
            } catch(const std::invalid_argument&) {
                immutable_rejected = true;
            }
            expect(immutable_rejected, "typed upsert accepted an immutable identity mutation");
            expect(store.find(first.id)->primary_text == first.primary_text,
                   "failed typed mutation leaked partial primary state");

            expect(store.erase(second.id), "typed row was not erased");
            const auto next = store.create_or_get_unit(request("scope-a", "unit three", 3));
            expect(next.id.value() > second.id.value(), "typed id allocation reused an erased id");
        }

        MdbxKnowledgeUnitTypedStore reopened(options);
        expect(reopened.find(first.id).has_value(), "typed envelope did not survive reopen");
        expect(!reopened.find(second.id).has_value(), "erased typed envelope reappeared");

        const auto legacy_path = test_path("legacy_");
        std::filesystem::remove(legacy_path, error);
        MdbxKnowledgeUnitStoreOptions legacy_options;
        legacy_options.path = legacy_path.string();
        legacy_options.table_prefix = "legacy parity";
        const auto parity_path = test_path("typed_parity_");
        {
            MdbxKnowledgeUnitStore legacy_store(legacy_options);
            const auto legacy = legacy_store.create_or_get_unit(
                request("scope-parity", "same logical envelope", 31));
            MdbxKnowledgeUnitTypedStoreOptions parity_options;
            parity_options.path = parity_path.string();
            parity_options.table_prefix = "typed parity";
            MdbxKnowledgeUnitTypedStore typed_parity(parity_options);
            const auto typed = typed_parity.create_or_get_unit(
                request("scope-parity", "same logical envelope", 31));
            expect(legacy.id == typed.id && legacy.kind == typed.kind
                       && legacy.scope_id == typed.scope_id
                       && legacy.primary_text == typed.primary_text
                       && legacy.content_hash == typed.content_hash
                       && legacy.revision == typed.revision
                       && MdbxKnowledgeUnitStore::encode_envelope_payload(legacy)
                           == MdbxKnowledgeUnitStore::encode_envelope_payload(typed),
                   "legacy and typed stores diverged on decoded envelope parity");
        }
        std::filesystem::remove(parity_path, error);
        std::filesystem::remove(legacy_path, error);

        const auto numeric_path = test_path("numeric_");
        std::filesystem::remove(numeric_path, error);
        MdbxKnowledgeUnitTypedStoreOptions numeric_options;
        numeric_options.path = numeric_path.string();
        numeric_options.table_prefix = "numeric order";
        {
            MdbxKnowledgeUnitTypedStore store(numeric_options);
            store.upsert(envelope("scope", "id ten", 10, 10));
            store.upsert(envelope("scope", "id two", 2, 11));
            const auto ids = store.scan_by_scope_and_kind(
                ScopeId{"scope"}, KnowledgeUnitKind::Chunk);
            expect(ids.size() == 2U && ids[0].value() == 2U && ids[1].value() == 10U,
                   "numeric typed index order was lexical rather than numeric");

            const auto boundary_id = KnowledgeUnitId{
                std::numeric_limits<std::uint64_t>::max()};
            bool boundary_rejected = false;
            try {
                store.upsert(envelope("scope", "id boundary", boundary_id.value(), 12));
            } catch(const std::runtime_error&) {
                boundary_rejected = true;
            }
            expect(boundary_rejected, "typed id-space boundary was not rejected");
            expect(!store.find(boundary_id).has_value(),
                   "rejected boundary write leaked a primary row");
            const auto after_boundary = store.create_or_get_unit(
                request("scope", "id eleven", 13));
            expect(after_boundary.id.value() == 11U,
                   "rejected boundary write advanced the allocation high-water mark");
        }

        const auto corrupt_path = test_path("corrupt_");
        std::filesystem::remove(corrupt_path, error);
        MdbxKnowledgeUnitTypedStoreOptions corrupt_options;
        corrupt_options.path = corrupt_path.string();
        corrupt_options.table_prefix = "corrupt typed";
        KnowledgeUnitId corrupt_id;
        {
            MdbxKnowledgeUnitTypedStore store(corrupt_options);
            corrupt_id = store.create_or_get_unit(request("scope", "corruption", 21)).id;
        }
        {
            auto context = MdbxStorageContext::open(corrupt_path.string());
            mdbxc::KeyValueTable<std::string, std::uint64_t> content_keys(
                context->connection(), "corrupt_typed_ku_typed_v1_content");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            std::string content_key;
            content_keys.for_each_range(
                std::string{},
                std::string(1, static_cast<char>(0xff)),
                [&content_key](const std::string& key, const std::uint64_t) {
                    content_key = key;
                    return false;
                },
                txn
            );
            content_keys.erase(content_key, txn);
            txn.commit();
        }
        {
            MdbxKnowledgeUnitTypedStore store(corrupt_options);
            bool missing_index_rejected = false;
            try {
                static_cast<void>(store.find(corrupt_id));
            } catch(const std::runtime_error&) {
                missing_index_rejected = true;
            }
            expect(missing_index_rejected,
                   "typed store did not reject a missing content index row");
        }

        std::filesystem::remove(path, error);
        std::filesystem::remove(numeric_path, error);
        std::filesystem::remove(corrupt_path, error);
        return 0;
    } catch(const std::exception& exception) {
        return (std::cerr << exception.what() << '\n', 1);
    }
}

#else
int main() { return 0; }
#endif
