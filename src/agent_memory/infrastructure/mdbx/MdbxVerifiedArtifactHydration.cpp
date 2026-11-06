#include "MdbxVerifiedArtifactHydration.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <utility>

namespace agent_memory {
namespace {

VerifiedHydrationResult fail(
    VerifiedHydrationStatus status,
    const VerifiedHydrationRequest& request,
    std::string diagnostic) {
    VerifiedHydrationResult result;
    result.status = status;
    result.source_revision_id = request.source_revision_id;
    result.binding_id = request.binding_id;
    result.offset = request.offset;
    result.diagnostic = std::move(diagnostic);
    return result;
}

VerifiedHydrationStatus map_catalog(ArtifactCatalogStatus status) noexcept {
    switch(status) {
    case ArtifactCatalogStatus::NotFound: return VerifiedHydrationStatus::Missing;
    case ArtifactCatalogStatus::Corrupt: return VerifiedHydrationStatus::Corrupt;
    case ArtifactCatalogStatus::InvalidArgument: return VerifiedHydrationStatus::InvalidArgument;
    case ArtifactCatalogStatus::Conflict: return VerifiedHydrationStatus::Unsupported;
    case ArtifactCatalogStatus::IoError: return VerifiedHydrationStatus::Unavailable;
    case ArtifactCatalogStatus::Ok: return VerifiedHydrationStatus::Hydrated;
    }
    return VerifiedHydrationStatus::Missing;
}

} // namespace

VerifiedHydrationResult MdbxVerifiedArtifactHydration::hydrate(
    const VerifiedHydrationRequest& request) const {
    if(request.source_revision_id.empty() || request.binding_id.empty() ||
       request.expected_scope.empty() || request.binding_scope.empty() ||
       request.expected_scope != request.binding_scope)
        return fail(VerifiedHydrationStatus::WrongScope, request, "hydration scope is missing or mismatched");
    if(request.current_source_revision_id &&
       *request.current_source_revision_id != request.source_revision_id)
        return fail(VerifiedHydrationStatus::Stale, request, "requested source revision is not current");

    const auto catalog = m_catalog.read_binding(request.binding_id);
    if(catalog.status != ArtifactCatalogStatus::Ok || !catalog.record)
        return fail(map_catalog(catalog.status), request, catalog.diagnostic);
    const auto& record = *catalog.record;
    if(record.binding.source_revision_id != request.source_revision_id)
        return fail(VerifiedHydrationStatus::Stale, request, "binding is attached to another source revision");
    if(request.expected_artifact_id && record.artifact.id != *request.expected_artifact_id)
        return fail(VerifiedHydrationStatus::Corrupt, request, "artifact identity mismatch");
    if(request.expected_digest &&
       (record.artifact.digest.algorithm != request.expected_digest->algorithm ||
        record.artifact.digest.value != request.expected_digest->value))
        return fail(VerifiedHydrationStatus::Corrupt, request, "artifact digest mismatch");
    if(record.body_state != BindingBodyState::Materialized || !record.receipt_finalized) {
        return fail(request.allow_summary_only ? VerifiedHydrationStatus::SummaryOnly
                                               : VerifiedHydrationStatus::Missing,
                    request, "verified retained body is unavailable");
    }
    const auto body = m_catalog.read_body_range(
        request.binding_id, request.offset, request.length);
    if(body.status != ArtifactCatalogStatus::Ok)
        return fail(map_catalog(body.status), request, body.diagnostic);
    VerifiedHydrationResult result;
    result.status = VerifiedHydrationStatus::Hydrated;
    result.source_revision_id = request.source_revision_id;
    result.binding_id = request.binding_id;
    result.artifact_id = record.artifact.id;
    result.offset = request.offset;
    result.bytes = body.bytes;
    return result;
}

} // namespace agent_memory

#endif
