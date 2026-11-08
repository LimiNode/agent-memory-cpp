#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceArtifactBindingStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxVerifiedArtifactHydration.hpp>
#include <agent_memory/ingestion/StructuralSegmentSet.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
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

std::filesystem::path test_path() {
    const auto stamp = std::chrono::high_resolution_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        (std::string{"agent_memory_article_vertical_"} + std::to_string(stamp) + ".mdbx");
}

ObservedResource observed(
    const ResourceId& resource_id,
    std::string body
) {
    return ObservedResource{
        ScopeId{"article-scope"},
        resource_id,
        "https://example.test/article.md",
        std::move(body),
        SourceTextOrigin::OriginalText,
        std::nullopt,
        1
    };
}

ResourceBodyDigest resource_digest(const ResourceImportResult& imported) {
    expect(imported.revision.body_digest.has_value(), "import did not publish body digest");
    return *imported.revision.body_digest;
}

ResourceRevisionRef revision_ref(
    const ResourceImportResult& imported
) {
    return ResourceRevisionRef{
        imported.resource_id,
        imported.revision.generation,
        resource_digest(imported)
    };
}

SourceRefSummary source_summary(
    const ResourceImportResult& imported,
    const StructuralSegment& segment
) {
    SourceRefSummary source;
    source.resource_id = imported.resource_id;
    source.reference_mode = SourceReferenceMode::RevisionBoundQuote;
    source.resource_revision = revision_ref(imported);
    source.uri = "https://example.test/article.md";
    source.excerpt = TextRange{
        static_cast<std::uint64_t>(segment.source.offset),
        static_cast<std::uint64_t>(segment.source.length)
    };
    source.preview = segment.source_text.substr(0, 256);
    source.quote_hash = compute_quote_hash(source.preview);
    source.confidence = 1.0;
    source.text_origin = SourceTextOrigin::OriginalText;
    return source;
}

std::vector<KnowledgeUnitId> publish_structural_units(
    const std::shared_ptr<MdbxStorageContext>& context,
    const ResourceImportResult& imported,
    const StructuralSegmentSet& segment_set,
    const std::string& table_prefix
) {
    MdbxKnowledgeUnitStore units(MdbxKnowledgeUnitStoreOptions{
        {}, table_prefix, false, kDefaultMdbxMaxDbs, context, {}
    });
    std::vector<KnowledgeUnitCreateRequest> requests;
    for(const auto& segment : segment_set.segments) {
        if(segment.text.empty() || segment.source.length == 0) continue;
        KnowledgeUnitCreateRequest request;
        request.kind = KnowledgeUnitKind::Chunk;
        request.scope_id = ScopeId{"article-scope"};
        request.primary_text = segment.text;
        request.display_text = segment.text;
        request.sources = {source_summary(imported, segment)};
        requests.push_back(std::move(request));
    }
    const auto envelopes = units.create_or_get_units(std::move(requests));
    expect(!envelopes.empty(), "structural segment set did not produce units");
    MdbxProjectionStore projections(MdbxProjectionStoreOptions{
        {}, table_prefix, false, kDefaultMdbxMaxDbs, context
    });
    for(const auto& envelope : envelopes) {
        projections.put(make_original_projection(envelope, envelope.revision));
    }
    return [&] {
        std::vector<KnowledgeUnitId> ids;
        ids.reserve(envelopes.size());
        for(const auto& envelope : envelopes) ids.push_back(envelope.id);
        return ids;
    }();
}

void publish_manifest_bindings(
    const std::shared_ptr<MdbxStorageContext>& context,
    const ResourceImportResult& imported,
    const std::vector<KnowledgeUnitId>& ids,
    const std::string& table_prefix
) {
    MdbxResourceManifestStorage storage(MdbxResourceManifestStorageOptions{
        {}, table_prefix, false, kDefaultMdbxMaxDbs, context
    });
    const auto expected = storage.find_manifest(imported.resource_id);
    expect(expected.has_value(), "resource manifest missing before segment publication");
    auto manifest = *expected;
    manifest.schema = ResourceManifestSchema{
        "agent_memory.m0_resource_importer.raw_chunks_original@article_vertical", 1
    };
    auto ordinal = static_cast<std::uint32_t>(manifest.records.size());
    for(const auto id : ids) {
        manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom, ChunkId{},
            std::string{"knowledge_unit:"} + std::to_string(id.value()), ordinal
        });
        manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom, ChunkId{},
            std::string{"original_projection:"} + std::to_string(id.value()), ordinal
        });
        ++ordinal;
    }
    storage.upsert_manifest_if_unchanged(std::move(manifest), expected);
}

