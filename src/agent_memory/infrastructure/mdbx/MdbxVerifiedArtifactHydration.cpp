#include "MdbxVerifiedArtifactHydration.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <utility>

namespace agent_memory {
namespace {

VerifiedArtifactMaterializationResult fail(
    VerifiedArtifactMaterializationStatus status,
    const VerifiedArtifactMaterializationRequest& request,
    std::string diagnostic) {
    VerifiedArtifactMaterializationResult result;
    result.status = status;
    result.source_revision_id = request.source_revision_id;
    result.binding_id = request.binding_id;
    result.offset = request.offset;
    result.diagnostic = std::move(diagnostic);
    return result;
}

VerifiedArtifactMaterializationStatus map_catalog(ArtifactCatalogStatus status) noexcept {
    switch(status) {
    case ArtifactCatalogStatus::NotFound: return VerifiedArtifactMaterializationStatus::Missing;
    case ArtifactCatalogStatus::Corrupt: return VerifiedArtifactMaterializationStatus::Corrupt;
    case ArtifactCatalogStatus::InvalidArgument: return VerifiedArtifactMaterializationStatus::InvalidArgument;
    case ArtifactCatalogStatus::VerificationBudgetExceeded:
        return VerifiedArtifactMaterializationStatus::VerificationBudgetExceeded;
    case ArtifactCatalogStatus::Conflict: return VerifiedArtifactMaterializationStatus::Unsupported;
    case ArtifactCatalogStatus::IoError: return VerifiedArtifactMaterializationStatus::Unavailable;
    case ArtifactCatalogStatus::Ok: return VerifiedArtifactMaterializationStatus::Corrupt;
    }
    return VerifiedArtifactMaterializationStatus::Unavailable;
}

} // namespace

VerifiedArtifactMaterializationResult MdbxArtifactMaterializer::materialize(
    const VerifiedArtifactMaterializationRequest& request) const {
    if(request.source_revision_id.empty() || request.binding_id.empty())
        return fail(VerifiedArtifactMaterializationStatus::InvalidArgument, request,
                    "materialization identity is incomplete");

    const auto catalog = m_catalog.read_binding_metadata(request.binding_id);
    if(catalog.status != ArtifactCatalogStatus::Ok || !catalog.record)
        return fail(map_catalog(catalog.status), request, catalog.diagnostic);
    const auto& record = *catalog.record;
    if(record.binding.source_revision_id != request.source_revision_id)
        return fail(VerifiedArtifactMaterializationStatus::BindingRevisionMismatch, request,
                    "binding is attached to another source revision; this is not a currentness decision");
    if(request.expected_artifact_id && record.artifact.id != *request.expected_artifact_id)
        return fail(VerifiedArtifactMaterializationStatus::Corrupt, request,
                    "artifact identity mismatch");
    if(request.expected_digest &&
       (record.artifact.digest.algorithm != request.expected_digest->algorithm ||
        record.artifact.digest.value != request.expected_digest->value))
        return fail(VerifiedArtifactMaterializationStatus::Corrupt, request,
                    "artifact digest mismatch");
    if(record.body_state != BindingBodyState::Materialized || !record.receipt_finalized) {
        return fail(VerifiedArtifactMaterializationStatus::Missing, request,
                    "retained body is unavailable; no summary evidence is present");
    }
    const auto body = m_catalog.read_body_range(
        request.binding_id, request.offset, request.length,
        request.verification_budget_bytes);
    if(body.status != ArtifactCatalogStatus::Ok)
        return fail(map_catalog(body.status), request, body.diagnostic);
    VerifiedArtifactMaterializationResult result;
    result.status = VerifiedArtifactMaterializationStatus::Materialized;
    result.source_revision_id = request.source_revision_id;
    result.binding_id = request.binding_id;
    result.artifact_id = record.artifact.id;
    result.offset = request.offset;
    result.bytes = body.bytes;
    return result;
}

} // namespace agent_memory

#endif
