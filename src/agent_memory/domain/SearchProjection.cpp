#include "SearchProjection.hpp"

namespace agent_memory {

bool is_valid_projection_kind(ProjectionKind kind) noexcept {
    switch(kind) {
        case ProjectionKind::Original:
        case ProjectionKind::DenseContextual:
        case ProjectionKind::QAQuestion:
        case ProjectionKind::QAAnswer:
        case ProjectionKind::Summary:
        case ProjectionKind::CodeSymbols:
        case ProjectionKind::TranslatedCanonical:
        case ProjectionKind::QACombined:
            return true;
    }
    return false;
}

bool is_valid_projection_version(const ProjectionVersionRef& version) noexcept {
    return version.unit_revision_at_build != 0
        && version.projection_revision != 0
        && version.derivation_generation != 0
        && !version.derivation_fingerprint.empty();
}

bool is_valid_search_projection(const SearchProjection& projection) noexcept {
    return !projection.scope_id.empty()
        && !projection.unit_id.empty()
        && is_valid_projection_kind(projection.kind)
        && is_valid_projection_version(projection.version)
        && (!projection.body.empty() || !projection.title.empty()
            || !projection.heading.empty() || !projection.code.empty()
            || !projection.tags.empty() || !projection.symbols.empty());
}

} // namespace agent_memory
