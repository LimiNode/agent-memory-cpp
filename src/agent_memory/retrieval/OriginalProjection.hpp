#pragma once
#ifndef AGENT_MEMORY_HEADER_RETRIEVAL_ORIGINAL_PROJECTION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RETRIEVAL_ORIGINAL_PROJECTION_HPP_INCLUDED

/// \file OriginalProjection.hpp
/// \brief M0 revision-aware Original projection publisher.

#include <agent_memory/domain/SearchProjection.hpp>

#include <cstdint>
#include <string>

namespace agent_memory {

    /// Builds the bounded Original projection from one persisted envelope.
    ///
    /// The resulting freshness token binds the envelope revision, projection
    /// revision and derivation identity. It is a derived view, not canonical
    /// source storage; callers must hydrate the envelope before returning hits.
    [[nodiscard]] SearchProjection make_original_projection(
        const KnowledgeUnitEnvelope& envelope,
        std::uint64_t projection_revision,
        std::uint64_t derivation_generation = 1,
        std::string derivation_fingerprint = "m0.original.primary_text.v1"
    );

} // namespace agent_memory

#endif
