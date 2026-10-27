#include <agent_memory/infrastructure/mdbx/MdbxPublicationCoordinator.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativePublicationParticipants.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionLexicalPublicationParticipants.hpp>

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

int fail(std::string_view message) {
    std::cerr << message << '\n';
    return 1;
}

std::filesystem::path test_path() {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path()
        / ("agent_memory_publication_coordinator_" + std::to_string(stamp) + ".mdbx");
}

SearchProjection publication_projection(const ScopeId& scope) {
    return SearchProjection{
        scope,
        KnowledgeUnitId{1},
        ProjectionKind::Original,
        ProjectionVersionRef{1, 1, 1, "projection-v1"},
        "title",
        "heading",
        "authoritative body",
        "",
        {},
        {}
    };
}

ResourceManifest publication_manifest(
    const ResourceManifestPayloadVersion payload_version,
    std::string resource_id = "resource-1"
) {
    return ResourceManifest{
        ResourceRevision{ResourceId{std::move(resource_id)}, 1, 1, 1},
        {},
        ResourceManifestState::Active,
        {},
        ResourceManifestSchema{"manifest-v1", 1},
        payload_version
    };
}

PublicationEvidence evidence(
    const ScopeId& scope,
    const std::uint64_t manifest_payload_version
) {
    PublicationEvidence value;
    value.resource_id = ResourceId{"resource-1"};
    value.resource_generation = 1;
    value.canonical_document_id = DocumentId{"retry-document"};
    value.canonical_revision = 1;
    value.unit_id = KnowledgeUnitId{1};
    value.unit_revision = 1;
    value.projection_version = ProjectionVersionRef{1, 1, 1, "projection-v1"};
    value.projection_payload_digest = compute_projection_payload_digest(
        publication_projection(scope)
    );
    value.manifest_schema = ResourceManifestSchema{"manifest-v1", 1};
    value.manifest_state = ResourceManifestState::Active;
    value.manifest_payload_version = static_cast<std::uint32_t>(manifest_payload_version);
    value.manifest_binding_digest = compute_manifest_binding_digest(
        publication_manifest(static_cast<ResourceManifestPayloadVersion>(
            manifest_payload_version
        ))
    );
    return value;
}

PublicationPlan plan_for(
    const ScopeId& scope,
    const PublicationProfileIdentity& profile,
    const PublicationFencingIdentity& fencing,
    const std::string& publication_id,
    const std::uint64_t expected_sequence = 0,
    const std::uint64_t manifest_payload_version = 1
) {
    auto target = evidence(scope, manifest_payload_version);
    return PublicationPlan{
        PublicationIdentity{scope, publication_id},
        PublicationScopeFrontier{scope, PublicationSequence{expected_sequence}},
        profile,
        fencing,
        PublicationEvidence{},
        target,
        PublicationPayloadIdentity{
            "payload-" + publication_id,
            "digest-" + publication_id,
            "resource-1:full"
        }
    };
}

KnowledgeUnitCreateRequest unit_request(const ScopeId& scope) {
    KnowledgeUnitCreateRequest request;
    request.kind = KnowledgeUnitKind::Chunk;
    request.scope_id = scope;
    request.primary_text = "authoritative unit";
    request.display_text = "authoritative unit";
    request.content_hash_recipe_version = 2;
    SourceRefSummary source;
    source.resource_id = ResourceId{"resource-1"};
    source.resource_revision = ResourceRevisionRef{
        source.resource_id, 1, ResourceBodyDigest{}
    };
    source.uri = "memory://resource-1";
    source.excerpt = TextRange{0, 5};
    source.confidence = 1.0;
    source.preview = "authoritative";
    source.quote_hash = compute_quote_hash(source.preview);
    request.sources.push_back(std::move(source));
    request.created_at_ms = 1;
    request.observed_at_ms = 1;
    request.priority_weight = 1.0;
    request.content_hash = compute_chunk_content_hash(
        request.scope_id,
        request.primary_text,
        request.sources,
        request.content_hash_recipe_version
    );
    return request;
}

