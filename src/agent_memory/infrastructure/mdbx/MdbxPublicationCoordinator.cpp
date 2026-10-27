#include "MdbxPublicationCoordinator.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxAuthoritativePublicationParticipants.hpp"
#include "MdbxProjectionLexicalPublicationParticipants.hpp"
#include "MdbxPublicationWriteScope.hpp"

#include <mdbx_containers/KeyValueTable.hpp>

#include <cstdint>
#include <algorithm>
#include <array>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr const char* kSchemaKey = "meta/schema";
constexpr const char* kProfileKey = "meta/profile";
constexpr const char* kFencingKey = "meta/fencing";
constexpr const char* kFrontierKey = "meta/frontier";
constexpr const char* kLatestReceiptKey = "meta/latest-receipt";
constexpr const char* kSchemaValue = "agent_memory.publication_coordinator.v1";

void append_u64(std::string& out, const std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8) {
        out.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

std::uint64_t read_u64(std::string_view value, std::size_t& position) {
    if(position + 8U > value.size()) {
        throw std::runtime_error("truncated publication metadata integer");
    }
    std::uint64_t result = 0;
    for(std::size_t index = 0; index < 8U; ++index) {
        result = (result << 8U)
            | static_cast<std::uint8_t>(value[position++]);
    }
    return result;
}

void append_text(std::string& out, std::string_view value) {
    append_u64(out, static_cast<std::uint64_t>(value.size()));
    out.append(value.data(), value.size());
}

std::string read_text(std::string_view value, std::size_t& position) {
    const auto size = read_u64(value, position);
    if(size > value.size() - position) {
        throw std::runtime_error("truncated publication metadata string");
    }
    const std::string result{value.substr(position, static_cast<std::size_t>(size))};
    position += static_cast<std::size_t>(size);
    return result;
}

void append_scope(std::string& out, const ScopeId& scope) {
    append_text(out, scope.value());
}

ScopeId read_scope(std::string_view value, std::size_t& position) {
    return ScopeId{read_text(value, position)};
}

void append_resource(std::string& out, const ResourceId& resource) {
    append_text(out, resource.value());
}

ResourceId read_resource(std::string_view value, std::size_t& position) {
    return ResourceId{read_text(value, position)};
}

void append_evidence(std::string& out, const PublicationEvidence& evidence) {
    append_resource(out, evidence.resource_id);
    append_u64(out, evidence.resource_generation);
    append_text(out, evidence.canonical_document_id.value());
    append_u64(out, evidence.canonical_revision);
    append_u64(out, evidence.unit_id.value());
    append_u64(out, evidence.unit_revision);
    append_u64(out, evidence.projection_version.unit_revision_at_build);
    append_u64(out, evidence.projection_version.projection_revision);
    append_u64(out, evidence.projection_version.derivation_generation);
    append_text(out, evidence.projection_version.derivation_fingerprint);
    append_text(out, evidence.projection_payload_digest);
    append_text(out, evidence.manifest_schema.schema_id);
    append_u64(out, evidence.manifest_schema.schema_version);
    append_u64(out, static_cast<std::uint8_t>(evidence.manifest_state));
    append_u64(out, evidence.manifest_payload_version);
    append_text(out, evidence.manifest_binding_digest);
}

std::string serialize_evidence(const PublicationEvidence& evidence) {
    std::string value;
    append_evidence(value, evidence);
    return value;
}

PublicationEvidence read_evidence(std::string_view value, std::size_t& position) {
    PublicationEvidence evidence;
    evidence.resource_id = read_resource(value, position);
    evidence.resource_generation = read_u64(value, position);
    evidence.canonical_document_id = DocumentId{read_text(value, position)};
    evidence.canonical_revision = read_u64(value, position);
    evidence.unit_id = KnowledgeUnitId{read_u64(value, position)};
    evidence.unit_revision = read_u64(value, position);
    evidence.projection_version.unit_revision_at_build = read_u64(value, position);
    evidence.projection_version.projection_revision = read_u64(value, position);
    evidence.projection_version.derivation_generation = read_u64(value, position);
    evidence.projection_version.derivation_fingerprint = read_text(value, position);
    evidence.projection_payload_digest = read_text(value, position);
    evidence.manifest_schema.schema_id = read_text(value, position);
    evidence.manifest_schema.schema_version = static_cast<std::uint32_t>(
        read_u64(value, position)
    );
    evidence.manifest_state = static_cast<ResourceManifestState>(
        read_u64(value, position)
    );
    evidence.manifest_payload_version = static_cast<std::uint32_t>(
        read_u64(value, position)
    );
    evidence.manifest_binding_digest = read_text(value, position);
    return evidence;
}

PublicationEvidence deserialize_evidence(std::string_view value) {
    std::size_t position = 0;
    const auto evidence = read_evidence(value, position);
    if(position != value.size()) {
        throw std::runtime_error("trailing publication evidence metadata");
    }
    return evidence;
}

std::string serialize_profile(const PublicationProfileIdentity& profile) {
    std::string value;
    append_scope(value, profile.scope_id);
    append_text(value, profile.profile_id);
    append_u64(value, profile.profile_version);
    return value;
}

PublicationProfileIdentity deserialize_profile(std::string_view value) {
    std::size_t position = 0;
    PublicationProfileIdentity profile{
        read_scope(value, position),
        read_text(value, position),
        static_cast<std::uint32_t>(read_u64(value, position))
    };
    if(position != value.size()) {
        throw std::runtime_error("trailing publication profile metadata");
    }
    return profile;
}

PublicationProfileIdentity read_profile(std::string_view value, std::size_t& position) {
    return PublicationProfileIdentity{
        read_scope(value, position),
        read_text(value, position),
        static_cast<std::uint32_t>(read_u64(value, position))
    };
}

std::string serialize_fencing(const PublicationFencingIdentity& fencing) {
    std::string value;
    append_text(value, fencing.owner_id);
    append_u64(value, fencing.epoch);
    append_text(value, fencing.token_digest);
    return value;
}

PublicationFencingIdentity deserialize_fencing(std::string_view value) {
    std::size_t position = 0;
    PublicationFencingIdentity fencing{
        read_text(value, position),
        read_u64(value, position),
        read_text(value, position)
    };
    if(position != value.size()) {
        throw std::runtime_error("trailing publication fencing metadata");
    }
    return fencing;
}

PublicationFencingIdentity read_fencing(std::string_view value, std::size_t& position) {
    return PublicationFencingIdentity{
        read_text(value, position),
        read_u64(value, position),
        read_text(value, position)
    };
}

std::string serialize_plan(const PublicationPlan& plan) {
    std::string value;
    append_scope(value, plan.identity.scope_id);
    append_text(value, plan.identity.publication_id);
    append_scope(value, plan.expected_frontier.scope_id);
    append_u64(value, plan.expected_frontier.sequence.value());
    value += serialize_profile(plan.profile);
    value += serialize_fencing(plan.fencing);
    append_evidence(value, plan.expected_evidence);
    append_evidence(value, plan.target_evidence);
    append_text(value, plan.payload.payload_id);
    append_text(value, plan.payload.payload_digest);
    append_text(value, plan.payload.canonical_coverage);
    return value;
}

PublicationPlan deserialize_plan(std::string_view value) {
    std::size_t position = 0;
    PublicationPlan plan;
    plan.identity.scope_id = read_scope(value, position);
    plan.identity.publication_id = read_text(value, position);
    plan.expected_frontier.scope_id = read_scope(value, position);
    plan.expected_frontier.sequence = PublicationSequence{read_u64(value, position)};
    plan.profile = read_profile(value, position);
    plan.fencing = read_fencing(value, position);
    plan.expected_evidence = read_evidence(value, position);
    plan.target_evidence = read_evidence(value, position);
    plan.payload.payload_id = read_text(value, position);
    plan.payload.payload_digest = read_text(value, position);
    plan.payload.canonical_coverage = read_text(value, position);
    if(position != value.size()) {
        throw std::runtime_error("trailing publication plan metadata");
    }
    return plan;
}

std::string serialize_receipt(
    const PublicationPlan& plan,
    const PublicationSequence sequence,
    const PublicationReceiptStatus status
) {
    std::string value = serialize_plan(plan);
    append_u64(value, sequence.value());
    value.push_back(static_cast<char>(status));
    return value;
}

struct StoredReceipt final {
    PublicationPlan plan;
    PublicationSequence sequence;
    PublicationReceiptStatus status = PublicationReceiptStatus::MetadataOnly;
};

StoredReceipt deserialize_receipt(std::string_view value) {
    if(value.size() < 8U) {
        throw std::runtime_error("truncated publication receipt metadata");
    }
    if(value.size() >= 9U && (value.back() == 0 || value.back() == 1)) {
        try {
            const auto plan = deserialize_plan(value.substr(0, value.size() - 9U));
            std::size_t position = value.size() - 9U;
            const auto sequence = PublicationSequence{read_u64(value, position)};
            const auto status = static_cast<PublicationReceiptStatus>(
                static_cast<std::uint8_t>(value.back())
            );
            return StoredReceipt{plan, sequence, status};
        } catch(const std::exception&) {
            // Fall through for receipts written by the pre-status format.
        }
    }
    const auto plan = deserialize_plan(value.substr(0, value.size() - 8U));
    std::size_t position = value.size() - 8U;
    return StoredReceipt{
        plan,
        PublicationSequence{read_u64(value, position)},
        PublicationReceiptStatus::MetadataOnly
    };
}

bool same_plan(const PublicationPlan& left, const PublicationPlan& right) {
    return left.identity.scope_id == right.identity.scope_id
        && left.identity.publication_id == right.identity.publication_id
        && left.expected_frontier.scope_id == right.expected_frontier.scope_id
        && left.expected_frontier.sequence.value()
            == right.expected_frontier.sequence.value()
        && left.profile.scope_id == right.profile.scope_id
        && left.profile.profile_id == right.profile.profile_id
        && left.profile.profile_version == right.profile.profile_version
        && left.fencing.owner_id == right.fencing.owner_id
        && left.fencing.epoch == right.fencing.epoch
        && left.fencing.token_digest == right.fencing.token_digest
        && same_publication_evidence(left.expected_evidence, right.expected_evidence)
        && same_publication_evidence(left.target_evidence, right.target_evidence)
        && left.payload.payload_id == right.payload.payload_id
        && left.payload.payload_digest == right.payload.payload_digest
        && left.payload.canonical_coverage == right.payload.canonical_coverage;
}

bool same_profile(
    const PublicationProfileIdentity& left,
    const PublicationProfileIdentity& right
) {
    return left.scope_id == right.scope_id
        && left.profile_id == right.profile_id
        && left.profile_version == right.profile_version;
}

bool same_fencing(
    const PublicationFencingIdentity& left,
    const PublicationFencingIdentity& right
) {
    return left.owner_id == right.owner_id
        && left.epoch == right.epoch
        && left.token_digest == right.token_digest;
}

class LambdaParticipant final : public MdbxPublicationParticipant {
public:
    LambdaParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        std::function<void(const mdbxc::Transaction&)> operation
    ) : m_context(std::move(context)), m_operation(std::move(operation)) {}

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override {
        return *m_context;
    }

    void apply(const mdbxc::Transaction& transaction) override {
        m_operation(transaction);
    }

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    std::function<void(const mdbxc::Transaction&)> m_operation;
};

} // namespace

