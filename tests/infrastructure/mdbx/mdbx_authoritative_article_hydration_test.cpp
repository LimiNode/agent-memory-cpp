#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativeArticleHydration.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceArtifactBindingStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <atomic>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <memory>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    static std::atomic_uint64_t sequence{0};
    const auto stamp = std::chrono::high_resolution_clock::now()
        .time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        (std::string{"agent_memory_authoritative_article_"} +
         std::to_string(stamp) + "_" +
         std::to_string(sequence.fetch_add(1, std::memory_order_relaxed)) +
         ".mdbx");
}

struct Fixture final {
    std::filesystem::path path = test_path();
    std::filesystem::path cas_root = path.parent_path() /
        (path.stem().string() + "_cas");
    std::string table_prefix = "authoritative_article";
    ScopeId scope{"article-scope"};
    ResourceId resource{"resource:article"};
    std::string body =
        "# Durable article\n\n"
        "The authoritative hydration path returns exact source bytes.\n\n"
        "## Details\n\n"
        "Currentness is checked again after the CAS read.\n";
    KnowledgeUnitId unit_id;
    std::uint64_t unit_revision = 0;
    TextRange range;
    ResourceRevisionRef revision;
};

MdbxResourceImporter make_importer(
    const std::shared_ptr<MdbxStorageContext>& context,
    const std::string& prefix) {
    MdbxResourceImporterOptions options;
    options.context = context;
    options.table_prefix = prefix;
    options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    return MdbxResourceImporter(std::move(options));
}

std::unique_ptr<MdbxAuthoritativeArticleHydrator> make_hydrator(
    const std::shared_ptr<MdbxStorageContext>& context,
    const Fixture& fixture,
    std::uint64_t max_verified_cas_bytes = 64U * 1024U * 1024U) {
    MdbxAuthoritativeArticleHydrationOptions options;
    options.context = context;
    options.table_prefix = fixture.table_prefix;
    options.raw_cas_root = fixture.cas_root;
    options.max_range_bytes = 4096;
    options.max_verified_cas_bytes = max_verified_cas_bytes;
    return std::make_unique<MdbxAuthoritativeArticleHydrator>(std::move(options));
}