ArtifactCatalogRecord retain_artifact(
    MdbxArtifactCatalog& catalog,
    const SourceRevisionId& source_revision_id,
    const ArtifactBindingId& binding_id,
    const std::string& body,
    std::uint64_t bound_at_ms
) {
    const auto result = catalog.ingest(ArtifactCatalogIngestRequest{
        source_revision_id,
        binding_id,
        "source-original",
        ArtifactRetentionClass::SourceOriginal,
        std::string{"text/markdown"},
        std::string{"article.md"},
        bound_at_ms,
        body
    });
    expect(result.status == ArtifactCatalogStatus::Ok && result.record.has_value(),
           "artifact catalog did not retain source bytes");
    return *result.record;
}

void verify_materialized_citation(
    const MdbxResourceArtifactBindingStore& joins,
    const MdbxArtifactMaterializer& materializer,
    const RevisionBoundRetrievalResult& hit,
    const ArtifactCatalogRecord& artifact,
    const std::string& expected_bytes
) {
    expect(hit.freshness == RevisionBoundFreshness::Current,
           "lexical hit was not admitted as current");
    expect(hit.materialization == RevisionBoundMaterialization::SummaryOnly,
           "snapshot retriever claimed byte hydration");
    expect(hit.source && hit.source->resource_revision,
           "current hit lost revision-bound source summary");
    const auto join = joins.find(*hit.source->resource_revision);
    expect(join.has_value(), "current hit has no persisted artifact join");
    const auto hydrated = materializer.materialize(VerifiedArtifactMaterializationRequest{
        join->source_revision_id,
        join->artifact_binding_id,
        artifact.artifact.id,
        artifact.artifact.digest,
        hit.source->excerpt.offset,
        hit.source->excerpt.length
    });
    expect(hydrated.status == VerifiedArtifactMaterializationStatus::Materialized,
           "citation materialization was not verified");
    expect(hydrated.bytes == expected_bytes,
           "materialized citation bytes differ from the structural source span");
}

} // namespace