class MdbxPublicationCoordinator::Impl final {
public:
    static std::shared_ptr<MdbxStorageContext> require_context(
        std::shared_ptr<MdbxStorageContext> value
    ) {
        if(!value || !value->connection()) {
            throw std::invalid_argument("publication coordinator requires an MDBX context");
        }
        return value;
    }

    explicit Impl(MdbxPublicationCoordinatorOptions options)
        : context(std::move(options.context)),
          scope_id(std::move(options.scope_id)),
          records(require_context(context)->connection(), std::move(options.table_name)) {
        if(scope_id.empty()) {
            throw std::invalid_argument("publication coordinator requires context and scope");
        }
    }

    std::shared_ptr<MdbxStorageContext> context;
    ScopeId scope_id;
    mdbxc::KeyValueTable<std::string, std::string> records;

    [[nodiscard]] std::string key(std::string_view suffix) const {
        return scope_id.value() + "|" + std::string{suffix};
    }
};

MdbxPublicationCoordinator::MdbxPublicationCoordinator(
    MdbxPublicationCoordinatorOptions options
) : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxPublicationCoordinator::~MdbxPublicationCoordinator() = default;
MdbxPublicationCoordinator::MdbxPublicationCoordinator(
    MdbxPublicationCoordinator&&
) noexcept = default;
MdbxPublicationCoordinator& MdbxPublicationCoordinator::operator=(
    MdbxPublicationCoordinator&&
) noexcept = default;