class CountingParticipant final : public MdbxPublicationParticipant {
public:
    CountingParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        std::size_t& apply_count
    ) : m_context(std::move(context)), m_apply_count(&apply_count) {}

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override {
        return *m_context;
    }

    void apply(const mdbxc::Transaction&) override {
        ++*m_apply_count;
    }

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    std::size_t* m_apply_count;
};

} // namespace

int main() {
    const auto path = test_path();
    std::error_code cleanup_error;
    const ScopeId scope{"scope-a"};
    const PublicationProfileIdentity profile{scope, "coordinated-v1", 1};
    const PublicationFencingIdentity fencing{"writer-a", 1, "token-1"};

    {
        auto context = MdbxStorageContext::open(path.string(), false, 32);
        MdbxPublicationCoordinatorOptions options;
        options.context = context;
        options.scope_id = scope;
        MdbxPublicationCoordinator coordinator{std::move(options)};

        if(coordinator.admit_profile(profile, fencing).code
            != PublicationAdmissionCode::Admitted
            || coordinator.admit_profile(profile, fencing).code
                != PublicationAdmissionCode::AlreadyAdmitted) {
            return fail("profile admission must persist and be idempotent");
        }
        if(coordinator.admit_profile(
                PublicationProfileIdentity{scope, "other", 1}, fencing
            ).code != PublicationAdmissionCode::ProfileConflict) {
            return fail("conflicting profile admission must be rejected");
        }
        if(coordinator.rotate_fencing(fencing).code
            != PublicationAdmissionCode::AlreadyAdmitted
            || coordinator.rotate_fencing(
                PublicationFencingIdentity{"writer-a", 1, "token-0"}
            ).code != PublicationAdmissionCode::FencingConflict) {
            return fail("same or lower fencing epoch must be rejected");
        }
        const PublicationFencingIdentity next_fencing{"writer-a", 2, "token-2"};
        if(coordinator.rotate_fencing(next_fencing).code
            != PublicationAdmissionCode::Admitted) {
            return fail("higher fencing epoch must be admitted");
        }

        auto first_plan = plan_for(scope, profile, next_fencing, "publication-1");
        if(coordinator.publish(first_plan, {}).code != PublicationOutcomeCode::InvalidPlan
            || coordinator.frontier().sequence.value() != 0) {
            return fail("authoritative publication API must reject an empty participant set");
        }
        std::size_t fake_apply_count = 0;
        CountingParticipant fake_a{context, fake_apply_count};
        CountingParticipant fake_b{context, fake_apply_count};
        CountingParticipant fake_c{context, fake_apply_count};
        CountingParticipant fake_d{context, fake_apply_count};
        CountingParticipant fake_e{context, fake_apply_count};
        if(coordinator.publish(
                first_plan, {&fake_a, &fake_b, &fake_c, &fake_d, &fake_e}
            ).code != PublicationOutcomeCode::InvalidPlan
            || fake_apply_count != 0
            || coordinator.frontier().sequence.value() != 0) {
            return fail("arbitrary participants must be rejected before apply");
        }

        const auto foreign_path = test_path();
        {
        auto foreign_context = MdbxStorageContext::open(
            foreign_path.string(), false, 32
        );
        MdbxCanonicalContentStoreOptions foreign_canonical_options;
        foreign_canonical_options.context = foreign_context;
        MdbxCanonicalContentStore foreign_canonical{
            std::move(foreign_canonical_options)
        };
        MdbxKnowledgeUnitStoreOptions foreign_unit_options;
        foreign_unit_options.context = foreign_context;
        MdbxKnowledgeUnitStore foreign_units{std::move(foreign_unit_options)};
        MdbxResourceManifestStorageOptions foreign_manifest_options;
        foreign_manifest_options.context = foreign_context;
        MdbxResourceManifestStorage foreign_manifests{
            std::move(foreign_manifest_options)
        };
        MdbxProjectionStoreOptions foreign_projection_options;
        foreign_projection_options.context = foreign_context;
        MdbxProjectionStore foreign_projections{
            std::move(foreign_projection_options)
        };
        MdbxPersistentLexicalIndexOptions foreign_lexical_options;
        foreign_lexical_options.context = foreign_context;
        std::array<std::uint8_t, 16> foreign_epoch{};
        foreign_epoch[0] = 7;
        foreign_lexical_options.descriptor = LexicalIndexDescriptor{
            LexicalIndexDescriptorRef{
                scope, ProjectionKind::Original, LexicalStatisticsEpoch{foreign_epoch}
            },
            "original", 1, "tokenizer-v1", "normalization-v1", {1}
        };
        MdbxPersistentLexicalIndex foreign_lexical{
            std::move(foreign_lexical_options)
        };
        auto expect_context_rejection = [](auto&& make_participant) {
            try {
                make_participant();
            } catch(const std::invalid_argument&) {
                return true;
            }
            return false;
        };
        if(!expect_context_rejection([&] {
                MdbxCanonicalPublicationParticipant participant{
                    context,
                    foreign_canonical,
                    CanonicalDocumentRevision{
                        DocumentId{"foreign-document"}, 0, {}, {}
                    }
                };
            })
            || !expect_context_rejection([&] {
                MdbxKnowledgeUnitPublicationParticipant participant{
                    context, foreign_units, {unit_request(scope)}
                };
            })
            || !expect_context_rejection([&] {
                MdbxManifestPublicationParticipant participant{
                    context, foreign_manifests,
                    publication_manifest(ResourceManifestPayloadVersion::V1),
                    std::nullopt
                };
            })
            || !expect_context_rejection([&] {
                MdbxProjectionPublicationParticipant participant{
                    context, foreign_projections, publication_projection(scope)
                };
            })
            || !expect_context_rejection([&] {
                MdbxLexicalPublicationParticipant participant{
                    context,
                    foreign_lexical,
                    FlatLexicalUnitInput{
                        scope, KnowledgeUnitId{1}, ProjectionKind::Original,
                        ResourceId{"resource-1"}, 1, 1,
                        ProjectionVersionRef{1, 1, 1, "projection-v1"},
                        {"foreign"}
                    }
                };
            })
            || coordinator.frontier().sequence.value() != 0) {
            return fail("participants must reject a mismatched underlying store context");
        }
        }
        std::filesystem::remove(foreign_path, cleanup_error);

        const auto committed = coordinator.publish_metadata_only(first_plan);
        if(committed.code != PublicationOutcomeCode::Committed
            || !committed.receipt
            || committed.receipt->committed_frontier.sequence.value() != 1
            || coordinator.frontier().sequence.value() != 1) {
            return fail("first metadata publication must commit sequence one");
        }

        const auto replay = coordinator.publish_metadata_only(first_plan);
        if(replay.code != PublicationOutcomeCode::Replayed
            || !replay.receipt
            || !replay.receipt->replay
            || coordinator.frontier().sequence.value() != 1) {
            return fail("identical publication replay must be idempotent");
        }
        auto changed_plan = first_plan;
        changed_plan.payload.payload_digest = "changed";
        if(coordinator.publish_metadata_only(changed_plan).code
            != PublicationOutcomeCode::PublicationIdentityConflict) {
            return fail("changed replay payload must conflict");
        }
        auto stale_plan = plan_for(scope, profile, next_fencing, "publication-2");
        if(coordinator.publish_metadata_only(stale_plan).code != PublicationOutcomeCode::StaleFrontier) {
            return fail("stale new publication must be rejected");
        }
        auto wrong_fencing = first_plan;
        wrong_fencing.identity.publication_id = "publication-3";
        wrong_fencing.fencing = fencing;
        if(coordinator.publish_metadata_only(wrong_fencing).code
            != PublicationOutcomeCode::FencingMismatch) {
            return fail("publication with stale fencing must be rejected");
        }

        MdbxPublicationCoordinatorOptions second_options;
        second_options.context = context;
        second_options.scope_id = ScopeId{"scope-b"};
        MdbxPublicationCoordinator second{std::move(second_options)};
        if(second.admit_profile(
                PublicationProfileIdentity{ScopeId{"scope-b"}, "coordinated-v1", 1},
                fencing
            ).code != PublicationAdmissionCode::Admitted
            || second.frontier().sequence.value() != 0) {
            return fail("independent scopes must have independent frontiers");
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

        MdbxCanonicalPublicationParticipant canonical_participant{
            context,
            canonical,
            CanonicalDocumentRevision{
                DocumentId{"authoritative-document"},
                0,
                {},
                {ContentBlock{
                    ContentBlockId{"block-1"},
                    1,
                    ContentBlockKind::Paragraph,
                    std::nullopt,
                    "authoritative body"
                }}
            }
        };
        MdbxKnowledgeUnitPublicationParticipant unit_participant{
            context,
            units,
            {unit_request(scope)}
        };
        MdbxManifestPublicationParticipant manifest_participant{
            context,
            manifests,
            publication_manifest(ResourceManifestPayloadVersion::V1),
            std::nullopt
        };
        auto authoritative_plan = plan_for(
            scope, profile, next_fencing, "publication-authoritative", 1
        );
        const auto authoritative = coordinator.publish(
            authoritative_plan,
            {&canonical_participant, &unit_participant, &manifest_participant}
        );
        if(authoritative.code != PublicationOutcomeCode::InvalidPlan
            || coordinator.frontier().sequence.value() != 1
            || canonical.read_current(DocumentId{"authoritative-document"})
            || !units.scan_by_scope(scope).empty()
            || manifests.find_manifest(ResourceId{"resource-1"})) {
            return fail("incomplete authoritative participant sets must be rejected");
        }

        MdbxCanonicalPublicationParticipant duplicate_a{
            context,
            canonical,
            CanonicalDocumentRevision{
                DocumentId{"duplicate-a"}, 0, {},
                {ContentBlock{ContentBlockId{"duplicate-a-block"}, 1,
                    ContentBlockKind::Paragraph, std::nullopt, "a"}}
            }
        };
        MdbxCanonicalPublicationParticipant duplicate_b{
            context,
            canonical,
            CanonicalDocumentRevision{
                DocumentId{"duplicate-b"}, 0, {},
                {ContentBlock{ContentBlockId{"duplicate-b-block"}, 1,
                    ContentBlockKind::Paragraph, std::nullopt, "b"}}
            }
        };
        if(coordinator.publish(
                plan_for(scope, profile, next_fencing, "publication-duplicate", 1),
                {&duplicate_a, &duplicate_b}
            ).code != PublicationOutcomeCode::InvalidPlan
            || coordinator.frontier().sequence.value() != 1) {
            return fail("duplicate participant roles must be rejected atomically");
        }

        std::array<std::uint8_t, 16> epoch{};
        epoch[0] = 7;
        MdbxPersistentLexicalIndexOptions lexical_options;
        lexical_options.context = context;
        lexical_options.descriptor = LexicalIndexDescriptor{
            LexicalIndexDescriptorRef{
                scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}
            },
            "original",
            1,
            "tokenizer-v1",
            "normalization-v1",
            {1}
        };
        MdbxPersistentLexicalIndex lexical{std::move(lexical_options)};
        MdbxProjectionStoreOptions projection_options;
        projection_options.context = context;
        MdbxProjectionStore projections{std::move(projection_options)};
        const ProjectionVersionRef projection_version{1, 1, 1, "projection-v1"};
        const auto target_projection = publication_projection(scope);
        projections.put(target_projection);
        const auto target_unit = units.create_or_get_unit(unit_request(scope));
        const auto target_manifest_input = publication_manifest(
            ResourceManifestPayloadVersion::V6
        );
        manifests.upsert_manifest(target_manifest_input);
        const auto target_manifest = manifests.find_manifest(ResourceId{"resource-1"});
        if(target_unit.id.value() != 1 || !target_manifest) {
            return fail("publication target baseline preparation failed");
        }
        MdbxProjectionPublicationParticipant projection_participant{
            context,
            projections,
            target_projection
        };
        MdbxLexicalPublicationParticipant lexical_participant{
            context,
            lexical,
            FlatLexicalUnitInput{
                scope,
                KnowledgeUnitId{1},
                ProjectionKind::Original,
                ResourceId{"resource-1"},
                1,
                1,
                projection_version,
                {"authoritative", "body"}
            }
        };
        if(!canonical.create_document(CanonicalDocumentRevision{
                DocumentId{"retry-document"},
                0,
                {},
                {ContentBlock{ContentBlockId{"retry-block"}, 1,
                    ContentBlockKind::Paragraph, std::nullopt, "baseline"}}
            })) {
            return fail("retry canonical baseline failed");
        }
        MdbxCanonicalPublicationParticipant retry_canonical{
            context,
            canonical,
            CanonicalEditRequest{
                DocumentId{"retry-document"},
                0,
                {ReplaceBlockText{ContentBlockId{"retry-block"}, "authoritative body"}}
            }
        };
        MdbxCanonicalPublicationParticipant failing_canonical{
            context,
            canonical,
            CanonicalEditRequest{
                DocumentId{"missing-document"},
                0,
                {ReplaceBlockText{ContentBlockId{"missing-block"}, "never written"}}
            }
        };
        MdbxManifestPublicationParticipant target_manifest_participant{
            context,
            manifests,
            target_manifest_input,
            target_manifest
        };
        const auto no_partial_publication = [&](const std::string& publication_id) {
            const auto document = canonical.read_current(DocumentId{"retry-document"});
            const auto unit_rows = units.scan_by_scope(scope);
            const auto projection_rows = projections.list(
                scope, KnowledgeUnitId{1}, ProjectionKind::Original
            );
            auto read = context->connection()->transaction(
                mdbxc::TransactionMode::READ_ONLY
            );
            const auto binding = coordinator.binding_in_transaction(
                ResourceId{"resource-1"}, read
            );
            read.commit();
            const auto frontier_unchanged = coordinator.frontier().sequence.value() == 1;
            const auto receipt_absent = !coordinator.receipt(publication_id);
            const auto unrelated_manifest_absent = !manifests.find_manifest(
                ResourceId{"resource-2"}
            );
            return frontier_unchanged
                && receipt_absent
                && !binding
                && document
                && document->revision == 0
                && unit_rows.size() == 1
                && projection_rows.size() == 1
                && lexical.read_snapshot().index.size() == 0
                && unrelated_manifest_absent;
        };
        const auto apply_failure_plan = plan_for(
            scope, profile, next_fencing, "publication-apply-failure", 1, 6
        );
        if(coordinator.publish(
                apply_failure_plan,
                {&failing_canonical, &unit_participant, &projection_participant,
                 &lexical_participant, &target_manifest_participant}
            ).code != PublicationOutcomeCode::CommitFailure
            || !no_partial_publication("publication-apply-failure")) {
            return fail("participant failure must roll back data, binding and frontier");
        }

        auto unrelated_unit_request = unit_request(scope);
        unrelated_unit_request.primary_text = "unrelated unit";
        unrelated_unit_request.display_text = "unrelated unit";
        unrelated_unit_request.content_hash = compute_chunk_content_hash(
            unrelated_unit_request.scope_id,
            unrelated_unit_request.primary_text,
            unrelated_unit_request.sources,
            unrelated_unit_request.content_hash_recipe_version
        );
        MdbxKnowledgeUnitPublicationParticipant unrelated_unit_participant{
            context, units, {std::move(unrelated_unit_request)}
        };
        const auto unit_mismatch_plan = plan_for(
            scope, profile, next_fencing, "publication-unit-mismatch", 1, 6
        );
        if(coordinator.publish(
                unit_mismatch_plan,
                {&retry_canonical, &unrelated_unit_participant, &projection_participant,
                 &lexical_participant, &target_manifest_participant}
            ).code != PublicationOutcomeCode::BindingMismatch
            || !no_partial_publication("publication-unit-mismatch")) {
            return fail("unrelated unit result must not validate an existing target row");
        }

        MdbxManifestPublicationParticipant unrelated_manifest_participant{
            context,
            manifests,
            publication_manifest(ResourceManifestPayloadVersion::V1, "resource-2"),
            std::nullopt
        };
        const auto manifest_mismatch_plan = plan_for(
            scope, profile, next_fencing, "publication-manifest-mismatch", 1, 6
        );
        if(coordinator.publish(
                manifest_mismatch_plan,
                {&retry_canonical, &unit_participant, &projection_participant,
                 &lexical_participant, &unrelated_manifest_participant}
            ).code != PublicationOutcomeCode::BindingMismatch
            || !no_partial_publication("publication-manifest-mismatch")) {
            return fail("unrelated manifest write must not validate an existing target row");
        }

        auto unrelated_projection = target_projection;
        unrelated_projection.version.projection_revision = 2;
        unrelated_projection.body = "unrelated projection";
        MdbxProjectionPublicationParticipant unrelated_projection_participant{
            context, projections, std::move(unrelated_projection)
        };
        const auto projection_mismatch_plan = plan_for(
            scope, profile, next_fencing, "publication-projection-mismatch", 1, 6
        );
        if(coordinator.publish(
                projection_mismatch_plan,
                {&retry_canonical, &unit_participant, &unrelated_projection_participant,
                 &lexical_participant, &target_manifest_participant}
            ).code != PublicationOutcomeCode::BindingMismatch
            || !no_partial_publication("publication-projection-mismatch")) {
            return fail("unrelated projection write must not validate an existing target row");
        }

        auto retry_plan = plan_for(
            scope, profile, next_fencing, "publication-retry", 1, 6
        );
        const auto retry_result = coordinator.publish(
            retry_plan,
            {&retry_canonical, &unit_participant, &projection_participant,
             &lexical_participant, &target_manifest_participant}
        );
        if(retry_result.code != PublicationOutcomeCode::Committed
            || coordinator.frontier().sequence.value() != 2
            || !canonical.read_current(DocumentId{"retry-document"})
            || units.scan_by_scope(scope).size() != 1
            || !manifests.find_manifest(ResourceId{"resource-1"})
            || projections.list(scope, KnowledgeUnitId{1}, ProjectionKind::Original).size() != 1
            || lexical.read_snapshot().index.size() != 1) {
            return fail("complete participant publication must be atomic and retryable");
        }

        mdbxc::KeyValueTable<std::string, std::string> metadata(
            context->connection(),
            "agent_memory_publication_coordinator_v1"
        );
        auto corruption = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE
        );
        metadata.insert_or_assign(scope.value() + "|meta/profile", "malformed", corruption);
        corruption.commit();
        if(coordinator.admit_profile(profile, next_fencing).code
            != PublicationAdmissionCode::InvalidModel) {
            return fail("malformed durable profile metadata must fail closed");
        }
    }

    {
        auto context = MdbxStorageContext::open(path.string(), false, 32);
        MdbxPublicationCoordinatorOptions options;
        options.context = context;
        options.scope_id = scope;
        MdbxPublicationCoordinator reopened{std::move(options)};
        if(reopened.frontier().sequence.value() != 2
            || !reopened.receipt("publication-1")) {
            return fail("frontier and receipt must survive reopen");
        }
    }

    std::filesystem::remove(path, cleanup_error);
    return 0;
}

#else

int main() {
    return 0;
}

#endif
