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
#include <stdexcept>
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

SearchProjection projection_value(
    const ScopeId& scope,
    std::string body = "pinned body",
    const std::uint64_t projection_revision = 1
) {
    return SearchProjection{
        scope,
        KnowledgeUnitId{1},
        ProjectionKind::Original,
        ProjectionVersionRef{1, projection_revision, 1, "projection-read"},
        "title",
        "heading",
        std::move(body),
        "",
        {},
        {}
    };
}

ResourceManifest manifest_value(
    const ResourceManifestPayloadVersion payload_version,
    const ResourceManifestState state = ResourceManifestState::Active
) {
    return ResourceManifest{
        ResourceRevision{ResourceId{"resource-read"}, 1, 1, 1},
        {DerivedRecordRef{
            DerivedRecordKind::Document,
            {},
            "complete-document",
            0
        }},
        state,
        {},
        ResourceManifestSchema{"manifest-read", 1},
        payload_version
    };
}

PublicationPlan metadata_plan(
    const ScopeId& scope,
    const PublicationProfileIdentity& profile,
    const PublicationFencingIdentity& fencing,
    const std::uint64_t canonical_revision,
    const std::uint64_t manifest_payload_version
);

bool prepare_reopen_fixture(
    const std::filesystem::path& database,
    const ScopeId& scope,
    const PublicationProfileIdentity& profile,
    const PublicationFencingIdentity& fencing
) {
    auto context = MdbxStorageContext::open(database.string(), false, 48);
    MdbxPublicationCoordinatorOptions coordinator_options;
    coordinator_options.context = context;
    coordinator_options.scope_id = scope;
    MdbxPublicationCoordinator coordinator{std::move(coordinator_options)};
    if(coordinator.admit_profile(profile, fencing).code != PublicationAdmissionCode::Admitted) {
        return false;
    }
    MdbxCanonicalContentStoreOptions canonical_options;
    canonical_options.context = context;
    MdbxCanonicalContentStore canonical{std::move(canonical_options)};
    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = context;
    MdbxKnowledgeUnitStore units{std::move(unit_options)};
    MdbxResourceManifestStorageOptions manifest_options;
    manifest_options.context = context;
    MdbxResourceManifestStorage manifests{std::move(manifest_options)};
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = context;
    MdbxProjectionStore projections{std::move(projection_options)};
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 9;
    MdbxPersistentLexicalIndexOptions lexical_options;
    lexical_options.context = context;
    lexical_options.descriptor = LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
        "original", 1, "tokenizer-read", "normalization-read", {1}
    };
    MdbxPersistentLexicalIndex lexical{std::move(lexical_options)};
    if(!canonical.create_document(CanonicalDocumentRevision{
            DocumentId{"complete-document"}, 0, {},
            {ContentBlock{ContentBlockId{"complete-block"}, 1,
                ContentBlockKind::Paragraph, std::nullopt, "baseline body"}}
        })) {
        return false;
    }
    MdbxCanonicalPublicationParticipant canonical_participant{
        context, canonical,
        CanonicalEditRequest{
            DocumentId{"complete-document"}, 0,
            {ReplaceBlockText{ContentBlockId{"complete-block"}, "pinned body"}}
        }
    };
    MdbxKnowledgeUnitPublicationParticipant unit_participant{
        context, units, {unit_request(scope)}
    };
    MdbxManifestPublicationParticipant manifest_participant{
        context, manifests, manifest_value(ResourceManifestPayloadVersion::V6), std::nullopt
    };
    MdbxProjectionPublicationParticipant projection_participant{
        context, projections, projection_value(scope)
    };
    MdbxLexicalPublicationParticipant lexical_participant{
        context, lexical,
        FlatLexicalUnitInput{
            scope, KnowledgeUnitId{1}, ProjectionKind::Original,
            ResourceId{"resource-read"}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "projection-read"}, {"pinned", "body"}
        }
    };
    return coordinator.publish(metadata_plan(scope, profile, fencing, 1, 6), {
        &canonical_participant, &unit_participant, &projection_participant,
        &lexical_participant, &manifest_participant
    }).code == PublicationOutcomeCode::Committed;
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
    target.canonical_document_id = DocumentId{"complete-document"};
    target.canonical_revision = canonical_revision;
    target.unit_id = KnowledgeUnitId{1};
    target.unit_revision = 1;
    target.projection_version = ProjectionVersionRef{1, 1, 1, "projection-read"};
    target.projection_payload_digest = compute_projection_payload_digest(
        projection_value(scope)
    );
    target.manifest_schema = ResourceManifestSchema{"manifest-read", 1};
    target.manifest_payload_version = manifest_payload_version;
    target.manifest_binding_digest = compute_manifest_binding_digest(
        manifest_value(static_cast<ResourceManifestPayloadVersion>(
            manifest_payload_version
        ))
    );
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

