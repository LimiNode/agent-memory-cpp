#include "DerivedPublicationAdmission.hpp"

#include <iomanip>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::size_t kMaxAdmissionInputs = 4'096;
constexpr std::size_t kMaxAdmissionFrontierBytes = 1U * 1024U * 1024U;
constexpr std::string_view kUnitSnapshotEncoding = "ku-local-v1|";

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

bool append_bounded(std::string& output, std::string_view value) {
    if(output.size() > kMaxAdmissionFrontierBytes ||
       value.size() > kMaxAdmissionFrontierBytes - output.size())
        return false;
    output.append(value.data(), value.size());
    return true;
}

bool append_field(
    std::string& output,
    std::string_view name,
    std::string_view value
) {
    const auto size = std::to_string(value.size());
    return append_bounded(output, name) && append_bounded(output, "=") &&
        append_bounded(output, size) && append_bounded(output, ":") &&
        append_bounded(output, value) && append_bounded(output, "|");
}

DerivedPublicationAdmissionStatus map_frontier_status(
    DerivedPublicationUnitSnapshotStatus status
) {
    switch(status) {
    case DerivedPublicationUnitSnapshotStatus::MissingInput:
        return DerivedPublicationAdmissionStatus::MissingInput;
    case DerivedPublicationUnitSnapshotStatus::ScopeMismatch:
        return DerivedPublicationAdmissionStatus::ScopeMismatch;
    case DerivedPublicationUnitSnapshotStatus::InactiveInput:
        return DerivedPublicationAdmissionStatus::InactiveInput;
    case DerivedPublicationUnitSnapshotStatus::InvalidInput:
        return DerivedPublicationAdmissionStatus::InvalidInput;
    case DerivedPublicationUnitSnapshotStatus::Available:
        break;
    case DerivedPublicationUnitSnapshotStatus::Unavailable:
        return DerivedPublicationAdmissionStatus::Unavailable;
    }
    return DerivedPublicationAdmissionStatus::Unavailable;
}

