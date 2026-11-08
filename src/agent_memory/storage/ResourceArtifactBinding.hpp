#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_RESOURCE_ARTIFACT_BINDING_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_RESOURCE_ARTIFACT_BINDING_HPP_INCLUDED

/// \file ResourceArtifactBinding.hpp
/// \brief Typed join between a resource revision and retained artifact identity.

#include <agent_memory/domain/Artifact.hpp>
#include <agent_memory/domain/KnowledgeUnit.hpp>

namespace agent_memory {

/// \brief Durable mapping owned by the resource publication profile.
///
/// Resource manifests remain authoritative for the active resource revision,
/// while ArtifactCatalog remains authoritative for artifact bytes and binding
/// state. This value is only the typed join between those authorities; it does
/// not duplicate either authority or infer one identity from another.
struct ResourceArtifactBinding final {
    ResourceRevisionRef resource_revision;
    SourceRevisionId source_revision_id;
    ArtifactBindingId artifact_binding_id;
};

[[nodiscard]] bool is_valid_resource_artifact_binding(
    const ResourceArtifactBinding& binding
) noexcept;

[[nodiscard]] bool operator==(
    const ResourceArtifactBinding& left,
    const ResourceArtifactBinding& right
) noexcept;

} // namespace agent_memory

#endif