const MdbxStorageContext& MdbxPublicationCoordinator::context() const noexcept {
    return *m_impl->context;
}

PublicationAdmissionResult MdbxPublicationCoordinator::admit_profile(
    PublicationProfileIdentity profile,
    PublicationFencingIdentity fencing
) {
    PublicationAdmissionResult result{PublicationAdmissionCode::InvalidProfile};
    auto scope = MdbxPublicationWriteScope::begin(m_impl->context);
    LambdaParticipant participant{
        m_impl->context,
        [this, profile = std::move(profile), fencing = std::move(fencing), &result](
            const mdbxc::Transaction& transaction
        ) mutable {
            const auto schema = m_impl->records.find(m_impl->key(kSchemaKey), transaction);
            if(schema && *schema != kSchemaValue) {
                result.code = PublicationAdmissionCode::InvalidModel;
                return;
            }
            if(!is_valid_publication_profile(profile)
                || profile.scope_id != m_impl->scope_id) {
                result.code = PublicationAdmissionCode::InvalidProfile;
                return;
            }
            if(!is_valid_publication_fencing(fencing)) {
                result.code = PublicationAdmissionCode::InvalidFencing;
                return;
            }
            const auto existing = m_impl->records.find(m_impl->key(kProfileKey), transaction);
            if(existing) {
                const auto current_fencing = m_impl->records.find(
                    m_impl->key(kFencingKey),
                    transaction
                );
                if(!current_fencing) {
                    result.code = PublicationAdmissionCode::InvalidModel;
                    return;
                }
                try {
                    const auto admitted = deserialize_profile(*existing);
                    const auto current = deserialize_fencing(*current_fencing);
                    if(!same_profile(admitted, profile)
                        || !same_fencing(current, fencing)) {
                        result.code = PublicationAdmissionCode::ProfileConflict;
                        return;
                    }
                } catch(const std::exception&) {
                    result.code = PublicationAdmissionCode::InvalidModel;
                    return;
                }
                result.code = PublicationAdmissionCode::AlreadyAdmitted;
                return;
            }
            m_impl->records.insert_or_assign(
                m_impl->key(kSchemaKey),
                kSchemaValue,
                transaction
            );
            m_impl->records.insert_or_assign(
                m_impl->key(kProfileKey),
                serialize_profile(profile),
                transaction
            );
            m_impl->records.insert_or_assign(
                m_impl->key(kFencingKey),
                serialize_fencing(fencing),
                transaction
            );
            std::string frontier;
            append_u64(frontier, 0);
            m_impl->records.insert_or_assign(
                m_impl->key(kFrontierKey),
                frontier,
                transaction
            );
            result.code = PublicationAdmissionCode::Admitted;
        }
    };
    try {
        scope.apply(participant);
    } catch(const std::exception&) {
        if(scope.active()) {
            scope.abort();
        }
        result.code = PublicationAdmissionCode::InvalidModel;
        return result;
    }
    if(result.code == PublicationAdmissionCode::Admitted) {
        scope.commit();
    } else {
        scope.abort();
    }
    return result;
}

