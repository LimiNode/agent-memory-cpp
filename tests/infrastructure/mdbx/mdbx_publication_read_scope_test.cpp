#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativePublicationParticipants.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPublicationCoordinator.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativePublicationParticipants.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionLexicalPublicationParticipants.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPublicationReadScope.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>
#include <string_view>

namespace {
using namespace agent_memory;

int fail(std::string_view message) { std::cerr << message << '\n'; return 1; }

std::filesystem::path path() {
    return std::filesystem::temp_directory_path() /
        ("agent_memory_publication_read_" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()) + ".mdbx");
}

KnowledgeUnitCreateRequest unit_request(const ScopeId& scope) {
    KnowledgeUnitCreateRequest request;
    request.kind = KnowledgeUnitKind::Chunk;
    request.scope_id = scope;
    request.primary_text = "pinned unit";
    request.display_text = "pinned unit";
    request.content_hash_recipe_version = 2;
    SourceRefSummary source;
    source.resource_id = ResourceId{"resource-read"};
    source.resource_revision = ResourceRevisionRef{source.resource_id, 1, ResourceBodyDigest{}};
    source.uri = "memory://read";
    source.excerpt = TextRange{0, 4};
    source.confidence = 1.0;
    source.preview = "pinned";
    source.quote_hash = compute_quote_hash(source.preview);
    request.sources.push_back(std::move(source));
    request.created_at_ms = 1;
    request.observed_at_ms = 1;
    request.priority_weight = 1.0;
    request.content_hash = compute_chunk_content_hash(
        request.scope_id, request.primary_text, request.sources,
        request.content_hash_recipe_version);
    return request;
}

PublicationPlan metadata_plan(
    const ScopeId& scope,
    const PublicationProfileIdentity& profile,
    const PublicationFencingIdentity& fencing,
    const std::uint64_t canonical_revision = 1,
    const std::uint64_t manifest_payload_version = 1
) {
    PublicationEvidence target;
    target.resource_id = ResourceId{"resource-read"};
    target.resource_generation = 1;
    target.canonical_revision = canonical_revision;
    target.unit_revision = 1;
    target.projection_version = ProjectionVersionRef{1, 1, 1, "projection-read"};
    target.manifest_schema = ResourceManifestSchema{"manifest-read", 1};
    target.manifest_revision = 1;
    target.manifest_payload_version = manifest_payload_version;
    target.manifest_binding_digest = "binding-read";
    return PublicationPlan{
        PublicationIdentity{scope, "read-publication"},
        PublicationScopeFrontier{scope, {}},
        profile,
        fencing,
        PublicationEvidence{},
        target,
        PublicationPayloadIdentity{"payload-read", "digest-read", "resource-read:full"}
    };
}

} // namespace