DerivedPublicationAdmissionStatus map_read_status(DerivedPublicationReadStatus status) {
    switch(status) {
    case DerivedPublicationReadStatus::StoredMatch:
        return DerivedPublicationAdmissionStatus::KnowledgeUnitLocalMatch;
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

IDerivedPublicationUnitSnapshotProvider::~IDerivedPublicationUnitSnapshotProvider() = default;

KnowledgeUnitDerivedPublicationSnapshotProvider::KnowledgeUnitDerivedPublicationSnapshotProvider(
    const IKnowledgeUnitStore& units
)
    : m_units(&units) {}

DerivedPublicationUnitSnapshotResult KnowledgeUnitDerivedPublicationSnapshotProvider::snapshot(
    const ScopeId& scope_id,
    const std::vector<KnowledgeUnitId>& unit_ids
) const {
    if(scope_id.empty() || unit_ids.empty() || unit_ids.size() > kMaxAdmissionInputs)
        return {DerivedPublicationUnitSnapshotStatus::InvalidInput, std::nullopt};

    DerivedPublicationUnitSnapshot snapshot;
    snapshot.inputs.reserve(unit_ids.size());
    snapshot.dependency_frontier.reserve(kUnitSnapshotEncoding.size());
    if(!append_bounded(snapshot.dependency_frontier, kUnitSnapshotEncoding))
        return {DerivedPublicationUnitSnapshotStatus::InvalidInput, std::nullopt};
    std::set<std::uint64_t> seen_ids;
    for(const auto id : unit_ids) {
        if(id.empty() || !seen_ids.insert(id.value()).second)
            return {DerivedPublicationUnitSnapshotStatus::InvalidInput, std::nullopt};
        std::optional<KnowledgeUnitEnvelope> envelope;
        try {
            envelope = m_units->find(id);
        } catch(const std::exception&) {
            return {DerivedPublicationUnitSnapshotStatus::Unavailable, std::nullopt};
        }
        if(!envelope)
            return {DerivedPublicationUnitSnapshotStatus::MissingInput, std::nullopt};
        if(envelope->scope_id != scope_id)
            return {DerivedPublicationUnitSnapshotStatus::ScopeMismatch, std::nullopt};
        if(envelope->lifecycle_state != KnowledgeUnitLifecycleState::Active)
            return {DerivedPublicationUnitSnapshotStatus::InactiveInput, std::nullopt};
        if(envelope->revision == 0 || envelope->sources.size() != 1 ||
           envelope->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
           !envelope->sources.front().resource_revision ||
           envelope->sources.front().resource_revision->generation == 0 ||
           envelope->sources.front().resource_revision->body_digest.algorithm !=
               ResourceBodyDigestAlgorithm::Sha256)
            return {DerivedPublicationUnitSnapshotStatus::InvalidInput, std::nullopt};

        const auto& source = envelope->sources.front();
        const auto& revision = *source.resource_revision;
        const auto digest = hex_digest(revision.body_digest);
        snapshot.inputs.push_back({id, envelope->revision, revision.generation, digest});
        const auto unit_id = std::to_string(id.value());
        const auto unit_revision = std::to_string(envelope->revision);
        const auto source_generation = std::to_string(revision.generation);
        if(!append_field(snapshot.dependency_frontier, "unit_id", unit_id) ||
           !append_field(snapshot.dependency_frontier, "unit_revision", unit_revision) ||
           !append_field(snapshot.dependency_frontier, "resource_id", revision.resource_id.value()) ||
           !append_field(snapshot.dependency_frontier, "source_generation", source_generation) ||
           !append_field(snapshot.dependency_frontier, "source_digest", digest))
            return {DerivedPublicationUnitSnapshotStatus::InvalidInput, std::nullopt};
    }
    return {DerivedPublicationUnitSnapshotStatus::Available, std::move(snapshot)};
}

DerivedPublicationAdmission::DerivedPublicationAdmission(
    IDerivedPublicationStore& publications,
    const IDerivedPublicationUnitSnapshotProvider& unit_snapshot_provider
)
    : m_publications(&publications), m_unit_snapshot_provider(&unit_snapshot_provider) {}

DerivedPublicationAdmissionResult DerivedPublicationAdmission::publish(
    DerivedPublicationRequest request
) {
    std::vector<KnowledgeUnitId> ids;
    ids.reserve(request.inputs.size());
    for(const auto& input : request.inputs) ids.push_back(input.unit_id);
    const auto frontier = m_unit_snapshot_provider->snapshot(request.scope_id, ids);
    if(frontier.status != DerivedPublicationUnitSnapshotStatus::Available)
        return {map_frontier_status(frontier.status), std::nullopt};
    if(!frontier.snapshot)
        return {DerivedPublicationAdmissionStatus::Unavailable, std::nullopt};
    if(!same_inputs(request.inputs, frontier.snapshot->inputs) ||
       request.dependency_frontier != frontier.snapshot->dependency_frontier)
        return {DerivedPublicationAdmissionStatus::Rejected, std::nullopt};
    try {
        m_publications->publish(std::move(request));
        return {DerivedPublicationAdmissionStatus::PublishedAfterUnitCheck, std::nullopt};
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
    const auto frontier = m_unit_snapshot_provider->snapshot(access_scope, ids);
    if(frontier.status != DerivedPublicationUnitSnapshotStatus::Available)
        return {map_frontier_status(frontier.status), std::nullopt};
    if(!frontier.snapshot)
        return {DerivedPublicationAdmissionStatus::Unavailable, std::nullopt};
    if(!same_inputs(current_inputs, frontier.snapshot->inputs) ||
       current_dependency_frontier != frontier.snapshot->dependency_frontier)
        return {DerivedPublicationAdmissionStatus::StoredMismatch, std::nullopt};
    const auto result = m_publications->read(
        access_scope, publication_id, frontier.snapshot->inputs,
        frontier.snapshot->dependency_frontier);
    return {map_read_status(result.status), std::move(result.publication)};
}

} // namespace agent_memory