PublicationAdmissionResult MdbxPublicationCoordinator::rotate_fencing(
    PublicationFencingIdentity fencing
) {
    PublicationAdmissionResult result{PublicationAdmissionCode::InvalidFencing};
    auto scope = MdbxPublicationWriteScope::begin(m_impl->context);
    LambdaParticipant participant{
        m_impl->context,
        [this, fencing = std::move(fencing), &result](
            const mdbxc::Transaction& transaction
        ) mutable {
            const auto current = m_impl->records.find(
                m_impl->key(kFencingKey),
                transaction
            );
            if(!current) {
                result.code = PublicationAdmissionCode::InvalidModel;
                return;
            }
            if(!is_valid_publication_fencing(fencing)) {
                result.code = PublicationAdmissionCode::InvalidFencing;
                return;
            }
            const auto admitted = deserialize_fencing(*current);
            if(admitted.owner_id == fencing.owner_id
                && admitted.epoch == fencing.epoch
                && admitted.token_digest == fencing.token_digest) {
                result.code = PublicationAdmissionCode::AlreadyAdmitted;
                return;
            }
            if(fencing.epoch <= admitted.epoch) {
                result.code = PublicationAdmissionCode::FencingConflict;
                return;
            }
            m_impl->records.insert_or_assign(
                m_impl->key(kFencingKey),
                serialize_fencing(fencing),
                transaction
            );
            result.code = PublicationAdmissionCode::Admitted;
        }
    };
    try {
        scope.apply(participant);
    } catch(const std::exception&) {
        if(scope.active()) {
            scope.abort();
        }
        result.code = PublicationAdmissionCode::InvalidModel;
        return result;
    }
    if(result.code == PublicationAdmissionCode::Admitted) {
        scope.commit();
    } else {
        scope.abort();
    }
    return result;
}

