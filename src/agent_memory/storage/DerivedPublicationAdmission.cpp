#include "DerivedPublicationAdmission.hpp"

#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::size_t kMaxAdmissionInputs = 1'000'000;
constexpr std::size_t kMaxAdmissionFrontierBytes = 1U * 1024U * 1024U;

std::string hex_digest(const ResourceBodyDigest& digest) {
    std::ostringstream stream;
    stream << std::hex << std::setfill('0');
    for(const auto byte : digest.bytes)
        stream << std::setw(2) << static_cast<unsigned int>(byte);
    return stream.str();
}

bool same_inputs(
    const std::vector<DerivedPublicationInput>& left,
    const std::vector<DerivedPublicationInput>& right
) {
    if(left.size() != right.size()) return false;
    for(std::size_t index = 0; index < left.size(); ++index) {
        const auto& a = left[index];
        const auto& b = right[index];
        if(a.unit_id != b.unit_id || a.unit_revision != b.unit_revision ||
           a.source_generation != b.source_generation || a.source_digest != b.source_digest)
            return false;
    }
    return true;
}

DerivedPublicationAdmissionStatus map_frontier_status(
    DerivedPublicationFrontierStatus status
) {
    switch(status) {
    case DerivedPublicationFrontierStatus::MissingInput:
        return DerivedPublicationAdmissionStatus::MissingInput;
    case DerivedPublicationFrontierStatus::ScopeMismatch:
        return DerivedPublicationAdmissionStatus::ScopeMismatch;
    case DerivedPublicationFrontierStatus::InactiveInput:
        return DerivedPublicationAdmissionStatus::InactiveInput;
    case DerivedPublicationFrontierStatus::Available:
        break;
    case DerivedPublicationFrontierStatus::InvalidInput:
    case DerivedPublicationFrontierStatus::Unavailable:
        return DerivedPublicationAdmissionStatus::Unavailable;
    }
    return DerivedPublicationAdmissionStatus::Unavailable;
}

DerivedPublicationAdmissionStatus map_read_status(DerivedPublicationReadStatus status) {
    switch(status) {
    case DerivedPublicationReadStatus::StoredMatch:
        return DerivedPublicationAdmissionStatus::StoredMatch;
    case DerivedPublicationReadStatus::StoredMismatch:
        return DerivedPublicationAdmissionStatus::StoredMismatch;
    case DerivedPublicationReadStatus::NotFound:
        return DerivedPublicationAdmissionStatus::NotFound;
    case DerivedPublicationReadStatus::ScopeMismatch:
        return DerivedPublicationAdmissionStatus::ScopeMismatch;
    case DerivedPublicationReadStatus::Unavailable:
        return DerivedPublicationAdmissionStatus::Unavailable;
    }
    return DerivedPublicationAdmissionStatus::Unavailable;
}

} // namespace

IDerivedPublicationFrontierProvider::~IDerivedPublicationFrontierProvider() = default;

KnowledgeUnitDerivedPublicationFrontierProvider::KnowledgeUnitDerivedPublicationFrontierProvider(
    const IKnowledgeUnitStore& units
)
    : m_units(&units) {}