int main() {
    const auto database = path();
    std::error_code cleanup;
    const ScopeId scope{"scope-read"};
    const PublicationProfileIdentity profile{scope, "coordinated-v1", 1};
    const PublicationFencingIdentity fencing{"reader-writer", 1, "token-read"};
    auto context = MdbxStorageContext::open(database.string(), false, 48);

    MdbxPublicationCoordinatorOptions coordinator_options;
    coordinator_options.context = context;
    coordinator_options.scope_id = scope;
    MdbxPublicationCoordinator coordinator{std::move(coordinator_options)};
    if(coordinator.admit_profile(profile, fencing).code != PublicationAdmissionCode::Admitted
        || coordinator.publish(metadata_plan(scope, profile, fencing)).code
            != PublicationOutcomeCode::Committed) {
        return fail("read-scope fixture metadata publication failed");
    }

    MdbxCanonicalContentStoreOptions canonical_options;
    canonical_options.context = context;
    MdbxCanonicalContentStore canonical{std::move(canonical_options)};
    if(!canonical.create_document(CanonicalDocumentRevision{
            DocumentId{"read-document"}, 0, {},
            {ContentBlock{ContentBlockId{"read-block"}, 1,
                          ContentBlockKind::Paragraph, std::nullopt, "pinned body"}}
        })) {
        return fail("read-scope canonical fixture failed");
    }

    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = context;
    MdbxKnowledgeUnitStore units{std::move(unit_options)};
    if(units.create_or_get_unit(unit_request(scope)).id.empty()) {
        return fail("read-scope unit fixture failed");
    }

    MdbxResourceManifestStorageOptions manifest_options;
    manifest_options.context = context;
    MdbxResourceManifestStorage manifests{std::move(manifest_options)};
    manifests.upsert_manifest(ResourceManifest{
        ResourceRevision{ResourceId{"resource-read"}, 1, 1, 1}, {}
    });

    const ProjectionVersionRef projection_version{1, 1, 1, "projection-read"};
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = context;
    MdbxProjectionStore projections{std::move(projection_options)};
    projections.put(SearchProjection{
        scope, KnowledgeUnitId{1}, ProjectionKind::Original, projection_version,
        "title", "heading", "pinned body", "", {}, {}
    });

    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 3;
    MdbxPersistentLexicalIndexOptions lexical_options;
    lexical_options.context = context;
    lexical_options.descriptor = LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{scope, ProjectionKind::Original,
                                 LexicalStatisticsEpoch{epoch}},
        "original", 1, "tokenizer-read", "normalization-read", {1}
    };
    MdbxPersistentLexicalIndex lexical{std::move(lexical_options)};
    lexical.upsert(FlatLexicalUnitInput{
        scope, KnowledgeUnitId{1}, ProjectionKind::Original,
        ResourceId{"resource-read"}, 1, 1, projection_version,
        {"pinned", "body"}
    });

    auto read_scope = MdbxPublicationReadScope::begin(context);
    MdbxFrontierReadParticipant frontier{context, coordinator};
    MdbxCanonicalReadParticipant canonical_read{context, canonical, DocumentId{"read-document"}};
    MdbxKnowledgeUnitReadParticipant unit_read{context, units, KnowledgeUnitId{1}};
    MdbxProjectionReadParticipant projection_read{
        context, projections, scope, KnowledgeUnitId{1}, ProjectionKind::Original
    };
    MdbxLexicalReadParticipant lexical_read{context, lexical};
    MdbxManifestReadParticipant manifest_read{context, manifests, ResourceId{"resource-read"}};
    read_scope.read(frontier);
    read_scope.read(canonical_read);
    read_scope.read(unit_read);
    read_scope.read(projection_read);
    read_scope.read(lexical_read);
    read_scope.read(manifest_read);
    read_scope.finish();
    const auto& observation = read_scope.observation();
    if(observation.completion == MdbxPublicationReadCompletion::Exact) {
        return fail("metadata-only and legacy writes must not produce Exact");
    }

    std::filesystem::remove(database, cleanup);

    const auto complete_database = path();
    auto complete_context = MdbxStorageContext::open(complete_database.string(), false, 48);
    MdbxPublicationCoordinatorOptions complete_coordinator_options;
    complete_coordinator_options.context = complete_context;
    complete_coordinator_options.scope_id = scope;
    MdbxPublicationCoordinator complete_coordinator{
        std::move(complete_coordinator_options)
    };
    if(complete_coordinator.admit_profile(profile, fencing).code
        != PublicationAdmissionCode::Admitted) {
        return fail("complete fixture profile admission failed");
    }

    MdbxCanonicalContentStoreOptions complete_canonical_options;
    complete_canonical_options.context = complete_context;
    MdbxCanonicalContentStore complete_canonical{
        std::move(complete_canonical_options)
    };
    MdbxKnowledgeUnitStoreOptions complete_unit_options;
    complete_unit_options.context = complete_context;
    MdbxKnowledgeUnitStore complete_units{std::move(complete_unit_options)};
    MdbxResourceManifestStorageOptions complete_manifest_options;
    complete_manifest_options.context = complete_context;
    MdbxResourceManifestStorage complete_manifests{
        std::move(complete_manifest_options)
    };
    MdbxProjectionStoreOptions complete_projection_options;
    complete_projection_options.context = complete_context;
    MdbxProjectionStore complete_projections{
        std::move(complete_projection_options)
    };
    std::array<std::uint8_t, 16> complete_epoch{};
    complete_epoch[0] = 4;
    MdbxPersistentLexicalIndexOptions complete_lexical_options;
    complete_lexical_options.context = complete_context;
    complete_lexical_options.descriptor = LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{
            scope, ProjectionKind::Original, LexicalStatisticsEpoch{complete_epoch}
        },
        "original", 1, "tokenizer-read", "normalization-read", {1}
    };
    MdbxPersistentLexicalIndex complete_lexical{
        std::move(complete_lexical_options)
    };
    const ProjectionVersionRef complete_projection_version{1, 1, 1, "projection-read"};
    if(!complete_canonical.create_document(CanonicalDocumentRevision{
            DocumentId{"complete-document"},
            0,
            {},
            {ContentBlock{
                ContentBlockId{"complete-block"},
                1,
                ContentBlockKind::Paragraph,
                std::nullopt,
                "baseline body"
            }}
        })) {
        return fail("complete canonical baseline failed");
    }
    MdbxCanonicalPublicationParticipant complete_canonical_participant{
        complete_context,
        complete_canonical,
        CanonicalEditRequest{
            DocumentId{"complete-document"},
            0,
            {ReplaceBlockText{ContentBlockId{"complete-block"}, "pinned body"}}
        }
    };
    MdbxKnowledgeUnitPublicationParticipant complete_unit_participant{
        complete_context,
        complete_units,
        {unit_request(scope)}
    };
    ResourceManifest complete_manifest{
        ResourceRevision{ResourceId{"resource-read"}, 1, 1, 1},
        {},
        ResourceManifestState::Active,
        {},
        ResourceManifestSchema{"manifest-read", 1},
        ResourceManifestPayloadVersion::V1
    };
    MdbxManifestPublicationParticipant complete_manifest_participant{
        complete_context,
        complete_manifests,
        complete_manifest,
        std::nullopt
    };
    MdbxProjectionPublicationParticipant complete_projection_participant{
        complete_context,
        complete_projections,
        SearchProjection{
            scope,
            KnowledgeUnitId{1},
            ProjectionKind::Original,
            complete_projection_version,
            "title",
            "heading",
            "pinned body",
            "",
            {},
            {}
        }
    };
    MdbxLexicalPublicationParticipant complete_lexical_participant{
        complete_context,
        complete_lexical,
        FlatLexicalUnitInput{
            scope,
            KnowledgeUnitId{1},
            ProjectionKind::Original,
            ResourceId{"resource-read"},
            1,
            1,
            complete_projection_version,
            {"pinned", "body"}
        }
    };
    const auto complete_outcome = complete_coordinator.publish(
            metadata_plan(scope, profile, fencing, 1, 6),
            {
                &complete_canonical_participant,
                &complete_unit_participant,
                &complete_projection_participant,
                &complete_lexical_participant,
                &complete_manifest_participant
            }
        );
    if(complete_outcome.code != PublicationOutcomeCode::Committed) {
        return fail(
            "complete coordinated publication failed: "
            + std::to_string(static_cast<int>(complete_outcome.code))
        );
    }

    auto complete_read_scope = MdbxPublicationReadScope::begin(complete_context);
    MdbxFrontierReadParticipant complete_frontier{
        complete_context, complete_coordinator
    };
    MdbxCanonicalReadParticipant complete_canonical_read{
        complete_context, complete_canonical, DocumentId{"complete-document"}
    };
    MdbxKnowledgeUnitReadParticipant complete_unit_read{
        complete_context, complete_units, KnowledgeUnitId{1}
    };
    MdbxProjectionReadParticipant complete_projection_read{
        complete_context,
        complete_projections,
        scope,
        KnowledgeUnitId{1},
        ProjectionKind::Original
    };
    MdbxLexicalReadParticipant complete_lexical_read{
        complete_context, complete_lexical
    };
    MdbxManifestReadParticipant complete_manifest_read{
        complete_context, complete_manifests, ResourceId{"resource-read"}
    };
    complete_read_scope.read(complete_frontier);
    complete_read_scope.read(complete_canonical_read);
    complete_read_scope.read(complete_unit_read);
    complete_read_scope.read(complete_projection_read);
    complete_read_scope.read(complete_lexical_read);
    complete_read_scope.read(complete_manifest_read);
    complete_read_scope.finish();
    const auto& complete_observation = complete_read_scope.observation();
    if(complete_observation.completion != MdbxPublicationReadCompletion::Exact) {
        return fail("one coordinated commit must produce Exact after reopen-equivalent read");
    }

    const auto independent_mutation = complete_canonical.commit(CanonicalEditRequest{
        DocumentId{"complete-document"},
        1,
        {ReplaceBlockText{ContentBlockId{"complete-block"}, "legacy mutation"}}
    });
    if(!independent_mutation.succeeded()) {
        return fail("independent post-commit mutation fixture failed");
    }
    auto mutated_read_scope = MdbxPublicationReadScope::begin(complete_context);
    MdbxFrontierReadParticipant mutated_frontier{
        complete_context, complete_coordinator
    };
    mutated_read_scope.read(mutated_frontier);
    mutated_read_scope.read(complete_canonical_read);
    mutated_read_scope.read(complete_unit_read);
    mutated_read_scope.read(complete_projection_read);
    mutated_read_scope.read(complete_lexical_read);
    mutated_read_scope.read(complete_manifest_read);
    mutated_read_scope.finish();
    if(mutated_read_scope.observation().completion == MdbxPublicationReadCompletion::Exact) {
        return fail("independent post-commit mutation must not remain Exact");
    }

    mdbxc::KeyValueTable<std::string, std::string> complete_metadata(
        complete_context->connection(), "agent_memory_publication_coordinator_v1"
    );
    auto remove_schema = complete_context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE
    );
    complete_metadata.erase(scope.value() + "|meta/schema", remove_schema);
    remove_schema.commit();
    auto missing_schema_scope = MdbxPublicationReadScope::begin(complete_context);
    MdbxFrontierReadParticipant missing_schema_frontier{
        complete_context, complete_coordinator
    };
    missing_schema_scope.read(missing_schema_frontier);
    missing_schema_scope.read(complete_canonical_read);
    missing_schema_scope.read(complete_unit_read);
    missing_schema_scope.read(complete_projection_read);
    missing_schema_scope.read(complete_lexical_read);
    missing_schema_scope.read(complete_manifest_read);
    missing_schema_scope.finish();
    if(missing_schema_scope.observation().completion == MdbxPublicationReadCompletion::Exact) {
        return fail("missing coordinator schema must fail closed");
    }

    std::filesystem::remove(complete_database, cleanup);
    return 0;
}

#else
int main() { return 0; }
#endif