PublicationOutcome MdbxPublicationCoordinator::publish_metadata_only(
    const PublicationPlan& plan
) {
    return publish_impl(plan, {}, true);
}

PublicationOutcome MdbxPublicationCoordinator::publish(
    const PublicationPlan& plan,
    const std::vector<MdbxPublicationParticipant*>& participants
) {
    return publish_impl(plan, participants, false);
}

PublicationOutcome MdbxPublicationCoordinator::publish_impl(
    const PublicationPlan& plan,
    const std::vector<MdbxPublicationParticipant*>& participants,
    const bool allow_metadata_only
) {
    PublicationOutcome result{PublicationOutcomeCode::InvalidPlan, std::nullopt};
    for(const auto* participant : participants) {
        if(!participant) {
            result.code = PublicationOutcomeCode::InvalidPlan;
            return result;
        }
    }
    MdbxCanonicalPublicationParticipant* canonical = nullptr;
    MdbxKnowledgeUnitPublicationParticipant* unit = nullptr;
    MdbxProjectionPublicationParticipant* projection = nullptr;
    MdbxLexicalPublicationParticipant* lexical = nullptr;
    MdbxManifestPublicationParticipant* manifest = nullptr;
    if(allow_metadata_only) {
        if(!participants.empty()) {
            return result;
        }
    } else {
        for(auto* candidate : participants) {
            if(auto* typed = dynamic_cast<MdbxCanonicalPublicationParticipant*>(candidate)) {
                if(canonical) { return result; }
                canonical = typed;
            } else if(auto* typed = dynamic_cast<MdbxKnowledgeUnitPublicationParticipant*>(candidate)) {
                if(unit) { return result; }
                unit = typed;
            } else if(auto* typed = dynamic_cast<MdbxProjectionPublicationParticipant*>(candidate)) {
                if(projection) { return result; }
                projection = typed;
            } else if(auto* typed = dynamic_cast<MdbxLexicalPublicationParticipant*>(candidate)) {
                if(lexical) { return result; }
                lexical = typed;
            } else if(auto* typed = dynamic_cast<MdbxManifestPublicationParticipant*>(candidate)) {
                if(manifest) { return result; }
                manifest = typed;
            } else {
                return result;
            }
        }
        if(!canonical || !unit || !projection || !lexical || !manifest) {
            return result;
        }
        if(canonical->context().connection() != m_impl->context->connection()
            || unit->context().connection() != m_impl->context->connection()
            || projection->context().connection() != m_impl->context->connection()
            || lexical->context().connection() != m_impl->context->connection()
            || manifest->context().connection() != m_impl->context->connection()) {
            return result;
        }
    }
    auto scope = MdbxPublicationWriteScope::begin(m_impl->context);
    const auto reset_participants = [&participants]() noexcept {
        for(auto* candidate : participants) {
            if(candidate) {
                candidate->reset_after_abort();
            }
        }
    };
    LambdaParticipant participant{
        m_impl->context,
        [this, &plan, &result, &participants, &scope, allow_metadata_only,
         canonical, unit, projection, lexical, manifest](
            const mdbxc::Transaction& transaction
        ) {
            if(!is_valid_publication_plan(plan)
                || plan.identity.scope_id != m_impl->scope_id) {
                result.code = PublicationOutcomeCode::InvalidPlan;
                return;
            }
            const auto schema = m_impl->records.find(m_impl->key(kSchemaKey), transaction);
            const auto profile = m_impl->records.find(m_impl->key(kProfileKey), transaction);
            const auto fencing = m_impl->records.find(m_impl->key(kFencingKey), transaction);
            const auto frontier = m_impl->records.find(m_impl->key(kFrontierKey), transaction);
            if(!schema || *schema != kSchemaValue || !profile || !fencing || !frontier) {
                result.code = PublicationOutcomeCode::InvalidModel;
                return;
            }
            if(deserialize_profile(*profile).profile_id != plan.profile.profile_id
                || deserialize_profile(*profile).profile_version != plan.profile.profile_version) {
                result.code = PublicationOutcomeCode::ProfileMismatch;
                return;
            }
            const auto admitted_fencing = deserialize_fencing(*fencing);
            if(admitted_fencing.owner_id != plan.fencing.owner_id
                || admitted_fencing.epoch != plan.fencing.epoch
                || admitted_fencing.token_digest != plan.fencing.token_digest) {
                result.code = PublicationOutcomeCode::FencingMismatch;
                return;
            }
            const auto receipt_key = m_impl->key(
                std::string{"receipt:"} + plan.identity.publication_id
            );
            const auto stored = m_impl->records.find(receipt_key, transaction);
            if(stored) {
                const auto committed = deserialize_receipt(*stored);
                if(same_plan(plan, committed.plan)) {
                    result.code = PublicationOutcomeCode::Replayed;
                    result.receipt = PublicationReceipt{
                        plan.identity,
                        PublicationScopeFrontier{
                            m_impl->scope_id,
                            committed.sequence
                        },
                        plan.payload,
                        true,
                        committed.status,
                        committed.plan.target_evidence
                    };
                } else {
                    result.code = PublicationOutcomeCode::PublicationIdentityConflict;
                }
                return;
            }
            std::size_t position = 0;
            const auto current_sequence = read_u64(*frontier, position);
            if(position != frontier->size()) {
                result.code = PublicationOutcomeCode::InvalidModel;
                return;
            }
            if(current_sequence != plan.expected_frontier.sequence.value()) {
                result.code = PublicationOutcomeCode::StaleFrontier;
                return;
            }
            if(current_sequence == std::numeric_limits<std::uint64_t>::max()) {
                result.code = PublicationOutcomeCode::SequenceOverflow;
                return;
            }

            const auto binding_key = m_impl->key(
                std::string{"binding:"} + plan.target_evidence.resource_id.value()
            );
            const auto stored_binding = m_impl->records.find(binding_key, transaction);
            if(stored_binding) {
                try {
                    if(plan.expected_evidence.resource_id.empty()
                        || !same_publication_evidence(
                            plan.expected_evidence,
                            deserialize_evidence(*stored_binding)
                        )) {
                        result.code = PublicationOutcomeCode::BindingMismatch;
                        return;
                    }
                } catch(const std::exception&) {
                    result.code = PublicationOutcomeCode::InvalidModel;
                    return;
                }
            } else if(!plan.expected_evidence.resource_id.empty()) {
                result.code = PublicationOutcomeCode::BindingMismatch;
                return;
            }

            try {
                for(auto* participant : participants) {
                    scope.apply(*participant);
                }
            } catch(const std::exception&) {
                result.code = PublicationOutcomeCode::CommitFailure;
                return;
            }
            const bool complete_roles = !allow_metadata_only;
            if(complete_roles) {
                if(!canonical->validates(plan, transaction)
                    || !unit->validates(plan, transaction)
                    || !projection->validates(plan, transaction)
                    || !lexical->validates(plan, transaction)
                    || !manifest->validates(plan, transaction)) {
                    result.code = PublicationOutcomeCode::BindingMismatch;
                    return;
                }
            }
            const auto receipt_status = complete_roles
                ? PublicationReceiptStatus::Complete
                : PublicationReceiptStatus::MetadataOnly;
            const PublicationSequence next_sequence{current_sequence + 1};
            std::string frontier_value;
            append_u64(frontier_value, next_sequence.value());
            const auto serialized_receipt = serialize_receipt(
                plan, next_sequence, receipt_status
            );
            m_impl->records.insert_or_assign(
                receipt_key,
                serialized_receipt,
                transaction
            );
            m_impl->records.insert_or_assign(
                m_impl->key(kLatestReceiptKey),
                serialized_receipt,
                transaction
            );
            if(receipt_status == PublicationReceiptStatus::Complete) {
                m_impl->records.insert_or_assign(
                    binding_key,
                    serialize_evidence(plan.target_evidence),
                    transaction
                );
            }
            m_impl->records.insert_or_assign(
                m_impl->key(kFrontierKey),
                frontier_value,
                transaction
            );
            result.code = PublicationOutcomeCode::Committed;
            result.receipt = PublicationReceipt{
                plan.identity,
                PublicationScopeFrontier{m_impl->scope_id, next_sequence},
                plan.payload,
                false,
                receipt_status,
                plan.target_evidence
            };
        }
    };
    try {
        scope.apply(participant);
    } catch(const std::exception&) {
        if(scope.active()) {
            scope.abort();
        }
        reset_participants();
        result.code = PublicationOutcomeCode::InvalidModel;
        return result;
    }
    if(result.code == PublicationOutcomeCode::Committed) {
        try {
            scope.commit();
        } catch(const std::exception&) {
            reset_participants();
            result.code = PublicationOutcomeCode::CommitFailure;
        }
    } else {
        scope.abort();
        reset_participants();
    }
    return result;
}

