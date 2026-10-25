#include "PublicationPlan.hpp"

#include <limits>
#include <utility>

namespace agent_memory {
namespace {

    bool same_projection_version(
        const ProjectionVersionRef& left,
        const ProjectionVersionRef& right
    ) noexcept {
        return left.unit_revision_at_build == right.unit_revision_at_build
            && left.projection_revision == right.projection_revision
            && left.derivation_generation == right.derivation_generation
            && left.derivation_fingerprint == right.derivation_fingerprint;
    }

    bool same_manifest_schema(
        const ResourceManifestSchema& left,
        const ResourceManifestSchema& right
    ) noexcept {
        return left.schema_id == right.schema_id
            && left.schema_version == right.schema_version;
    }

    bool same_profile(
        const PublicationProfileIdentity& left,
        const PublicationProfileIdentity& right
    ) noexcept {
        return left.scope_id == right.scope_id
            && left.profile_id == right.profile_id
            && left.profile_version == right.profile_version;
    }

    bool same_fencing(
        const PublicationFencingIdentity& left,
        const PublicationFencingIdentity& right
    ) noexcept {
        return left.owner_id == right.owner_id
            && left.epoch == right.epoch
            && left.token_digest == right.token_digest;
    }

    bool same_payload(
        const PublicationPayloadIdentity& left,
        const PublicationPayloadIdentity& right
    ) noexcept {
        return left.payload_id == right.payload_id
            && left.payload_digest == right.payload_digest
            && left.canonical_coverage == right.canonical_coverage;
    }

    bool same_evidence(
        const PublicationEvidence& left,
        const PublicationEvidence& right
    ) noexcept {
        return left.resource_id == right.resource_id
            && left.resource_generation == right.resource_generation
            && left.unit_revision == right.unit_revision
            && same_projection_version(left.projection_version, right.projection_version)
            && same_manifest_schema(left.manifest_schema, right.manifest_schema)
            && left.manifest_payload_version == right.manifest_payload_version
            && left.manifest_binding_digest == right.manifest_binding_digest;
    }