DerivedPublicationFrontierResult KnowledgeUnitDerivedPublicationFrontierProvider::snapshot(
    const ScopeId& scope_id,
    const std::vector<KnowledgeUnitId>& unit_ids
) const {
    if(scope_id.empty() || unit_ids.empty() || unit_ids.size() > kMaxAdmissionInputs)
        return {DerivedPublicationFrontierStatus::InvalidInput, std::nullopt};

    DerivedPublicationFrontierSnapshot snapshot;
    snapshot.inputs.reserve(unit_ids.size());
    for(const auto id : unit_ids) {
        if(id.empty()) return {DerivedPublicationFrontierStatus::InvalidInput, std::nullopt};
        const auto envelope = m_units->find(id);
        if(!envelope) return {DerivedPublicationFrontierStatus::MissingInput, std::nullopt};
        if(envelope->scope_id != scope_id)
            return {DerivedPublicationFrontierStatus::ScopeMismatch, std::nullopt};
        if(envelope->lifecycle_state != KnowledgeUnitLifecycleState::Active)
            return {DerivedPublicationFrontierStatus::InactiveInput, std::nullopt};
        if(envelope->revision == 0 || envelope->sources.size() != 1 ||
           envelope->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
           !envelope->sources.front().resource_revision ||
           envelope->sources.front().resource_revision->generation == 0 ||
           envelope->sources.front().resource_revision->body_digest.algorithm !=
               ResourceBodyDigestAlgorithm::Sha256)
            return {DerivedPublicationFrontierStatus::InvalidInput, std::nullopt};

        const auto& source = envelope->sources.front();
        const auto& revision = *source.resource_revision;
        const auto digest = hex_digest(revision.body_digest);
        snapshot.inputs.push_back({id, envelope->revision, revision.generation, digest});
        const auto entry = "unit:" + std::to_string(id.value()) +
            "@" + std::to_string(envelope->revision) +
            ";source:" + revision.resource_id.value() +
            "@" + std::to_string(revision.generation) + ":" + digest + ";";
        if(entry.size() > kMaxAdmissionFrontierBytes - snapshot.dependency_frontier.size())
            return {DerivedPublicationFrontierStatus::InvalidInput, std::nullopt};
        snapshot.dependency_frontier += entry;
    }
    return {DerivedPublicationFrontierStatus::Available, std::move(snapshot)};
}

DerivedPublicationAdmission::DerivedPublicationAdmission(
    IDerivedPublicationStore& publications,
    const IDerivedPublicationFrontierProvider& frontier_provider
)
    : m_publications(&publications), m_frontier_provider(&frontier_provider) {}

DerivedPublicationAdmissionResult DerivedPublicationAdmission::publish(
    DerivedPublicationRequest request
) {
    std::vector<KnowledgeUnitId> ids;
    ids.reserve(request.inputs.size());
    for(const auto& input : request.inputs) ids.push_back(input.unit_id);
    const auto frontier = m_frontier_provider->snapshot(request.scope_id, ids);
    if(frontier.status != DerivedPublicationFrontierStatus::Available)
        return {map_frontier_status(frontier.status), std::nullopt};
    if(!frontier.snapshot || !same_inputs(request.inputs, frontier.snapshot->inputs) ||
       request.dependency_frontier != frontier.snapshot->dependency_frontier)
        return {DerivedPublicationAdmissionStatus::Rejected, std::nullopt};
    try {
        m_publications->publish(std::move(request));
        return {DerivedPublicationAdmissionStatus::Published, std::nullopt};
    } catch(const std::invalid_argument&) {
        return {DerivedPublicationAdmissionStatus::Rejected, std::nullopt};
    } catch(const std::exception&) {
        return {DerivedPublicationAdmissionStatus::Unavailable, std::nullopt};
    }
}

DerivedPublicationAdmissionResult DerivedPublicationAdmission::read(
    const ScopeId& access_scope,
    const std::string& publication_id,
    const std::vector<DerivedPublicationInput>& current_inputs,
    const std::string& current_dependency_frontier
) const {
    std::vector<KnowledgeUnitId> ids;
    ids.reserve(current_inputs.size());
    for(const auto& input : current_inputs) ids.push_back(input.unit_id);
    const auto frontier = m_frontier_provider->snapshot(access_scope, ids);
    if(frontier.status != DerivedPublicationFrontierStatus::Available)
        return {map_frontier_status(frontier.status), std::nullopt};
    if(!frontier.snapshot || !same_inputs(current_inputs, frontier.snapshot->inputs) ||
       current_dependency_frontier != frontier.snapshot->dependency_frontier)
        return {DerivedPublicationAdmissionStatus::StoredMismatch, std::nullopt};
    const auto result = m_publications->read(
        access_scope, publication_id, frontier.snapshot->inputs,
        frontier.snapshot->dependency_frontier);
    return {map_read_status(result.status), std::move(result.publication)};
}

} // namespace agent_memory
