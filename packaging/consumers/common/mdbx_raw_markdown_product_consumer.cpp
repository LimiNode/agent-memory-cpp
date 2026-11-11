#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>

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
            const auto candidate = root / ("agent-memory-raw-markdown-consumer-" +
                std::to_string(random()) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
        }
        throw std::runtime_error("unable to create a temporary directory");
    }
    ~TemporaryDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }
    const std::filesystem::path& path() const noexcept { return path_; }
private:
    std::filesystem::path path_;
};

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary;
        const auto database = temporary.path() / "memory.mdbx";
        const auto cas = temporary.path() / "cas";
        const std::string body =
            "# Product path\n\nThe exact retained Markdown bytes are authoritative.\n";
        const agent_memory::ScopeId scope{"raw-markdown-scope"};
        const agent_memory::ResourceId resource{"resource:raw-markdown"};
        auto context = agent_memory::MdbxStorageContext::open(database.string());
        {
            agent_memory::RawMarkdownProductIngestionOptions options;
            options.context = context;
            options.table_prefix = "raw_catalog_test";
            options.raw_cas_root = cas;
            options.max_range_bytes = 64U * 1024U;
            agent_memory::MdbxRawMarkdownProductIngestion ingestion(std::move(options));
            const auto imported = ingestion.import(agent_memory::ObservedResource{
                scope, resource, "https://example.test/product.md", body,
                agent_memory::SourceTextOrigin::OriginalText, std::nullopt, 1
            }, 1);
            if(imported.status !=
               agent_memory::RawMarkdownProductImportStatus::Published) {
                throw std::runtime_error(
                    "installed product import failed: " + imported.diagnostic);
            }

            const auto hits = ingestion.retrieve_and_hydrate(
                scope,
                agent_memory::RetrievalQuery{"authoritative", std::nullopt, 8, {}},
                4096);
            bool hydrated = false;
            for(const auto& hit : hits) {
                if(!hit.hydration.bytes.empty() &&
                   hit.hydration.status ==
                       agent_memory::AuthoritativeArticleHydrationStatus::Hydrated) {
                    hydrated = hit.hydration.bytes == body.substr(
                        hit.hydration.source_range.offset, hit.hydration.source_range.length);
                }
            }
            if(!hydrated) throw std::runtime_error("installed product hydration failed");

            agent_memory::RawMarkdownProductIngestionOptions negative_options;
            negative_options.context = context;
            negative_options.table_prefix = "raw_catalog_test";
            negative_options.raw_cas_root = temporary.path() / "missing-cas";
            agent_memory::MdbxRawMarkdownProductIngestion missing_cas(
                std::move(negative_options));
            const auto unavailable = missing_cas.retrieve_and_hydrate(
                scope,
                agent_memory::RetrievalQuery{"authoritative", std::nullopt, 8, {}},
                4096);
            if(unavailable.size() != 1 ||
               unavailable.front().candidate.knowledge_unit_id !=
                   imported.resource->raw_units.front()) {
                throw std::runtime_error("missing-CAS candidate was not observed");
            }
            for(const auto& hit : unavailable) {
                if(hit.hydration.status !=
                       agent_memory::AuthoritativeArticleHydrationStatus::Missing ||
                   !hit.hydration.bytes.empty() ||
                   hit.hydration.diagnostic_code !=
                       agent_memory::AuthoritativeArticleHydrationDiagnostic::CasMissing) {
                    throw std::runtime_error(
                        "missing CAS unexpectedly exposed product bytes");
                }
            }
        }

        context.reset();
        context = agent_memory::MdbxStorageContext::open(database.string());
        agent_memory::RawMarkdownProductIngestionOptions reopened_options;
        reopened_options.context = context;
        reopened_options.table_prefix = "raw_catalog_test";
        reopened_options.raw_cas_root = cas;
        agent_memory::MdbxRawMarkdownProductIngestion reopened(
            std::move(reopened_options));
        const auto reopened_hits = reopened.retrieve_and_hydrate(
            scope,
            agent_memory::RetrievalQuery{"authoritative", std::nullopt, 8, {}},
            4096);
        for(const auto& hit : reopened_hits) {
            if(hit.hydration.status ==
                   agent_memory::AuthoritativeArticleHydrationStatus::Hydrated &&
               !hit.hydration.bytes.empty()) {
                return hit.hydration.bytes == body.substr(
                    hit.hydration.source_range.offset, hit.hydration.source_range.length) ? 0 : 1;
            }
        }
        return 1;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