    bool is_empty_expected_evidence(const PublicationEvidence& evidence) noexcept {
        return evidence.resource_generation == 0
            && evidence.unit_revision == 0
            && evidence.projection_version.unit_revision_at_build == 0
            && evidence.projection_version.projection_revision == 0
            && evidence.projection_version.derivation_generation == 0
            && evidence.projection_version.derivation_fingerprint.empty()
            && evidence.manifest_schema.schema_id.empty()
            && evidence.manifest_schema.schema_version == 0
            && evidence.manifest_payload_version == 0
            && evidence.manifest_binding_digest.empty();
    }

} // namespace

bool is_valid_publication_identity(
    const PublicationIdentity& identity
) noexcept {
    return !identity.scope_id.empty() && !identity.publication_id.empty();
}

bool is_valid_publication_profile(
    const PublicationProfileIdentity& profile
) noexcept {
    return !profile.scope_id.empty()
        && !profile.profile_id.empty()
        && profile.profile_version != 0;
}

bool is_valid_publication_fencing(
    const PublicationFencingIdentity& fencing
) noexcept {
    return !fencing.owner_id.empty()
        && fencing.epoch != 0
        && !fencing.token_digest.empty();
}

bool is_valid_publication_payload(
    const PublicationPayloadIdentity& payload
) noexcept {
    return !payload.payload_id.empty()
        && !payload.payload_digest.empty()
        && !payload.canonical_coverage.empty();
}

bool is_valid_publication_evidence(
    const PublicationEvidence& evidence
) noexcept {
    return !evidence.resource_id.empty()
        && evidence.resource_generation != 0
        && evidence.unit_revision != 0
        && is_valid_projection_version(evidence.projection_version)
        && !evidence.manifest_schema.schema_id.empty()
        && evidence.manifest_schema.schema_version != 0
        && evidence.manifest_payload_version != 0
        && !evidence.manifest_binding_digest.empty();
}

bool is_valid_publication_plan(
    const PublicationPlan& plan
) noexcept {
    if(!is_valid_publication_identity(plan.identity)
        || !is_valid_publication_profile(plan.profile)
        || !is_valid_publication_fencing(plan.fencing)
        || !is_valid_publication_payload(plan.payload)
        || plan.expected_frontier.scope_id != plan.identity.scope_id
        || plan.profile.scope_id != plan.identity.scope_id
        || !is_valid_publication_evidence(plan.target_evidence)) {
        return false;
    }

    const auto expected_is_empty = is_empty_expected_evidence(plan.expected_evidence);
    if(expected_is_empty) {
        if(!plan.expected_evidence.resource_id.empty()
            && plan.expected_evidence.resource_id != plan.target_evidence.resource_id) {
            return false;
        }
    } else if(!is_valid_publication_evidence(plan.expected_evidence)
        || plan.expected_evidence.resource_id != plan.target_evidence.resource_id) {
        return false;
    }

    if(!expected_is_empty
        && (plan.target_evidence.resource_generation
                < plan.expected_evidence.resource_generation
            || plan.target_evidence.unit_revision < plan.expected_evidence.unit_revision)) {
        return false;
    }

    return true;
}

PublicationConformanceModel::PublicationConformanceModel(
    ScopeId scope_id,
    PublicationFencingIdentity initial_fencing,
    const PublicationSequence initial_sequence
) : m_scope_id(std::move(scope_id)),
    m_fencing(std::move(initial_fencing)),
    m_frontier{m_scope_id, initial_sequence} {}

PublicationAdmissionResult PublicationConformanceModel::admit_profile(
    PublicationProfileIdentity profile
) {
    if(!is_valid_publication_profile(profile) || profile.scope_id != m_scope_id) {
        return PublicationAdmissionResult{PublicationAdmissionCode::InvalidProfile};
    }
    if(!m_profile.has_value()) {
        m_profile = std::move(profile);
        return PublicationAdmissionResult{PublicationAdmissionCode::Admitted};
    }
    if(same_profile(*m_profile, profile)) {
        return PublicationAdmissionResult{PublicationAdmissionCode::AlreadyAdmitted};
    }
    return PublicationAdmissionResult{PublicationAdmissionCode::ProfileConflict};
}

PublicationAdmissionResult PublicationConformanceModel::rotate_fencing(
    PublicationFencingIdentity fencing
) {
    if(!is_valid_publication_fencing(fencing)) {
        return PublicationAdmissionResult{PublicationAdmissionCode::InvalidFencing};
    }
    m_fencing = std::move(fencing);
    return PublicationAdmissionResult{PublicationAdmissionCode::Admitted};
}

PublicationOutcome PublicationConformanceModel::publish(
    const PublicationPlan& plan
) {
    if(!is_valid_publication_plan(plan)) {
        return PublicationOutcome{PublicationOutcomeCode::InvalidPlan, std::nullopt};
    }
    if(plan.identity.scope_id != m_scope_id
        || plan.expected_frontier.scope_id != m_scope_id) {
        return PublicationOutcome{PublicationOutcomeCode::ScopeMismatch, std::nullopt};
    }
    if(!m_profile.has_value()) {
        return PublicationOutcome{PublicationOutcomeCode::NotAdmitted, std::nullopt};
    }
    if(!same_profile(*m_profile, plan.profile)) {
        return PublicationOutcome{PublicationOutcomeCode::ProfileMismatch, std::nullopt};
    }
    if(!same_fencing(m_fencing, plan.fencing)) {
        return PublicationOutcome{PublicationOutcomeCode::FencingMismatch, std::nullopt};
    }

    const auto existing = m_committed.find(plan.identity.publication_id);
    if(existing != m_committed.end()) {
        if(equivalent_replay(plan, existing->second)) {
            auto receipt = existing->second.receipt;
            receipt.replay = true;
            return PublicationOutcome{PublicationOutcomeCode::Replayed, std::move(receipt)};
        }
        return PublicationOutcome{
            PublicationOutcomeCode::PublicationIdentityConflict,
            std::nullopt
        };
    }

    if(plan.expected_frontier.sequence.value() != m_frontier.sequence.value()) {
        return PublicationOutcome{PublicationOutcomeCode::StaleFrontier, std::nullopt};
    }

    const auto binding = m_bindings.find(plan.target_evidence.resource_id);
    if(binding != m_bindings.end()) {
        if(is_empty_expected_evidence(plan.expected_evidence)
            || !matches_existing_binding(plan.expected_evidence, binding->second)) {
            return PublicationOutcome{PublicationOutcomeCode::BindingMismatch, std::nullopt};
        }
    } else if(!is_empty_expected_evidence(plan.expected_evidence)) {
        return PublicationOutcome{PublicationOutcomeCode::BindingMismatch, std::nullopt};
    }

    const auto current_sequence = m_frontier.sequence.value();
    if(current_sequence == std::numeric_limits<std::uint64_t>::max()) {
        return PublicationOutcome{PublicationOutcomeCode::SequenceOverflow, std::nullopt};
    }

    PublicationReceipt receipt{
        plan.identity,
        PublicationScopeFrontier{
            m_scope_id,
            PublicationSequence{current_sequence + 1}
        },
        plan.payload,
        false
    };
    CommittedPublication committed{plan, receipt};

    // The model installs the fully prepared replacement only after all checks pass.
    m_bindings[plan.target_evidence.resource_id] = plan.target_evidence;
    m_frontier = receipt.committed_frontier;
    m_committed.emplace(plan.identity.publication_id, std::move(committed));
    return PublicationOutcome{PublicationOutcomeCode::Committed, std::move(receipt)};
}

const PublicationScopeFrontier& PublicationConformanceModel::frontier() const noexcept {
    return m_frontier;
}

bool PublicationConformanceModel::has_admitted_profile() const noexcept {
    return m_profile.has_value();
}

const std::optional<PublicationProfileIdentity>&
PublicationConformanceModel::profile() const noexcept {
    return m_profile;
}

bool PublicationConformanceModel::matches_existing_binding(
    const PublicationEvidence& expected,
    const PublicationEvidence& current
) const noexcept {
    return same_evidence(expected, current);
}

bool PublicationConformanceModel::equivalent_replay(
    const PublicationPlan& plan,
    const CommittedPublication& committed
) const noexcept {
    return plan.identity.scope_id == committed.plan.identity.scope_id
        && plan.identity.publication_id == committed.plan.identity.publication_id
        && same_profile(plan.profile, committed.plan.profile)
        && same_payload(plan.payload, committed.plan.payload)
        && same_evidence(plan.target_evidence, committed.plan.target_evidence);
}

} // namespace agent_memory
