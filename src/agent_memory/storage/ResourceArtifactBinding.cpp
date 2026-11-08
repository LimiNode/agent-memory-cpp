#include "ResourceArtifactBinding.hpp"

namespace agent_memory {

bool is_valid_resource_artifact_binding(
    const ResourceArtifactBinding& binding
) noexcept {
    return !binding.resource_revision.resource_id.empty()
        && binding.resource_revision.generation != 0
        && is_valid_resource_body_digest(binding.resource_revision.body_digest)
        && !binding.source_revision_id.empty()
        && !binding.artifact_binding_id.empty();
}

bool operator==(
    const ResourceArtifactBinding& left,
    const ResourceArtifactBinding& right
) noexcept {
    return left.resource_revision.resource_id ==
               right.resource_revision.resource_id
        && left.resource_revision.generation ==
               right.resource_revision.generation
        && left.resource_revision.body_digest.algorithm ==
               right.resource_revision.body_digest.algorithm
        && left.resource_revision.body_digest.bytes ==
               right.resource_revision.body_digest.bytes
        && left.source_revision_id == right.source_revision_id
        && left.artifact_binding_id == right.artifact_binding_id;
}

} // namespace agent_memory