int main() {
    const auto path = test_path();
    const auto cas_root = path.parent_path() / (path.stem().string() + "_cas");
    std::error_code error;
    std::filesystem::remove(path, error);
    std::filesystem::remove_all(cas_root, error);

    const auto body_v1 = std::string{
        "# Durable article\n\n"
        "The retained citation path verifies exact source bytes.\n\n"
        "## Scope\n\n"
        "Revision updates never substitute historical material.\n"
    };
    const auto body_v2 = std::string{
        "# Durable article\n\n"
        "The retained citation path now contains the revised evidence.\n\n"
        "## Scope\n\n"
        "Revision updates still reject historical material.\n"
    };
    const ResourceId resource_id{"resource:article-vertical"};
    const std::string table_prefix = "article_vertical";
    ArtifactCatalogRecord artifact_v2_saved;

    {
    auto context = MdbxStorageContext::open(path.string());
    MdbxArtifactCatalog catalog(MdbxArtifactCatalogOptions{
        {}, table_prefix, false, kDefaultMdbxMaxDbs, context, cas_root, {},
        64U * 1024U * 1024U, {}
    });
    MdbxResourceArtifactBindingStore joins(MdbxResourceArtifactBindingStoreOptions{
        {}, table_prefix, false, kDefaultMdbxMaxDbs, context
    });
    MdbxResourceImporterOptions importer_options;
    importer_options.context = context;
    importer_options.table_prefix = table_prefix;
    importer_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    MdbxResourceImporter importer(std::move(importer_options));

    const auto artifact_v1 = retain_artifact(
        catalog, SourceRevisionId{"article-source@1"},
        ArtifactBindingId{"article-binding@1"}, body_v1, 1
    );
    const auto imported_v1 = importer.import(observed(resource_id, body_v1));
    const auto revision_v1 = revision_ref(imported_v1);
    expect(artifact_v1.artifact.digest.value == revision_v1.body_digest.bytes,
           "resource and artifact authorities disagree on the retained digest");
    joins.put(ResourceArtifactBinding{
        revision_v1, SourceRevisionId{"article-source@1"},
        ArtifactBindingId{"article-binding@1"}
    });
    joins.put(ResourceArtifactBinding{
        revision_v1, SourceRevisionId{"article-source@1"},
        ArtifactBindingId{"article-binding@1"}
    });
    auto forged_revision = revision_v1;
    forged_revision.body_digest.bytes[0] ^= 0x01U;
    expect(!joins.find(forged_revision).has_value(),
           "artifact join accepted a forged resource digest");
    bool collision_rejected = false;
    try {
        joins.put(ResourceArtifactBinding{
            revision_v1, SourceRevisionId{"article-source@1"},
            ArtifactBindingId{"article-binding-forged"}
        });
    } catch(const std::runtime_error&) {
        collision_rejected = true;
    }
    expect(collision_rejected,
           "artifact join allowed a second binding for one exact revision");
    const auto segments_v1 = build_structural_segment_set(
        body_v1, "article-source@1"
    );
    const auto ids_v1 = publish_structural_units(
        context, imported_v1, segments_v1, table_prefix
    );
    publish_manifest_bindings(context, imported_v1, ids_v1, table_prefix);

    StandardTokenizer tokenizer;
    MdbxSnapshotRevisionBoundRetriever retriever(
        context, ScopeId{"article-scope"}, tokenizer,
        ProjectionAwareLexicalRetrieverOptions{ProjectionKind::Original, 64, 4096,
                                               8U * 1024U * 1024U,
                                               "agent_memory.m0_resource_importer.raw_chunks_original@article_vertical", 1},
        table_prefix
    );
    const auto first = retriever.retrieve(
        RetrievalQuery{"exact source bytes", std::nullopt, 16, {}}
    );
    expect(!first.empty(), "article lexical query returned no candidates");
    const auto first_it = std::find_if(
        first.begin(), first.end(), [](const auto& hit) {
            return hit.freshness == RevisionBoundFreshness::Current && hit.source &&
                hit.source->resource_revision && hit.source->excerpt.length != 0;
        }
    );
    const auto structural_first_it = std::find_if(
        first.begin(), first.end(), [&](const auto& hit) {
            return std::find(ids_v1.begin(), ids_v1.end(), hit.knowledge_unit_id) !=
                    ids_v1.end() &&
                hit.freshness == RevisionBoundFreshness::Current && hit.source &&
                hit.source->resource_revision && hit.source->excerpt.length != 0;
        }
    );
    expect(first_it != first.end() && structural_first_it != first.end(),
           "article lexical query had no current structural citation candidate");
    const auto& selected_first = *structural_first_it;
    const auto first_join = joins.find(*selected_first.source->resource_revision);
    expect(first_join && first_join->source_revision_id == SourceRevisionId{"article-source@1"},
           "citation candidate resolved through the wrong source revision");
    const auto first_span = body_v1.substr(
        static_cast<std::size_t>(selected_first.source->excerpt.offset),
        static_cast<std::size_t>(selected_first.source->excerpt.length)
    );
    const ProjectionAwareLexicalCandidate old_candidate{
        selected_first.knowledge_unit_id,
        selected_first.unit_revision,
        selected_first.projection_kind,
        selected_first.score
    };
    verify_materialized_citation(
        joins,
        MdbxArtifactMaterializer(catalog),
        selected_first,
        artifact_v1,
        first_span
    );

    const auto artifact_v2 = retain_artifact(
        catalog, SourceRevisionId{"article-source@2"},
        ArtifactBindingId{"article-binding@2"}, body_v2, 2
    );
    artifact_v2_saved = artifact_v2;
    const auto imported_v2 = importer.import(observed(resource_id, body_v2));
    expect(imported_v2.revision.generation == 2,
           "article update did not advance the resource generation");
    const auto revision_v2 = revision_ref(imported_v2);
    joins.put(ResourceArtifactBinding{
        revision_v2, SourceRevisionId{"article-source@2"},
        ArtifactBindingId{"article-binding@2"}
    });
    const auto segments_v2 = build_structural_segment_set(
        body_v2, "article-source@2"
    );
    const auto ids_v2 = publish_structural_units(
        context, imported_v2, segments_v2, table_prefix
    );
    publish_manifest_bindings(context, imported_v2, ids_v2, table_prefix);

    const auto stale = retriever.admit_candidate(old_candidate);
    expect(stale.freshness == RevisionBoundFreshness::Stale,
           "a saved generation-one candidate was not rejected after source update");
    expect(stale.text.empty(), "stale result exposed canonical text");
    const auto second = retriever.retrieve(
        RetrievalQuery{"revised evidence", std::nullopt, 16, {}}
    );
    const auto second_it = std::find_if(
        second.begin(), second.end(), [](const auto& hit) {
            return hit.freshness == RevisionBoundFreshness::Current && hit.source &&
                hit.source->resource_revision &&
                hit.source->resource_revision->generation == 2;
        }
    );
    const auto structural_second_it = std::find_if(
        second.begin(), second.end(), [&](const auto& hit) {
            return std::find(ids_v2.begin(), ids_v2.end(), hit.knowledge_unit_id) !=
                    ids_v2.end() &&
                hit.freshness == RevisionBoundFreshness::Current && hit.source &&
                hit.source->resource_revision &&
                hit.source->resource_revision->generation == 2;
        }
    );
    expect(second_it != second.end() && structural_second_it != second.end(),
           "updated article was not lexically retrievable as a structural segment");
    const auto& selected_second = *structural_second_it;
    const auto second_join = joins.find(*selected_second.source->resource_revision);
    expect(second_join && second_join->source_revision_id == SourceRevisionId{"article-source@2"},
           "updated citation resolved through the historical source revision");
    const auto second_span = body_v2.substr(
        static_cast<std::size_t>(selected_second.source->excerpt.offset),
        static_cast<std::size_t>(selected_second.source->excerpt.length)
    );
    verify_materialized_citation(
        joins,
        MdbxArtifactMaterializer(catalog),
        selected_second,
        artifact_v2,
        second_span
    );

    }

    {
        auto reopened = MdbxStorageContext::open(path.string());
        MdbxResourceArtifactBindingStore reopened_joins(
            MdbxResourceArtifactBindingStoreOptions{
                {}, table_prefix, false, kDefaultMdbxMaxDbs, reopened
            }
        );
        MdbxResourceImporterOptions reopened_options;
        reopened_options.context = reopened;
        reopened_options.table_prefix = table_prefix;
        reopened_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        MdbxResourceImporter reopened_importer(std::move(reopened_options));
        StandardTokenizer reopened_tokenizer;
        MdbxSnapshotRevisionBoundRetriever reopened_retriever(
            reopened, ScopeId{"article-scope"}, reopened_tokenizer,
            ProjectionAwareLexicalRetrieverOptions{
                ProjectionKind::Original, 256, 4096, 8U * 1024U * 1024U,
                "agent_memory.m0_resource_importer.raw_chunks_original@article_vertical", 1
            },
            table_prefix
        );
        const auto reopened_hits = reopened_retriever.retrieve(
            RetrievalQuery{"revised evidence", std::nullopt, 16, {}}
        );
        const auto reopened_hit = std::find_if(
            reopened_hits.begin(), reopened_hits.end(), [](const auto& hit) {
                return hit.freshness == RevisionBoundFreshness::Current && hit.source;
            }
        );
        expect(reopened_hit != reopened_hits.end(),
               "reopen lost the current article citation");
        expect(reopened_joins.find(*reopened_hit->source->resource_revision).has_value(),
               "reopen lost the typed resource/artifact join");
        expect(reopened_importer.find_manifest(resource_id).has_value(),
               "reopen lost the active resource manifest");
    }

    std::filesystem::remove_all(cas_root, error);
    auto final_context = MdbxStorageContext::open(path.string());
    MdbxArtifactCatalog final_catalog(MdbxArtifactCatalogOptions{
        {}, table_prefix, false, kDefaultMdbxMaxDbs, final_context, cas_root, {},
        64U * 1024U * 1024U, {}
    });
    const auto missing = MdbxArtifactMaterializer(final_catalog).materialize(
        VerifiedArtifactMaterializationRequest{
            SourceRevisionId{"article-source@2"},
            ArtifactBindingId{"article-binding@2"},
            artifact_v2_saved.artifact.id,
            artifact_v2_saved.artifact.digest,
            0,
            4
        }
    );
    expect(missing.status == VerifiedArtifactMaterializationStatus::Missing,
           "missing CAS body was not rejected fail-closed");

    final_context.reset();
    std::filesystem::remove(path, error);
    std::filesystem::remove_all(cas_root, error);
    std::cout << "article-to-citation vertical: PASS\n";
    return 0;
}

#else

int main() { return 0; }

#endif