void prepare(
    Fixture& fixture,
    const std::string& artifact_role = "source-original",
    std::optional<std::string> media_type = std::string{"text/markdown"}) {
    std::error_code error;
    std::filesystem::remove(fixture.path, error);
    std::filesystem::remove_all(fixture.cas_root, error);
    auto context = MdbxStorageContext::open(fixture.path.string());
    MdbxArtifactCatalog catalog(MdbxArtifactCatalogOptions{
        {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context,
        fixture.cas_root, {}, 64U * 1024U * 1024U, {}
    });
    auto importer = make_importer(context, fixture.table_prefix);
    const auto imported = importer.import(ObservedResource{
        fixture.scope, fixture.resource, "https://example.test/article.md",
        fixture.body, SourceTextOrigin::OriginalText, std::nullopt, 1
    });
    MdbxKnowledgeUnitStore units(MdbxKnowledgeUnitStoreOptions{
        {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context, {}
    });
    for(const auto& id : units.scan_by_scope(fixture.scope)) {
        const auto envelope = units.find(id);
        if(envelope && !envelope->sources.empty() &&
           envelope->sources.front().resource_revision) {
            fixture.unit_id = id;
            fixture.unit_revision = envelope->revision;
            const auto& source = envelope->sources.front();
            fixture.range = source.excerpt;
            fixture.revision = *source.resource_revision;
            break;
        }
    }
    expect(!fixture.unit_id.empty() && fixture.unit_revision != 0,
           "import did not publish a revision-bound article unit");
    expect(fixture.range.length != 0, "article source range is empty");
    const auto envelope = units.find(fixture.unit_id);
    expect(envelope.has_value(), "selected article unit disappeared");
    MdbxProjectionStore projections(MdbxProjectionStoreOptions{
        {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context
    });
    projections.put(make_original_projection(*envelope, envelope->revision));
    MdbxResourceManifestStorage manifests(MdbxResourceManifestStorageOptions{
        {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context
    });
    const auto expected_manifest = manifests.find_manifest(fixture.resource);
    expect(expected_manifest.has_value(), "article manifest disappeared");
    auto manifest = *expected_manifest;
    manifest.schema = ResourceManifestSchema{
        "agent_memory.m0_resource_importer.raw_chunks_original@agent_memory", 1
    };
    const auto unit_key = std::string{"knowledge_unit:"} +
        std::to_string(fixture.unit_id.value());
    const auto projection_key = std::string{"original_projection:"} +
        std::to_string(fixture.unit_id.value());
    const auto has_key = [&manifest](const std::string& key) {
        for(const auto& record : manifest.records) {
            if(record.kind == DerivedRecordKind::Custom && record.key == key) {
                return true;
            }
        }
        return false;
    };
    auto ordinal = static_cast<std::uint32_t>(manifest.records.size());
    if(!has_key(unit_key)) {
        manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom, ChunkId{}, unit_key, ordinal++
        });
    }
    if(!has_key(projection_key)) {
        manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom, ChunkId{}, projection_key, ordinal
        });
    }
    manifests.upsert_manifest_if_unchanged(std::move(manifest), expected_manifest);
    const auto artifact = catalog.ingest(ArtifactCatalogIngestRequest{
        SourceRevisionId{"article-source@1"}, ArtifactBindingId{"article-binding@1"},
        artifact_role, ArtifactRetentionClass::SourceOriginal,
        std::move(media_type), std::string{"article.md"}, 1, fixture.body
    });
    expect(artifact.status == ArtifactCatalogStatus::Ok && artifact.record,
           "catalog did not retain article bytes");
    MdbxResourceArtifactBindingStore joins(
        MdbxResourceArtifactBindingStoreOptions{
            {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context
        });
    joins.put(ResourceArtifactBinding{
        fixture.revision, SourceRevisionId{"article-source@1"},
        ArtifactBindingId{"article-binding@1"}
    });
    (void)imported;
}

MdbxAuthoritativeArticleHydrationRequest request_for(
    const Fixture& fixture) {
    MdbxAuthoritativeArticleHydrationRequest request;
    request.candidate.knowledge_unit_id = fixture.unit_id;
    request.candidate.unit_revision = fixture.unit_revision;
    request.candidate.projection_kind = ProjectionKind::Original;
    request.scope_id = fixture.scope;
    request.source_range = fixture.range;
    return request;
}

std::string binding_key(const ResourceRevisionRef& revision) {
    const auto append = [](std::string& out, std::string_view value) {
        out += std::to_string(value.size());
        out.push_back(':');
        out.append(value.data(), value.size());
    };
    std::string key;
    append(key, revision.resource_id.value());
    append(key, std::to_string(revision.generation));
    append(key, "sha256");
    std::string digest;
    digest.reserve(revision.body_digest.bytes.size());
    for(const auto byte : revision.body_digest.bytes) {
        digest.push_back(static_cast<char>(byte));
    }
    append(key, digest);
    return key;
}

} // namespace

