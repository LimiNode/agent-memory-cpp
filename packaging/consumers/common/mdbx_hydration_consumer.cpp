#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativeArticleHydration.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceArtifactBindingStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#include <filesystem>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

namespace {

class TemporaryDirectory final {
public:
    TemporaryDirectory() {
        const auto root = std::filesystem::temp_directory_path();
        std::random_device random;
        for(std::size_t attempt = 0; attempt != 32; ++attempt) {
            const auto candidate = root / ("agent-memory-hydration-consumer-" +
                std::to_string(random()) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
        }
        throw std::runtime_error("unable to create a reserved temporary directory");
    }
    ~TemporaryDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }
    TemporaryDirectory(const TemporaryDirectory&) = delete;
    TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;
    const std::filesystem::path& path() const noexcept { return path_; }
private:
    std::filesystem::path path_;
};

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary;
        const auto database = temporary.path() / "database.mdbx";
        const auto cas = temporary.path() / "cas";
        const std::string prefix = "installed_hydration";
        const agent_memory::ScopeId scope{"consumer-scope"};
        const agent_memory::ResourceId resource{"resource:consumer"};
        const std::string body = "# Installed article\n\nExact bytes.\n";
        auto context = agent_memory::MdbxStorageContext::open(database.string());

        agent_memory::MdbxResourceImporterOptions importer_options;
        importer_options.context = context;
        importer_options.table_prefix = prefix;
        importer_options.publication_profile =
            agent_memory::ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        agent_memory::MdbxResourceImporter importer(std::move(importer_options));
        const auto imported = importer.import(agent_memory::ObservedResource{
            scope, resource, "https://example.test/consumer.md", body,
            agent_memory::SourceTextOrigin::OriginalText, std::nullopt, 1
        });

        agent_memory::MdbxKnowledgeUnitStore units(
            agent_memory::MdbxKnowledgeUnitStoreOptions{
                {}, prefix, false, agent_memory::kDefaultMdbxMaxDbs, context, {}
            });
        agent_memory::KnowledgeUnitEnvelope envelope;
        for(const auto id : units.scan_by_scope(scope)) {
            const auto candidate = units.find(id);
            if(candidate && !candidate->sources.empty() &&
               candidate->sources.front().resource_revision) {
                envelope = *candidate;
                break;
            }
        }
        if(envelope.id.empty()) return 1;
        const auto& source = envelope.sources.front();

        agent_memory::MdbxProjectionStore projections(
            agent_memory::MdbxProjectionStoreOptions{
                {}, prefix, false, agent_memory::kDefaultMdbxMaxDbs, context
            });
        projections.put(agent_memory::make_original_projection(
            envelope, envelope.revision));
        agent_memory::MdbxResourceManifestStorage manifests(
            agent_memory::MdbxResourceManifestStorageOptions{
                {}, prefix, false, agent_memory::kDefaultMdbxMaxDbs, context
            });
        const auto expected_manifest = manifests.find_manifest(resource);
        if(!expected_manifest) return 1;
        auto manifest = *expected_manifest;
        manifest.schema = agent_memory::ResourceManifestSchema{
            "agent_memory.m0_resource_importer.raw_chunks_original@agent_memory", 1
        };
        const auto unit_key = std::string{"knowledge_unit:"} +
            std::to_string(envelope.id.value());
        const auto projection_key = std::string{"original_projection:"} +
            std::to_string(envelope.id.value());
        const auto has_key = [&manifest](const std::string& key) {
            for(const auto& record : manifest.records) {
                if(record.kind == agent_memory::DerivedRecordKind::Custom &&
                   record.key == key) {
                    return true;
                }
            }
            return false;
        };
        auto ordinal = static_cast<std::uint32_t>(manifest.records.size());
        if(!has_key(unit_key)) {
            manifest.records.push_back(agent_memory::DerivedRecordRef{
                agent_memory::DerivedRecordKind::Custom, agent_memory::ChunkId{},
                unit_key, ordinal++
            });
        }
        if(!has_key(projection_key)) {
            manifest.records.push_back(agent_memory::DerivedRecordRef{
                agent_memory::DerivedRecordKind::Custom, agent_memory::ChunkId{},
                projection_key, ordinal
            });
        }
        manifests.upsert_manifest_if_unchanged(
            std::move(manifest), expected_manifest);

        agent_memory::MdbxArtifactCatalog catalog(
            agent_memory::MdbxArtifactCatalogOptions{
                {}, prefix, false, agent_memory::kDefaultMdbxMaxDbs, context,
                cas, {}, 64U * 1024U * 1024U, {}
            });
        const auto retained = catalog.ingest(agent_memory::ArtifactCatalogIngestRequest{
            agent_memory::SourceRevisionId{"consumer-source@1"},
            agent_memory::ArtifactBindingId{"consumer-binding@1"},
            "source-original", agent_memory::ArtifactRetentionClass::SourceOriginal,
            std::string{"text/markdown"}, std::string{"consumer.md"}, 1, body
        });
        if(retained.status != agent_memory::ArtifactCatalogStatus::Ok) return 1;
        agent_memory::MdbxResourceArtifactBindingStore joins(
            agent_memory::MdbxResourceArtifactBindingStoreOptions{
                {}, prefix, false, agent_memory::kDefaultMdbxMaxDbs, context
            });
        joins.put(agent_memory::ResourceArtifactBinding{
            *source.resource_revision,
            agent_memory::SourceRevisionId{"consumer-source@1"},
            agent_memory::ArtifactBindingId{"consumer-binding@1"}
        });

        agent_memory::MdbxAuthoritativeArticleHydrationOptions hydration_options;
        hydration_options.context = context;
        hydration_options.table_prefix = prefix;
        hydration_options.raw_cas_root = cas;
        agent_memory::MdbxAuthoritativeArticleHydrator hydrator(
            std::move(hydration_options));
        agent_memory::MdbxAuthoritativeArticleHydrationRequest request;
        request.candidate.knowledge_unit_id = envelope.id;
        request.candidate.unit_revision = envelope.revision;
        request.candidate.projection_kind = agent_memory::ProjectionKind::Original;
        request.scope_id = scope;
        request.source_range = source.excerpt;
        const auto result = hydrator.hydrate(request);
        (void)imported;
        if(result.status !=
               agent_memory::AuthoritativeArticleHydrationStatus::Hydrated ||
           result.bytes != body.substr(
               static_cast<std::size_t>(source.excerpt.offset),
               static_cast<std::size_t>(source.excerpt.length))) {
            throw std::runtime_error(
                "hydration failed: status=" +
                std::to_string(static_cast<int>(result.status)) +
                " diagnostic=" + result.diagnostic);
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