class ForgedCanonicalReadParticipant final : public MdbxPublicationReadParticipant {
public:
    ForgedCanonicalReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        CanonicalDocumentRevision canonical
    ) : m_context(std::move(context)), m_canonical(std::move(canonical)) {}

    const MdbxStorageContext& context() const noexcept override {
        m_context_called = true;
        return *m_context;
    }

    void read(
        const mdbxc::Transaction&,
        MdbxPublicationReadObservation& observation
    ) override {
        m_read_called = true;
        observation.canonical = m_canonical;
    }

    bool context_called() const noexcept { return m_context_called; }
    bool read_called() const noexcept { return m_read_called; }

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    CanonicalDocumentRevision m_canonical;
    mutable bool m_context_called = false;
    bool m_read_called = false;
};

bool replace_once(
    std::string& value,
    const std::string_view expected,
    const std::string_view replacement
) {
    if(expected.size() != replacement.size()) {
        return false;
    }
    const auto position = value.find(expected);
    if(position == std::string::npos) {
        return false;
    }
    value.replace(position, expected.size(), replacement);
    return true;
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
        || coordinator.publish_metadata_only(metadata_plan(scope, profile, fencing)).code
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
    manifests.upsert_manifest(manifest_value(ResourceManifestPayloadVersion::V1));

    const ProjectionVersionRef projection_version{1, 1, 1, "projection-read"};
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = context;
    MdbxProjectionStore projections{std::move(projection_options)};
    projections.put(projection_value(scope));

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
    if(observation.completion == MdbxPublicationReadCompletion::TargetExact) {
        return fail("metadata-only and legacy writes must not produce TargetExact");
    }

    std::filesystem::remove(database, cleanup);

    const auto reopen_database = path();
    if(!prepare_reopen_fixture(reopen_database, scope, profile, fencing)) {
        return fail("close/reopen fixture publication failed");
    }
    {
        auto reopened_context = MdbxStorageContext::open(reopen_database.string(), false, 48);
        MdbxPublicationCoordinatorOptions reopened_coordinator_options;
        reopened_coordinator_options.context = reopened_context;
        reopened_coordinator_options.scope_id = scope;
        MdbxPublicationCoordinator reopened_coordinator{
            std::move(reopened_coordinator_options)
        };
        MdbxCanonicalContentStoreOptions canonical_options;
        canonical_options.context = reopened_context;
        MdbxCanonicalContentStore canonical{std::move(canonical_options)};
        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = reopened_context;
        MdbxKnowledgeUnitStore units{std::move(unit_options)};
        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = reopened_context;
        MdbxResourceManifestStorage manifests{std::move(manifest_options)};
        MdbxProjectionStoreOptions projection_options;
        projection_options.context = reopened_context;
        MdbxProjectionStore projections{std::move(projection_options)};
        std::array<std::uint8_t, 16> epoch{};
        epoch[0] = 9;
        MdbxPersistentLexicalIndexOptions lexical_options;
        lexical_options.context = reopened_context;
        lexical_options.descriptor = LexicalIndexDescriptor{
            LexicalIndexDescriptorRef{scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
            "original", 1, "tokenizer-read", "normalization-read", {1}
        };
        MdbxPersistentLexicalIndex lexical{std::move(lexical_options)};
        auto read_scope = MdbxPublicationReadScope::begin(reopened_context);
        MdbxFrontierReadParticipant frontier{reopened_context, reopened_coordinator};
        MdbxCanonicalReadParticipant canonical_read{
            reopened_context, canonical, DocumentId{"complete-document"}
        };
        MdbxKnowledgeUnitReadParticipant unit_read{
            reopened_context, units, KnowledgeUnitId{1}
        };
        MdbxProjectionReadParticipant projection_read{
            reopened_context, projections, scope, KnowledgeUnitId{1}, ProjectionKind::Original
        };
        MdbxLexicalReadParticipant lexical_read{reopened_context, lexical};
        MdbxManifestReadParticipant manifest_read{
            reopened_context, manifests, ResourceId{"resource-read"}
        };
        read_scope.read(frontier);
        read_scope.read(canonical_read);
        read_scope.read(unit_read);
        read_scope.read(projection_read);
        read_scope.read(lexical_read);
        read_scope.read(manifest_read);
        read_scope.finish();
        if(read_scope.observation().completion != MdbxPublicationReadCompletion::TargetExact) {
            return fail("fresh context close/reopen must preserve TargetExact evidence");
        }
        mdbxc::KeyValueTable<std::string, std::string> metadata(
            reopened_context->connection(), "agent_memory_publication_coordinator_v1"
        );
        auto corrupt = reopened_context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE
        );
        metadata.insert_or_assign(scope.value() + "|meta/latest-receipt", "corrupt", corrupt);
        corrupt.commit();
        auto corrupt_scope = MdbxPublicationReadScope::begin(reopened_context);
        MdbxFrontierReadParticipant corrupt_frontier{
            reopened_context, reopened_coordinator
        };
        corrupt_scope.read(corrupt_frontier);
        corrupt_scope.read(canonical_read);
        corrupt_scope.read(unit_read);
        corrupt_scope.read(projection_read);
        corrupt_scope.read(lexical_read);
        corrupt_scope.read(manifest_read);
        corrupt_scope.finish();
        if(corrupt_scope.observation().completion == MdbxPublicationReadCompletion::TargetExact) {
            return fail("corrupt receipt must not produce TargetExact evidence");
        }
    }
    std::filesystem::remove(reopen_database, cleanup);

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
    MdbxManifestPublicationParticipant complete_manifest_participant{
        complete_context,
        complete_manifests,
        manifest_value(ResourceManifestPayloadVersion::V6),
        std::nullopt
    };
    MdbxProjectionPublicationParticipant complete_projection_participant{
        complete_context,
        complete_projections,
        projection_value(scope)
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
    const auto read_complete_target = [&]() {
        auto scope_read = MdbxPublicationReadScope::begin(complete_context);
        scope_read.read(complete_frontier);
        scope_read.read(complete_canonical_read);
        scope_read.read(complete_unit_read);
        scope_read.read(complete_projection_read);
        scope_read.read(complete_lexical_read);
        scope_read.read(complete_manifest_read);
        scope_read.finish();
        return scope_read.observation().completion;
    };
    if(read_complete_target() != MdbxPublicationReadCompletion::TargetExact) {
        return fail("one coordinated commit must produce TargetExact after pinned read");
    }

    {
        auto duplicate_scope = MdbxPublicationReadScope::begin(complete_context);
        duplicate_scope.read(complete_frontier);
        try {
            duplicate_scope.read(complete_frontier);
            return fail("duplicate authoritative read family must be rejected");
        } catch(const std::invalid_argument&) {}
    }
    {
        auto missing_scope = MdbxPublicationReadScope::begin(complete_context);
        missing_scope.read(complete_frontier);
        missing_scope.read(complete_canonical_read);
        missing_scope.read(complete_unit_read);
        missing_scope.read(complete_projection_read);
        missing_scope.read(complete_lexical_read);
        try {
            missing_scope.finish();
            return fail("missing authoritative read family must be rejected");
        } catch(const std::logic_error&) {}
    }

    complete_projections.put(projection_value(scope, "historical body", 9));
    complete_lexical.upsert(FlatLexicalUnitInput{
        scope,
        KnowledgeUnitId{99},
        ProjectionKind::Original,
        ResourceId{"unrelated-resource"},
        7,
        3,
        ProjectionVersionRef{3, 4, 5, "unrelated-projection"},
        {"unrelated", "tokens"}
    });
    if(read_complete_target() != MdbxPublicationReadCompletion::TargetExact) {
        return fail(
            "historical projections and unrelated lexical units must preserve target exactness"
        );
    }
    {
        auto wrong_unit_scope = MdbxPublicationReadScope::begin(complete_context);
        MdbxKnowledgeUnitReadParticipant wrong_unit{
            complete_context, complete_units, KnowledgeUnitId{99}
        };
        wrong_unit_scope.read(complete_frontier);
        wrong_unit_scope.read(complete_canonical_read);
        wrong_unit_scope.read(wrong_unit);
        wrong_unit_scope.read(complete_projection_read);
        wrong_unit_scope.read(complete_lexical_read);
        wrong_unit_scope.read(complete_manifest_read);
        wrong_unit_scope.finish();
        if(wrong_unit_scope.observation().completion
            == MdbxPublicationReadCompletion::TargetExact) {
            return fail("wrong unit identity must invalidate TargetExact");
        }
    }

    mdbxc::KeyValueTable<std::string, std::string> complete_metadata(
        complete_context->connection(), "agent_memory_publication_coordinator_v1"
    );
    const auto read_metadata = [&](const std::string& key) {
        auto transaction = complete_context->connection()->transaction(
            mdbxc::TransactionMode::READ_ONLY
        );
        const auto value = complete_metadata.find(key, transaction);
        transaction.commit();
        return value;
    };
    const auto write_metadata = [&complete_context, &complete_metadata](
        const std::string& key,
        const std::optional<std::string>& value
    ) {
        auto transaction = complete_context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE
        );
        if(value) {
            complete_metadata.insert_or_assign(key, *value, transaction);
        } else {
            complete_metadata.erase(key, transaction);
        }
        transaction.commit();
    };
    const auto binding_key = scope.value() + "|binding:resource-read";
    const auto original_binding = read_metadata(binding_key);
    if(!original_binding) {
        return fail("complete publication did not persist its resource binding");
    }
    const auto rejects_binding = [&](const std::optional<std::string>& candidate) {
        write_metadata(binding_key, candidate);
        const bool rejected = read_complete_target()
            != MdbxPublicationReadCompletion::TargetExact;
        write_metadata(binding_key, original_binding);
        return rejected;
    };
    if(!rejects_binding(std::nullopt)) {
        return fail("missing durable binding must invalidate TargetExact");
    }
    if(!rejects_binding(std::string{"malformed"})) {
        return fail("malformed durable binding must invalidate TargetExact");
    }
    auto another_resource_binding = *original_binding;
    if(!replace_once(another_resource_binding, "resource-read", "resource-evil")
        || !rejects_binding(another_resource_binding)) {
        return fail("binding for another resource must invalidate TargetExact");
    }
    auto stale_binding = *original_binding;
    if(!replace_once(stale_binding, "complete-document", "outdated-document")
        || !rejects_binding(stale_binding)) {
        return fail("stale durable binding must invalidate TargetExact");
    }
    const auto complete_plan = metadata_plan(scope, profile, fencing, 1, 6);
    auto projection_digest_binding = *original_binding;
    auto changed_projection_digest = complete_plan.target_evidence.projection_payload_digest;
    changed_projection_digest.front() = changed_projection_digest.front() == '0' ? '1' : '0';
    if(!replace_once(
            projection_digest_binding,
            complete_plan.target_evidence.projection_payload_digest,
            changed_projection_digest
        ) || !rejects_binding(projection_digest_binding)) {
        return fail("changed projection digest binding must invalidate TargetExact");
    }
    auto manifest_digest_binding = *original_binding;
    auto changed_manifest_digest = complete_plan.target_evidence.manifest_binding_digest;
    changed_manifest_digest.front() = changed_manifest_digest.front() == '0' ? '1' : '0';
    if(!replace_once(
            manifest_digest_binding,
            complete_plan.target_evidence.manifest_binding_digest,
            changed_manifest_digest
        ) || !rejects_binding(manifest_digest_binding)) {
        return fail("changed manifest digest binding must invalidate TargetExact");
    }
    auto moved_generation_binding = *original_binding;
    const auto generation_last_byte = 8U + std::string_view{"resource-read"}.size() + 7U;
    if(generation_last_byte >= moved_generation_binding.size()) {
        return fail("resource generation binding fixture is malformed");
    }
    moved_generation_binding[generation_last_byte] = 2;
    if(!rejects_binding(moved_generation_binding)) {
        return fail("binding moved to another generation must invalidate TargetExact");
    }

    const auto publication_receipt_key = scope.value() + "|receipt:read-publication";
    const auto original_publication_receipt = read_metadata(publication_receipt_key);
    if(!original_publication_receipt) {
        return fail("complete publication did not persist its per-publication receipt");
    }
    write_metadata(publication_receipt_key, std::string{"malformed"});
    const bool malformed_receipt_rejected = read_complete_target()
        != MdbxPublicationReadCompletion::TargetExact;
    write_metadata(publication_receipt_key, original_publication_receipt);
    if(!malformed_receipt_rejected) {
        return fail("malformed per-publication receipt must invalidate TargetExact");
    }

    const auto frontier_key = scope.value() + "|meta/frontier";
    const auto original_frontier = read_metadata(frontier_key);
    if(!original_frontier || original_frontier->size() != 8U) {
        return fail("complete publication frontier fixture is malformed");
    }
    auto mismatched_frontier = *original_frontier;
    mismatched_frontier.back() = 2;
    write_metadata(frontier_key, mismatched_frontier);
    const bool mismatched_frontier_rejected = read_complete_target()
        != MdbxPublicationReadCompletion::TargetExact;
    write_metadata(frontier_key, original_frontier);
    if(!mismatched_frontier_rejected) {
        return fail("mismatched scope frontier must invalidate TargetExact");
    }

    if(!complete_lexical.erase(KnowledgeUnitId{1})) {
        return fail("lexical membership mutation fixture failed");
    }
    if(read_complete_target() == MdbxPublicationReadCompletion::TargetExact) {
        return fail("missing target lexical membership must invalidate TargetExact");
    }
    complete_lexical.upsert(FlatLexicalUnitInput{
        scope,
        KnowledgeUnitId{1},
        ProjectionKind::Original,
        ResourceId{"resource-read"},
        1,
        1,
        complete_projection_version,
        {"pinned", "body"}
    });
    if(read_complete_target() != MdbxPublicationReadCompletion::TargetExact) {
        return fail("restored target lexical membership must recover TargetExact");
    }

    complete_manifests.upsert_manifest(
        manifest_value(ResourceManifestPayloadVersion::V6, ResourceManifestState::ErasePending)
    );
    if(read_complete_target() == MdbxPublicationReadCompletion::TargetExact) {
        return fail("manifest state mutation must invalidate TargetExact evidence");
    }
    complete_manifests.upsert_manifest(manifest_value(ResourceManifestPayloadVersion::V6));

    const auto deleted_projection = complete_projections.delete_revision(
        scope, KnowledgeUnitId{1}, ProjectionKind::Original, 1
    );
    if(!deleted_projection) {
        return fail("projection payload mutation fixture failed");
    }
    complete_projections.put(projection_value(scope, "changed body", 2));
    if(read_complete_target() == MdbxPublicationReadCompletion::TargetExact) {
        return fail("projection payload mutation must invalidate TargetExact evidence");
    }
    complete_projections.put(projection_value(scope));

    const auto committed_canonical = complete_canonical.read_current(
        DocumentId{"complete-document"}
    );
    if(!committed_canonical) {
        return fail("committed canonical fixture disappeared");
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
    ForgedCanonicalReadParticipant forged_canonical{
        complete_context, *committed_canonical
    };
    try {
        mutated_read_scope.read(forged_canonical);
        return fail("unknown read participant must be rejected");
    } catch(const std::invalid_argument&) {}
    if(forged_canonical.context_called() || forged_canonical.read_called()) {
        return fail("unknown read participant callback must not execute");
    }
    mutated_read_scope.finish();
    if(mutated_read_scope.observation().completion == MdbxPublicationReadCompletion::TargetExact) {
        return fail("independent post-commit mutation must not remain TargetExact");
    }

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
    if(missing_schema_scope.observation().completion
        == MdbxPublicationReadCompletion::TargetExact) {
        return fail("missing coordinator schema must fail closed");
    }

    std::filesystem::remove(complete_database, cleanup);
    return 0;
}

#else
int main() { return 0; }
#endif