int main() {
    Fixture fixture;
    try {
        prepare(fixture);
        auto context = MdbxStorageContext::open(fixture.path.string());
        auto hydrator = make_hydrator(context, fixture);
        const auto request = request_for(fixture);

        const auto hydrated = hydrator->hydrate(request);
        if(hydrated.status != AuthoritativeArticleHydrationStatus::Hydrated) {
            std::cerr << "initial hydration status="
                      << static_cast<int>(hydrated.status)
                      << " diagnostic=" << hydrated.diagnostic << '\n';
        }
        expect(hydrated.status == AuthoritativeArticleHydrationStatus::Hydrated,
               "authoritative article was not hydrated");
        expect(hydrated.bytes == fixture.body.substr(
                   static_cast<std::size_t>(fixture.range.offset),
                   static_cast<std::size_t>(fixture.range.length)),
               "hydrated bytes differ from the authoritative source range");
        expect(hydrated.source_revision_id && hydrated.artifact_binding_id,
               "hydrated result lost provenance identities");

        auto budget_hydrator = make_hydrator(context, fixture, 1);
        const auto budget_result = budget_hydrator->hydrate(request);
        expect(budget_result.status ==
                   AuthoritativeArticleHydrationStatus::InvalidArgument &&
                   budget_result.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::VerificationBudgetExceeded &&
                   budget_result.bytes.empty(),
               "full CAS verification budget was not enforced before reading");

        Fixture unsupported_profile;
        prepare(unsupported_profile, "image-original", std::string{"image/png"});
        auto unsupported_context = MdbxStorageContext::open(
            unsupported_profile.path.string());
        auto unsupported_hydrator = make_hydrator(
            unsupported_context, unsupported_profile);
        const auto unsupported = unsupported_hydrator->hydrate(
            request_for(unsupported_profile));
        expect(unsupported.status == AuthoritativeArticleHydrationStatus::Unsupported &&
                   unsupported.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::ArtifactProfileUnsupported &&
                   unsupported.bytes.empty(),
               "unsupported artifact profile was admitted");
        unsupported_hydrator.reset();
        unsupported_context.reset();
        std::error_code unsupported_error;
        std::filesystem::remove(unsupported_profile.path, unsupported_error);
        std::filesystem::remove_all(unsupported_profile.cas_root, unsupported_error);
        budget_hydrator.reset();

        Fixture corrupt_fixture;
        prepare(corrupt_fixture);
        auto corrupt_context = MdbxStorageContext::open(
            corrupt_fixture.path.string());
        mdbxc::KeyValueTable<std::string, std::string> bindings(
            corrupt_context->connection(),
            "authoritative_article_resource_artifact_bindings");
        {
            auto transaction = corrupt_context->connection()->transaction(
                mdbxc::TransactionMode::WRITABLE);
            bindings.insert_or_assign(
                binding_key(corrupt_fixture.revision), "malformed", transaction);
            transaction.commit();
        }
        auto corrupt_hydrator = make_hydrator(corrupt_context, corrupt_fixture);
        const auto corrupt_metadata = corrupt_hydrator->hydrate(
            request_for(corrupt_fixture));
        expect(corrupt_metadata.status == AuthoritativeArticleHydrationStatus::Corrupt &&
                   corrupt_metadata.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::PersistedMetadataCorrupt &&
                   corrupt_metadata.bytes.empty(),
               "malformed persisted binding was not classified as corruption");
        corrupt_hydrator.reset();
        corrupt_context.reset();
        std::error_code corrupt_error;
        std::filesystem::remove(corrupt_fixture.path, corrupt_error);
        std::filesystem::remove_all(corrupt_fixture.cas_root, corrupt_error);

        Fixture citation_fixture;
        prepare(citation_fixture);
        auto citation_context = MdbxStorageContext::open(
            citation_fixture.path.string());
        MdbxKnowledgeUnitStore citation_units(MdbxKnowledgeUnitStoreOptions{
            {}, citation_fixture.table_prefix, false, kDefaultMdbxMaxDbs,
            citation_context, {}
        });
        const auto base_envelope = citation_units.find(citation_fixture.unit_id);
        expect(base_envelope.has_value(), "citation fixture unit disappeared");
        auto forged_source = base_envelope->sources.front();
        forged_source.preview = "forged preview";
        forged_source.quote_hash = compute_quote_hash(forged_source.preview);
        --forged_source.excerpt.length;
        const auto forged_length = static_cast<std::size_t>(
            forged_source.excerpt.length);
        const auto forged_envelope = citation_units.create_or_get_unit(
            KnowledgeUnitCreateRequest{
                KnowledgeUnitKind::Chunk,
                citation_fixture.scope,
                citation_fixture.body.substr(0, forged_length),
                citation_fixture.body.substr(0, forged_length),
                std::nullopt,
                2,
                {forged_source},
                0,
                0,
                0.0
            });
        MdbxProjectionStore citation_projections(MdbxProjectionStoreOptions{
            {}, citation_fixture.table_prefix, false, kDefaultMdbxMaxDbs,
            citation_context
        });
        citation_projections.put(make_original_projection(
            forged_envelope, forged_envelope.revision));
        MdbxResourceManifestStorage citation_manifests(
            MdbxResourceManifestStorageOptions{
                {}, citation_fixture.table_prefix, false, kDefaultMdbxMaxDbs,
                citation_context
            });
        const auto expected_citation_manifest = citation_manifests.find_manifest(
            citation_fixture.resource);
        expect(expected_citation_manifest.has_value(),
               "citation fixture manifest disappeared");
        auto citation_manifest = *expected_citation_manifest;
        auto citation_ordinal = static_cast<std::uint32_t>(
            citation_manifest.records.size());
        citation_manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom, ChunkId{},
            std::string{"knowledge_unit:"} +
                std::to_string(forged_envelope.id.value()), citation_ordinal
        });
        citation_manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom, ChunkId{},
            std::string{"original_projection:"} +
                std::to_string(forged_envelope.id.value()), citation_ordinal
        });
        citation_manifests.upsert_manifest_if_unchanged(
            std::move(citation_manifest), expected_citation_manifest);
        auto citation_hydrator = make_hydrator(citation_context, citation_fixture);
        auto citation_request = request_for(citation_fixture);
        citation_request.candidate.knowledge_unit_id = forged_envelope.id;
        citation_request.candidate.unit_revision = forged_envelope.revision;
        citation_request.source_range = forged_source.excerpt;
        const auto citation_result = citation_hydrator->hydrate(citation_request);
        expect(citation_result.status == AuthoritativeArticleHydrationStatus::Corrupt &&
                   citation_result.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::SourceQuoteMismatch &&
                   citation_result.bytes.empty(),
               "forged citation preview was not checked against CAS bytes");
        citation_hydrator.reset();
        citation_context.reset();
        std::error_code citation_error;
        std::filesystem::remove(citation_fixture.path, citation_error);
        std::filesystem::remove_all(citation_fixture.cas_root, citation_error);

        auto wrong_scope = request;
        wrong_scope.scope_id = ScopeId{"other-scope"};
        auto wrong_scope_result = hydrator->hydrate(wrong_scope);
        expect(wrong_scope_result.status == AuthoritativeArticleHydrationStatus::Stale &&
                   wrong_scope_result.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::ScopeMismatch &&
                   wrong_scope_result.bytes.empty(),
               "wrong scope was not rejected without bytes");

        auto forged = request;
        forged.candidate.unit_revision++;
        auto forged_result = hydrator->hydrate(forged);
        expect(forged_result.status == AuthoritativeArticleHydrationStatus::Stale &&
                   forged_result.bytes.empty(),
               "forged candidate revision was admitted");

        auto bad_range = request;
        ++bad_range.source_range.offset;
        auto bad_range_result = hydrator->hydrate(bad_range);
        expect(bad_range_result.status ==
                   AuthoritativeArticleHydrationStatus::InvalidArgument &&
                   bad_range_result.bytes.empty(),
               "range not bound to source evidence was admitted");

        auto bad_claim = request;
        bad_claim.claimed_artifact_digest = BlobDigest{};
        auto bad_claim_result = hydrator->hydrate(bad_claim);
        expect(bad_claim_result.status ==
                   AuthoritativeArticleHydrationStatus::InvalidArgument &&
                   bad_claim_result.bytes.empty(),
               "forged artifact claim was admitted");

        std::filesystem::path raw_blob;
        for(const auto& entry : std::filesystem::recursive_directory_iterator(
                fixture.cas_root)) {
            if(entry.is_regular_file() && entry.path().extension() == ".raw") {
                raw_blob = entry.path();
                break;
            }
        }
        expect(!raw_blob.empty(), "catalog did not create a Raw CAS file");
        {
            std::ofstream output(raw_blob, std::ios::binary | std::ios::app);
            output << 'x';
        }
        const auto corrupt_cas = hydrator->hydrate(request);
        expect(corrupt_cas.status == AuthoritativeArticleHydrationStatus::Corrupt &&
                   corrupt_cas.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::CasCorrupt &&
                   corrupt_cas.bytes.empty(),
               "corrupted CAS body was not fail-closed");

        std::error_code error;
        std::filesystem::remove_all(fixture.cas_root, error);
        auto missing_cas = hydrator->hydrate(request);
        expect(missing_cas.status == AuthoritativeArticleHydrationStatus::Missing &&
                   missing_cas.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::CasMissing &&
                   missing_cas.bytes.empty(),
               "missing CAS body was not fail-closed");
        hydrator.reset();
        context.reset();

        prepare(fixture);
        context = MdbxStorageContext::open(fixture.path.string());
        auto hydrator_second = make_hydrator(context, fixture);
        hydrator_second->set_before_final_revalidation_hook_for_testing([context, &fixture] {
            MdbxKnowledgeUnitStore units(MdbxKnowledgeUnitStoreOptions{
                {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context, {}
            });
            KnowledgeUnitMutablePatch patch;
            patch.observed_at_ms = 2;
            (void)units.update_mutable_fields(fixture.unit_id, std::move(patch));
        });
        const auto changed = hydrator_second->hydrate(request);
        expect(changed.status == AuthoritativeArticleHydrationStatus::Stale &&
                   (changed.diagnostic_code ==
                        AuthoritativeArticleHydrationDiagnostic::UnitRevisionMismatch ||
                    changed.diagnostic_code ==
                        AuthoritativeArticleHydrationDiagnostic::MetadataChanged) &&
                   changed.bytes.empty(),
               "generation-only metadata mutation before linearization was not rejected");
        hydrator_second->set_before_final_revalidation_hook_for_testing({});
        hydrator_second.reset();
        context.reset();

        prepare(fixture);
        context = MdbxStorageContext::open(fixture.path.string());
        auto hydrator_third = make_hydrator(context, fixture);
        hydrator_third->set_after_final_revalidation_hook_for_testing(
            [context, &fixture] {
                MdbxKnowledgeUnitStore units(MdbxKnowledgeUnitStoreOptions{
                    {}, fixture.table_prefix, false, kDefaultMdbxMaxDbs, context, {}
                });
                expect(units.erase(fixture.unit_id),
                       "test could not erase unit after final revalidation");
            });
        const auto completed = hydrator_third->hydrate(request);
        expect(completed.status == AuthoritativeArticleHydrationStatus::Hydrated &&
                   !completed.bytes.empty(),
               "mutation after final snapshot invalidated a completed hydration");
        hydrator_third->set_after_final_revalidation_hook_for_testing({});
        const auto after_completion = hydrator_third->hydrate(request);
        expect(after_completion.status == AuthoritativeArticleHydrationStatus::Stale &&
                   after_completion.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::UnitMissing &&
                   after_completion.bytes.empty(),
               "next operation did not observe the post-completion mutation");

        hydrator_third.reset();
        context.reset();
        std::filesystem::remove(fixture.path, error);
        std::filesystem::remove_all(fixture.cas_root, error);
        std::cout << "authoritative article hydration: PASS\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        std::error_code ignored;
        std::filesystem::remove(fixture.path, ignored);
        std::filesystem::remove_all(fixture.cas_root, ignored);
        return 1;
    }
}

#else

int main() { return 0; }

#endif