PublicationScopeFrontier MdbxPublicationCoordinator::frontier() const {
    auto transaction = m_impl->context->connection()->transaction(
        mdbxc::TransactionMode::READ_ONLY
    );
    const auto result = frontier_in_transaction(transaction);
    transaction.commit();
    return result;
}

PublicationScopeFrontier MdbxPublicationCoordinator::frontier_in_transaction(
    const mdbxc::Transaction& transaction
) const {
    const auto value = m_impl->records.find(m_impl->key(kFrontierKey), transaction);
    if(!value) {
        return PublicationScopeFrontier{};
    }
    std::size_t position = 0;
    const PublicationSequence sequence{read_u64(*value, position)};
    if(position != value->size()) {
        throw std::runtime_error("invalid publication frontier metadata");
    }
    return PublicationScopeFrontier{m_impl->scope_id, sequence};
}

bool MdbxPublicationCoordinator::metadata_valid_in_transaction(
    const mdbxc::Transaction& transaction
) const noexcept {
    try {
        const auto schema = m_impl->records.find(m_impl->key(kSchemaKey), transaction);
        const auto profile = m_impl->records.find(m_impl->key(kProfileKey), transaction);
        const auto fencing = m_impl->records.find(m_impl->key(kFencingKey), transaction);
        if(!schema || *schema != kSchemaValue || !profile || !fencing) {
            return false;
        }
        const auto admitted_profile = deserialize_profile(*profile);
        const auto admitted_fencing = deserialize_fencing(*fencing);
        return admitted_profile.scope_id == m_impl->scope_id
            && is_valid_publication_profile(admitted_profile)
            && is_valid_publication_fencing(admitted_fencing);
    } catch(...) {
        return false;
    }
}

