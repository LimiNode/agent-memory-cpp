#include "OriginalProjection.hpp"

#include <stdexcept>
#include <utility>

namespace agent_memory {

SearchProjection make_original_projection(
    const KnowledgeUnitEnvelope& envelope,
    std::uint64_t projection_revision,
    std::uint64_t derivation_generation,
    std::string derivation_fingerprint
) {
    if(envelope.kind != KnowledgeUnitKind::Chunk
       || envelope.lifecycle_state == KnowledgeUnitLifecycleState::Erased
       || envelope.id.empty()
       || envelope.scope_id.empty()
       || envelope.revision == 0
       || envelope.primary_text.empty()
       || projection_revision == 0
       || derivation_generation == 0
       || derivation_fingerprint.empty()) {
        throw std::invalid_argument("invalid envelope for Original projection");
    }
    SearchProjection projection;
    projection.scope_id = envelope.scope_id;
    projection.unit_id = envelope.id;
    projection.kind = ProjectionKind::Original;
    projection.version = ProjectionVersionRef{
        envelope.revision,
        projection_revision,
        derivation_generation,
        std::move(derivation_fingerprint)
    };
    projection.body = envelope.primary_text;
    return projection;
}

} // namespace agent_memory
