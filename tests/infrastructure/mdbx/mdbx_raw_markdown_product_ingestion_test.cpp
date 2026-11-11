#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/domain/Sha256.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <fstream>
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

    auto assert_fail_closed = [](const std::vector<RawMarkdownProductHit>& hits,
                                 const char* message,
                                 bool require_candidate,
                                 AuthoritativeArticleHydrationDiagnostic code =
                                     AuthoritativeArticleHydrationDiagnostic::BindingMissing) {
        if(require_candidate) expect(hits.size() == 1, message);
        else expect(hits.empty(), message);
        for(const auto& hit : hits) {
            expect(hit.hydration.bytes.empty() &&
                   hit.hydration.status == (code ==
                       AuthoritativeArticleHydrationDiagnostic::CasCorrupt
                           ? AuthoritativeArticleHydrationStatus::Corrupt
                           : AuthoritativeArticleHydrationStatus::Missing),
                   message);
            expect(hit.candidate.freshness == RevisionBoundFreshness::Current &&
                   hit.hydration.diagnostic_code == code &&
                   !hit.hydration.diagnostic.empty(), message);
        }
    };
    const auto assert_recovered = [](MdbxRawMarkdownProductIngestion& ingestion,
                                      const ScopeId& recovered_scope,
                                      const RawMarkdownProductImportResult& replay,
                                      const std::string& body) {
        expect(replay.status == RawMarkdownProductImportStatus::Published &&
               replay.resource && replay.resource->raw_units.size() == 1,
               "replay did not complete source-backed product publication");
        const auto hits = ingestion.retrieve_and_hydrate(
            recovered_scope, RetrievalQuery{body, std::nullopt, 8, {}}, 4096);
        expect(hits.size() == 1 &&
               hits.front().candidate.knowledge_unit_id == replay.resource->raw_units.front() &&
               hits.front().hydration.status == AuthoritativeArticleHydrationStatus::Hydrated &&
               hits.front().hydration.bytes == body,
               "recovered product did not hydrate its exact published Chunk");
    };

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
        std::optional<RawMarkdownProductHit> old_hit;
        for(const auto& hit : hits) {
            old_hit = hit;
            expect(hit.hydration.bytes.empty() ||
                   hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated,
                   "failed product hydration exposed bytes");
            if(hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated) {
                hydrated = true;
                expect(!hit.hydration.bytes.empty(),
                       "Hydrated result omitted source bytes");
                expect(hit.hydration.bytes == body_v1.substr(
                           hit.hydration.source_range.offset,
                           hit.hydration.source_range.length),
                       "hydrated bytes did not match the exact retained range");
            }
        }
        expect(hydrated, "lexical candidate did not reach authoritative hydration");
        expect(old_hit.has_value(), "initial import produced no retained candidate identity");

        const auto second = ingestion.import(ObservedResource{
            scope, resource, "https://example.test/product.md", body_v2,
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 2);
        expect(second.status == RawMarkdownProductImportStatus::Published &&
               second.resource && second.resource->revision.generation == 2,
               "Markdown update did not publish a new revision");
        expect(second.resource->revision.body_digest &&
               second.resource->revision.body_digest->bytes == sha256(body_v2),
               "A@2 publication omitted its exact SHA-256 digest");
        const auto updated = ingestion.retrieve_and_hydrate(
            scope, RetrievalQuery{"revised evidence", std::nullopt, 8, {}}, 4096);
        bool found_v2 = false;
        for(const auto& hit : updated) {
            expect(hit.hydration.bytes.empty() ||
                   hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated,
                   "updated failed hydration exposed bytes");
            if(hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated) {
                const auto& range = hit.hydration.source_range;
                expect(range.offset <= body_v2.size() &&
                       range.length <= body_v2.size() - range.offset,
                       "updated hydration returned an invalid source range");
                found_v2 = hit.hydration.bytes ==
                    body_v2.substr(static_cast<std::size_t>(range.offset),
                                   static_cast<std::size_t>(range.length));
            }
        }
        expect(found_v2, "updated Markdown revision was not hydrated");

        MdbxAuthoritativeArticleHydrationOptions stale_options;
        stale_options.context = context;
        stale_options.table_prefix = "raw_catalog_test";
        stale_options.raw_cas_root = cas;
        stale_options.expected_manifest_schema_id =
            "agent_memory.m0_resource_importer.raw_chunks_original@raw_catalog_test";
        MdbxAuthoritativeArticleHydrator stale_hydrator(std::move(stale_options));
        MdbxAuthoritativeArticleHydrationRequest stale_request;
        stale_request.candidate = ProjectionAwareLexicalCandidate{
            old_hit->candidate.knowledge_unit_id,
            old_hit->candidate.unit_revision,
            old_hit->candidate.projection_kind,
            old_hit->candidate.score
        };
        stale_request.scope_id = scope;
        stale_request.source_range = old_hit->candidate.source
            ? old_hit->candidate.source->excerpt : TextRange{};
        stale_request.claimed_resource_revision = old_hit->candidate.source
            ? old_hit->candidate.source->resource_revision : std::nullopt;
        stale_request.max_bytes = 4096;
        const auto stale = stale_hydrator.hydrate(stale_request);
        expect(stale.status == AuthoritativeArticleHydrationStatus::Stale &&
               stale.bytes.empty(),
               "A@1 candidate remained hydratable after A@2 publication");
        StandardTokenizer tokenizer;
        ProjectionAwareLexicalRetrieverOptions admission_options;
        admission_options.expected_manifest_schema_id =
            "agent_memory.m0_resource_importer.raw_chunks_original@raw_catalog_test";
        MdbxSnapshotRevisionBoundRetriever admission(
            context, scope, tokenizer, admission_options, "raw_catalog_test");
        const auto stale_admission = admission.admit_candidate(stale_request.candidate);
        expect(stale_admission.freshness == RevisionBoundFreshness::Stale &&
               stale_admission.text.empty(), "old A@1 admission was not Stale");

        const auto timestamp_replay = ingestion.import(ObservedResource{
            scope, resource, "https://example.test/product.md", body_v2,
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 999);
        expect(timestamp_replay.status == RawMarkdownProductImportStatus::Conflict,
               "changed binding timestamp was not rejected as a conflicting replay");
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
        auto bounded_options = options(database, cas, context);
        bounded_options.max_indexed_projection_bytes = 1;
        auto bounded = MdbxRawMarkdownProductIngestion(std::move(bounded_options));
        bool budget_exceeded = false;
        try {
            (void)bounded.retrieve_and_hydrate(
                scope, RetrievalQuery{"revised evidence", std::nullopt, 8, {}}, 4096);
        } catch(const ProjectionAwareLexicalRetrievalBudgetExceeded&) {
            budget_exceeded = true;
        }
        expect(budget_exceeded,
               "lexical budget overflow returned a partial result instead of failing closed");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId race_scope{"interleaved"};
        const ResourceId race_resource{"resource:interleaved"};
        std::optional<RawMarkdownProductHit> old_candidate;
        auto delayed_options = options(database, cas, context);
        auto newer = MdbxRawMarkdownProductIngestion(options(database, cas, context));
        delayed_options.before_final_observation_hook_for_testing = [&] {
            const auto old_hits = newer.retrieve_and_hydrate(
                race_scope, RetrievalQuery{"oldrevision", std::nullopt, 8, {}}, 4096);
            expect(old_hits.size() == 1 &&
                   old_hits.front().hydration.status ==
                       AuthoritativeArticleHydrationStatus::Hydrated,
                   "interleaving did not pause at the joined A@1 boundary");
            old_candidate = old_hits.front();
            const auto replacement = newer.import(ObservedResource{
                race_scope, race_resource, "https://example.test/interleaved.md",
                "# newrevision\n", SourceTextOrigin::OriginalText, std::nullopt, 1
            }, 8);
            expect(replacement.status == RawMarkdownProductImportStatus::Published &&
                   replacement.resource->revision.generation == 2,
                   "interleaved A@2 did not complete product publication");
        };
        auto delayed = MdbxRawMarkdownProductIngestion(std::move(delayed_options));
        const auto superseded = delayed.import(ObservedResource{
            race_scope, race_resource, "https://example.test/interleaved.md",
            "# oldrevision\n", SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 7);
        if(!(old_candidate &&
               superseded.status == RawMarkdownProductImportStatus::Conflict &&
               superseded.diagnostic.find("superseded") != std::string::npos)) {
            throw std::runtime_error("superseded A@1 returned status " +
                std::to_string(static_cast<int>(superseded.status)) + ": " +
                superseded.diagnostic);
        }
        MdbxAuthoritativeArticleHydrationOptions hydration_options;
        hydration_options.context = context;
        hydration_options.table_prefix = "raw_catalog_test";
        hydration_options.raw_cas_root = cas;
        hydration_options.expected_manifest_schema_id =
            "agent_memory.m0_resource_importer.raw_chunks_original@raw_catalog_test";
        MdbxAuthoritativeArticleHydrator hydrator(std::move(hydration_options));
        MdbxAuthoritativeArticleHydrationRequest old_request;
        old_request.candidate = ProjectionAwareLexicalCandidate{
            old_candidate->candidate.knowledge_unit_id,
            old_candidate->candidate.unit_revision, ProjectionKind::Original, 1.0F};
        old_request.scope_id = race_scope;
        old_request.source_range = old_candidate->hydration.source_range;
        old_request.claimed_resource_revision = old_candidate->hydration.resource_revision;
        old_request.max_bytes = 4096;
        const auto rejected = hydrator.hydrate(old_request);
        expect(rejected.status == AuthoritativeArticleHydrationStatus::Stale &&
               rejected.bytes.empty(), "superseded A@1 hydration returned bytes");
        const auto current = newer.retrieve_and_hydrate(
            race_scope, RetrievalQuery{"newrevision", std::nullopt, 8, {}}, 4096);
        expect(current.size() == 1 &&
               current.front().candidate.root_generation == 2 &&
               current.front().hydration.bytes == "# newrevision\n",
               "interleaved A@2 did not remain current and exactly hydratable");
    }

    {
        // Corrupt the immutable CAS object after a successful publication.
        // The catalog and manifest remain present, so the candidate must be
        // observable, but complete-object verification must reject its bytes.
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{"corrupt-cas-scope"};
        auto reader = MdbxRawMarkdownProductIngestion(options(database, cas, context));
        const auto published = reader.import(ObservedResource{
            scope, ResourceId{"resource:corrupt-cas"},
            "https://example.test/corrupt-cas.md", "# Corrupt CAS\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 4);
        expect(published.status == RawMarkdownProductImportStatus::Published &&
               published.artifact, "corrupt-CAS fixture was not published");
        const auto& artifact_id = published.artifact->artifact.id.value();
        const auto cas_file = cas / "sha256" / artifact_id.substr(7, 2) /
            (artifact_id.substr(7) + ".raw");
        std::ofstream corrupt(cas_file, std::ios::binary | std::ios::trunc);
        corrupt << "not the published bytes";
        corrupt.close();
        const auto hits = reader.retrieve_and_hydrate(
            scope, RetrievalQuery{"Corrupt CAS", std::nullopt, 8, {}}, 4096);
        assert_fail_closed(hits, "corrupt CAS bytes escaped authoritative hydration", true,
                           AuthoritativeArticleHydrationDiagnostic::CasCorrupt);
        expect(hits.front().candidate.root_resource_id == ResourceId{"resource:corrupt-cas"} &&
               hits.front().hydration.status == AuthoritativeArticleHydrationStatus::Corrupt &&
               hits.front().hydration.diagnostic_code ==
                   AuthoritativeArticleHydrationDiagnostic::CasCorrupt,
               "corrupt CAS did not exercise exact candidate verification");
    }

    const auto run_importer_fault = [&](ResourceImporterFaultPoint point,
                                        const char* label,
                                        bool candidate_visible) {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{std::string{"fault-"} + label};
        auto fault_options = options(database, cas, context);
        std::uint64_t clock = 1000;
        fault_options.importer_ownership_lease_ms = 1;
        fault_options.importer_clock_ms = [&clock] { return clock; };
        fault_options.importer_fault_injector = [point](ResourceImporterFaultPoint actual) {
            if(actual == point) throw std::runtime_error("deterministic importer interruption");
        };
        const ResourceId id{std::string{"imp:"} + label};
        auto interrupted = std::make_unique<MdbxRawMarkdownProductIngestion>(std::move(fault_options));
        const auto pending = interrupted->import(ObservedResource{
            scope, id, "https://example.test/importer-" + std::string{label} + ".md",
            "# Importer fault " + std::string{label} + "\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 5);
        expect(pending.status == RawMarkdownProductImportStatus::Incomplete,
               "importer interruption was reported as published");
        const auto hits = interrupted->retrieve_and_hydrate(
            scope, RetrievalQuery{"Importer fault " + std::string{label},
                                  std::nullopt, 8, {}}, 4096);
        assert_fail_closed(hits, "importer interruption exposed hydrated bytes",
                           candidate_visible);
        if(candidate_visible) {
            expect(hits.front().candidate.root_resource_id == id &&
                   hits.front().hydration.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::BindingMissing,
                   "post-manifest fault did not exercise the missing typed join");
        }
        clock += 2;
        interrupted.reset();
        context.reset();
        context = MdbxStorageContext::open(database.string());
        auto recovery_options = options(database, cas, context);
        recovery_options.importer_ownership_lease_ms = 1;
        recovery_options.importer_clock_ms = [&clock] { return clock; };
        auto recovered = MdbxRawMarkdownProductIngestion(std::move(recovery_options));
        const auto replay = recovered.import(ObservedResource{
            scope, id, "https://example.test/importer-" + std::string{label} + ".md",
            "# Importer fault " + std::string{label} + "\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 5);
        expect(replay.status == RawMarkdownProductImportStatus::Published,
               "importer interruption did not recover through replay");
        assert_recovered(recovered, scope, replay,
                         "# Importer fault " + std::string{label} + "\n");
    };

    run_importer_fault(ResourceImporterFaultPoint::AfterIntent,
                       "after-intent", false);
    run_importer_fault(ResourceImporterFaultPoint::AfterRawUnitPublication,
                       "after-raw", false);
    run_importer_fault(ResourceImporterFaultPoint::BeforeManifestPublication,
                       "before-manifest", false);
    run_importer_fault(ResourceImporterFaultPoint::AfterManifestPublication,
                       "after-manifest", true);

    {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{"before-join-scope"};
        auto fault_options = options(database, cas, context);
        fault_options.before_join_publication_hook_for_testing = [] {
            throw std::runtime_error("deterministic interruption before typed join");
        };
        auto interrupted = std::make_unique<MdbxRawMarkdownProductIngestion>(std::move(fault_options));
        const auto pending = interrupted->import(ObservedResource{
            scope, ResourceId{"resource:before-join"},
            "https://example.test/before-join.md", "# Before join\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 6);
        expect(pending.status == RawMarkdownProductImportStatus::Incomplete,
               "pre-join interruption was reported as published");
        const auto hits = interrupted->retrieve_and_hydrate(
            scope, RetrievalQuery{"Before join", std::nullopt, 8, {}}, 4096);
        assert_fail_closed(hits, "pre-join interruption exposed hydrated bytes", true);
        expect(hits.front().candidate.root_resource_id == ResourceId{"resource:before-join"} &&
               hits.front().hydration.diagnostic_code ==
                   AuthoritativeArticleHydrationDiagnostic::BindingMissing,
               "pre-join fault did not exercise the missing typed join");
        interrupted.reset();
        context.reset();
        context = MdbxStorageContext::open(database.string());
        auto recovered = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto replay = recovered.import(ObservedResource{
            scope, ResourceId{"resource:before-join"},
            "https://example.test/before-join.md", "# Before join\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 6);
        expect(replay.status == RawMarkdownProductImportStatus::Published,
               "pre-join interruption did not recover through replay");
        assert_recovered(recovered, scope, replay, "# Before join\n");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{"before-cas-scope"};
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
        assert_fail_closed(hits, "pre-CAS interruption exposed hydrated bytes", true);
        expect(pending.resource && hits.front().candidate.knowledge_unit_id ==
                   pending.resource->raw_units.front(), "pre-CAS candidate identity changed");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{"after-cas-scope"};
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
        assert_fail_closed(hits, "post-CAS interruption exposed hydrated bytes", true);
        expect(pending.resource && hits.front().candidate.knowledge_unit_id ==
                   pending.resource->raw_units.front(), "post-CAS candidate identity changed");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{"catalog-fault-scope"};
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
        assert_fail_closed(hits, "catalog interruption exposed hydrated bytes", true);
        expect(pending.resource && hits.front().candidate.knowledge_unit_id ==
                   pending.resource->raw_units.front(), "post-catalog candidate identity changed");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        const ScopeId scope{"catalog-fault-scope"};
        auto recovered = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto replay = recovered.import(ObservedResource{
            scope, ResourceId{"resource:fault"}, "https://example.test/fault.md",
            "# Fault recovery\n\nThe catalog receipt is replayable.\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(replay.status == RawMarkdownProductImportStatus::Published,
               "catalog interruption did not recover on replay");
        assert_recovered(recovered, scope, replay,
                         "# Fault recovery\n\nThe catalog receipt is replayable.\n");
    }

    {
        auto context = MdbxStorageContext::open(database.string());
        auto recovered = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto before_cas_replay = recovered.import(ObservedResource{
            ScopeId{"before-cas-scope"}, ResourceId{"resource:before-cas"},
            "https://example.test/before-cas.md", "# Before CAS\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(before_cas_replay.status == RawMarkdownProductImportStatus::Published,
               "pre-CAS interruption did not recover on replay");
        assert_recovered(recovered, ScopeId{"before-cas-scope"}, before_cas_replay,
                         "# Before CAS\n");
        const auto after_cas_replay = recovered.import(ObservedResource{
            ScopeId{"after-cas-scope"}, ResourceId{"resource:after-cas"},
            "https://example.test/after-cas.md", "# After CAS\n",
            SourceTextOrigin::OriginalText, std::nullopt, 1
        }, 3);
        expect(after_cas_replay.status == RawMarkdownProductImportStatus::Published,
               "post-CAS interruption did not recover on replay");
        assert_recovered(recovered, ScopeId{"after-cas-scope"}, after_cas_replay,
                         "# After CAS\n");
    }

    {
        std::error_code missing_error;
        std::filesystem::remove_all(cas, missing_error);
        auto context = MdbxStorageContext::open(database.string());
        auto missing = MdbxRawMarkdownProductIngestion(
            options(database, cas, context));
        const auto hits = missing.retrieve_and_hydrate(
            scope, RetrievalQuery{"revised evidence", std::nullopt, 8, {}}, 4096);
        assert_fail_closed(hits, "missing CAS body escaped through product hydration", true,
                           AuthoritativeArticleHydrationDiagnostic::CasMissing);
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
