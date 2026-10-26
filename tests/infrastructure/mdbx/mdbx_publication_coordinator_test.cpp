#include <agent_memory/infrastructure/mdbx/MdbxPublicationCoordinator.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxAuthoritativePublicationParticipants.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <filesystem>
#include <iostream>
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

PublicationEvidence evidence() {
    PublicationEvidence value;
    value.resource_id = ResourceId{"resource-1"};
    value.resource_generation = 1;
    value.canonical_revision = 1;
    value.unit_revision = 1;
    value.projection_version = ProjectionVersionRef{1, 1, 1, "projection-v1"};
    value.manifest_schema = ResourceManifestSchema{"manifest-v1", 1};
    value.manifest_revision = 1;
    value.manifest_state = ResourceManifestState::Active;
    value.manifest_payload_version = 1;
    value.manifest_binding_digest = "binding-v1";
    return value;
}

PublicationPlan plan_for(
    const ScopeId& scope,
    const PublicationProfileIdentity& profile,
    const PublicationFencingIdentity& fencing,
    const std::string& publication_id,
    const std::uint64_t expected_sequence = 0
) {
    const auto target = evidence();
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

class FailingParticipant final : public MdbxPublicationParticipant {
public:
    explicit FailingParticipant(std::shared_ptr<MdbxStorageContext> context)
        : m_context(std::move(context)) {}

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override {
        return *m_context;
    }

    void apply(const mdbxc::Transaction&) override {
        throw std::runtime_error("injected authoritative participant failure");
    }

private:
    std::shared_ptr<MdbxStorageContext> m_context;
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
        const auto committed = coordinator.publish(first_plan);
        if(committed.code != PublicationOutcomeCode::Committed
            || !committed.receipt
            || committed.receipt->committed_frontier.sequence.value() != 1
            || coordinator.frontier().sequence.value() != 1) {
            return fail("first metadata publication must commit sequence one");
        }

        const auto replay = coordinator.publish(first_plan);
        if(replay.code != PublicationOutcomeCode::Replayed
            || !replay.receipt
            || !replay.receipt->replay
            || coordinator.frontier().sequence.value() != 1) {
            return fail("identical publication replay must be idempotent");
        }
        auto changed_plan = first_plan;
        changed_plan.payload.payload_digest = "changed";
        if(coordinator.publish(changed_plan).code
            != PublicationOutcomeCode::PublicationIdentityConflict) {
            return fail("changed replay payload must conflict");
        }
        auto stale_plan = plan_for(scope, profile, next_fencing, "publication-2");
        if(coordinator.publish(stale_plan).code != PublicationOutcomeCode::StaleFrontier) {
            return fail("stale new publication must be rejected");
        }
        auto wrong_fencing = first_plan;
        wrong_fencing.identity.publication_id = "publication-3";
        wrong_fencing.fencing = fencing;
        if(coordinator.publish(wrong_fencing).code
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
            ResourceManifest{
                ResourceRevision{ResourceId{"resource-1"}, 1, 1, 1},
                {}
            },
            std::nullopt
        };
        auto authoritative_plan = plan_for(
            scope, profile, next_fencing, "publication-authoritative", 1
        );
        const auto authoritative = coordinator.publish(
            authoritative_plan,
            {&canonical_participant, &unit_participant, &manifest_participant}
        );
        if(authoritative.code != PublicationOutcomeCode::Committed
            || coordinator.frontier().sequence.value() != 2
            || !canonical.read_current(DocumentId{"authoritative-document"})
            || units.scan_by_scope(scope).size() != 1
            || !manifests.find_manifest(ResourceId{"resource-1"})) {
            return fail("authoritative participants must publish atomically");
        }

        FailingParticipant failing{context};
        auto failed_plan = plan_for(scope, profile, next_fencing, "publication-failed", 2);
        if(coordinator.publish(failed_plan, {&failing}).code
            != PublicationOutcomeCode::CommitFailure
            || coordinator.frontier().sequence.value() != 2
            || coordinator.receipt("publication-failed")) {
            return fail("participant failure must roll back metadata and frontier");
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
