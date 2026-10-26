#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativePublicationParticipants.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPublicationCoordinator.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPublicationReadScope.hpp>

#if AGENT_MEMORY_HAS_MDBX

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
    const PublicationFencingIdentity& fencing
) {
    PublicationEvidence target;
    target.resource_id = ResourceId{"resource-read"};
    target.resource_generation = 1;
    target.canonical_revision = 1;
    target.unit_revision = 1;
    target.projection_version = ProjectionVersionRef{1, 1, 1, "projection-read"};
    target.manifest_schema = ResourceManifestSchema{"manifest-read", 1};
    target.manifest_revision = 1;
    target.manifest_payload_version = 1;
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
    if(observation.completion != MdbxPublicationReadCompletion::Exact
        || observation.frontier.sequence.value() != 1
        || !observation.canonical
        || !observation.unit
        || observation.projections.size() != 1
        || !observation.lexical
        || observation.lexical->index.size() != 1
        || !observation.manifest) {
        return fail("pinned read scope must return complete authoritative evidence");
    }

    std::filesystem::remove(database, cleanup);
    return 0;
}

#else
int main() { return 0; }
#endif