std::optional<PublicationReceipt> MdbxPublicationCoordinator::receipt_in_transaction(
    const std::string& publication_id,
    const mdbxc::Transaction& transaction
) const {
    const auto value = m_impl->records.find(
        m_impl->key(std::string{"receipt:"} + publication_id), transaction
    );
    if(!value) {
        return std::nullopt;
    }
    const auto stored = deserialize_receipt(*value);
    return PublicationReceipt{
        stored.plan.identity,
        PublicationScopeFrontier{m_impl->scope_id, stored.sequence},
        stored.plan.payload,
        false,
        stored.status,
        stored.plan.target_evidence
    };
}

std::optional<PublicationReceipt>
MdbxPublicationCoordinator::latest_receipt_in_transaction(
    const mdbxc::Transaction& transaction
) const {
    const auto value = m_impl->records.find(m_impl->key(kLatestReceiptKey), transaction);
    if(!value) {
        return std::nullopt;
    }
    const auto stored = deserialize_receipt(*value);
    return PublicationReceipt{
        stored.plan.identity,
        PublicationScopeFrontier{m_impl->scope_id, stored.sequence},
        stored.plan.payload,
        false,
        stored.status,
        stored.plan.target_evidence
    };
}

std::optional<PublicationEvidence> MdbxPublicationCoordinator::binding_in_transaction(
    const ResourceId& resource_id,
    const mdbxc::Transaction& transaction
) const {
    const auto value = m_impl->records.find(
        m_impl->key(std::string{"binding:"} + resource_id.value()), transaction
    );
    return value
        ? std::optional<PublicationEvidence>{deserialize_evidence(*value)}
        : std::nullopt;
}

std::optional<PublicationReceipt> MdbxPublicationCoordinator::receipt(
    const std::string& publication_id
) const {
    auto transaction = m_impl->context->connection()->transaction(
        mdbxc::TransactionMode::READ_ONLY
    );
    const auto result = receipt_in_transaction(publication_id, transaction);
    transaction.commit();
    return result;
}

} // namespace agent_memory

#endif
