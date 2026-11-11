#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path unique_path(const char* stem) {
    const auto stamp = std::chrono::high_resolution_clock::now()
        .time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        (std::string{stem} + std::to_string(stamp) + ".mdbx");
}

RawMarkdownProductIngestionOptions options(
    const std::filesystem::path& database,
    const std::filesystem::path& cas,
    const std::shared_ptr<MdbxStorageContext>& context
) {
    RawMarkdownProductIngestionOptions result;
    result.context = context;
    result.table_prefix = "raw_catalog_test";
    result.raw_cas_root = cas;
    result.max_range_bytes = 64U * 1024U;
    result.max_verified_cas_bytes = 8U * 1024U * 1024U;
    (void)database;
    return result;
}

} // namespace

int main() {
    const auto database = unique_path("agent_memory_raw_markdown_product_");
    const auto cas = database.parent_path() / (database.stem().string() + "_cas");
    std::error_code error;
    std::filesystem::remove(database, error);
    std::filesystem::remove_all(cas, error);

    const ScopeId scope{"raw-markdown-scope"};
    const ResourceId resource{"resource:raw-markdown"};
    const std::string body_v1 =
        "# Product path\n\nThe exact retained Markdown bytes are authoritative.\n";
    const std::string body_v2 =
        "# Product path\n\nThe revised Markdown evidence remains revision bound.\n";

    {
        auto context = MdbxStorageContext::open(database.string());
        auto ingestion = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto first = ingestion.import(ObservedResource{
            scope, resource, "https://example.test/product.md", body_v1,
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 1);
        if(first.status != RawMarkdownProductImportStatus::Published) {
            throw std::runtime_error("Raw Markdown import was not published: " + first.diagnostic);
        }
        expect(first.source_revision_id && first.artifact_binding_id &&
               first.resource_artifact_binding,
               "published import omitted provenance identities");

        const auto replay = ingestion.import(ObservedResource{
            scope, resource, "https://example.test/product.md", body_v1,
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 1);
        expect(replay.status == RawMarkdownProductImportStatus::Published,
               "identical Raw Markdown replay was not idempotent");
        expect(replay.resource && replay.resource->revision.generation == 1,
               "identical replay advanced the source generation");

        const auto hits = ingestion.retrieve_and_hydrate(
            scope, RetrievalQuery{"authoritative", std::nullopt, 8, {}}, 4096);
        bool hydrated = false;
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty() ||
                   hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated,
                   "failed product hydration exposed bytes");
            if(hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated) {
                hydrated = true;
                expect(!hit.hydration.bytes.empty(),
                       "Hydrated result omitted source bytes");
                expect(body_v1.find(hit.hydration.bytes) != std::string::npos,
                       "hydrated bytes did not belong to the retained Markdown body");
            }
        }
        expect(hydrated, "lexical candidate did not reach authoritative hydration");

        const auto second = ingestion.import(ObservedResource{
            scope, resource, "https://example.test/product.md", body_v2,
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 2);
        expect(second.status == RawMarkdownProductImportStatus::Published &&
               second.resource && second.resource->revision.generation == 2,
               "Markdown update did not publish a new revision");
        const auto updated = ingestion.retrieve_and_hydrate(
            scope, RetrievalQuery{"revised evidence", std::nullopt, 8, {}}, 4096);
        bool found_v2 = false;
        for(const auto& hit : updated) {
            expect(hit.hydration.bytes.empty() ||
                   hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated,
                   "updated failed hydration exposed bytes");
            if(hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated) {
                found_v2 = body_v2.find(hit.hydration.bytes) != std::string::npos;
            }
        }
        expect(found_v2, "updated Markdown revision was not hydrated");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto reopened = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto hits = reopened.retrieve_and_hydrate(
            scope, RetrievalQuery{"revised evidence", std::nullopt, 8, {}}, 4096);
        bool hydrated = false;
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty() ||
                   hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated,
                   "reopen failure exposed bytes");
            hydrated = hydrated ||
                hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated;
        }
        expect(hydrated, "reopen lost the published Raw Markdown product path");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto fault_options = options(database, cas, context);
        fault_options.catalog_fault_injector = [](ArtifactCatalogFaultPoint point) {
            if(point == ArtifactCatalogFaultPoint::BeforeCasPublication) {
                throw std::runtime_error("deterministic pre-CAS interruption");
            }
        };
        auto interrupted = MdbxRawMarkdownProductIngestion(std::move(fault_options));
        const auto pending = interrupted.import(ObservedResource{
            scope, ResourceId{"resource:before-cas"}, "https://example.test/before-cas.md",
            "# Before CAS\n", SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(pending.status == RawMarkdownProductImportStatus::Incomplete,
               "pre-CAS interruption was reported as published");
        const auto hits = interrupted.retrieve_and_hydrate(
            scope, RetrievalQuery{"Before CAS", std::nullopt, 8, {}}, 4096);
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty() &&
                       hit.hydration.status != AuthoritativeArticleHydrationStatus::Hydrated,
                   "pre-CAS interruption exposed hydrated bytes");
        }
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto fault_options = options(database, cas, context);
        fault_options.catalog_fault_injector = [](ArtifactCatalogFaultPoint point) {
            if(point == ArtifactCatalogFaultPoint::AfterCasPublication) {
                throw std::runtime_error("deterministic post-CAS interruption");
            }
        };
        auto interrupted = MdbxRawMarkdownProductIngestion(std::move(fault_options));
        const auto pending = interrupted.import(ObservedResource{
            scope, ResourceId{"resource:after-cas"}, "https://example.test/after-cas.md",
            "# After CAS\n", SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(pending.status == RawMarkdownProductImportStatus::Incomplete,
               "post-CAS interruption was reported as published");
        const auto hits = interrupted.retrieve_and_hydrate(
            scope, RetrievalQuery{"After CAS", std::nullopt, 8, {}}, 4096);
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty() &&
                       hit.hydration.status != AuthoritativeArticleHydrationStatus::Hydrated,
                   "post-CAS interruption exposed hydrated bytes");
        }
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto fault_options = options(database, cas, context);
        fault_options.catalog_fault_injector = [](ArtifactCatalogFaultPoint point) {
            if(point == ArtifactCatalogFaultPoint::AfterCatalogCommit) {
                throw std::runtime_error("deterministic catalog interruption");
            }
        };
        auto interrupted = MdbxRawMarkdownProductIngestion(std::move(fault_options));
        const auto pending = interrupted.import(ObservedResource{
            scope, ResourceId{"resource:fault"}, "https://example.test/fault.md",
            "# Fault recovery\n\nThe catalog receipt is replayable.\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(pending.status == RawMarkdownProductImportStatus::Incomplete,
               "catalog interruption was reported as published");
        const auto hits = interrupted.retrieve_and_hydrate(
            scope, RetrievalQuery{"catalog receipt", std::nullopt, 8, {}}, 4096);
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty() &&
                       hit.hydration.status != AuthoritativeArticleHydrationStatus::Hydrated,
                   "catalog interruption exposed hydrated bytes");
        }
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto recovered = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto replay = recovered.import(ObservedResource{
            scope, ResourceId{"resource:fault"}, "https://example.test/fault.md",
            "# Fault recovery\n\nThe catalog receipt is replayable.\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(replay.status == RawMarkdownProductImportStatus::Published,
               "catalog interruption did not recover on replay");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto recovered = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto before_cas_replay = recovered.import(ObservedResource{
            scope, ResourceId{"resource:before-cas"},
            "https://example.test/before-cas.md", "# Before CAS\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(before_cas_replay.status == RawMarkdownProductImportStatus::Published,
               "pre-CAS interruption did not recover on replay");
        const auto after_cas_replay = recovered.import(ObservedResource{
            scope, ResourceId{"resource:after-cas"},
            "https://example.test/after-cas.md", "# After CAS\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(after_cas_replay.status == RawMarkdownProductImportStatus::Published,
               "post-CAS interruption did not recover on replay");
    }

    {
        std::error_code missing_error;
        std::filesystem::remove_all(cas, missing_error);
        auto context = MdbxStorageContext::open(database.string());
        auto missing = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto hits = missing.retrieve_and_hydrate(
            scope, RetrievalQuery{"revised evidence", std::nullopt, 8, {}}, 4096);
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty(),
                   "missing CAS body escaped through product hydration");
            expect(hit.hydration.status != AuthoritativeArticleHydrationStatus::Hydrated,
                   "missing CAS body was reported as Hydrated");
        }
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto invalid = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto result = invalid.import(ObservedResource{
            scope, ResourceId{"resource:html"}, "https://example.test/page.html",
            "<p>not Markdown</p>", SourceTextOrigin::OriginalText, std::nullopt, 1
        });
        expect(result.status == RawMarkdownProductImportStatus::InvalidArgument,
               "non-Markdown input crossed the Raw Markdown boundary");

        const auto invalid_scope = invalid.import(ObservedResource{
            ScopeId{}, ResourceId{"resource:invalid-scope"},
            "https://example.test/invalid-scope.md", "# invalid\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        });
        expect(invalid_scope.status == RawMarkdownProductImportStatus::InvalidArgument,
               "empty scope crossed the Raw Markdown boundary");
    }

    std::filesystem::remove_all(cas, error);
    std::filesystem::remove(database, error);
    std::cout << "raw Markdown product ingestion: PASS\n";
    return 0;
}

#else

int main() { return 0; }

#endif
