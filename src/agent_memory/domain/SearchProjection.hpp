#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_SEARCH_PROJECTION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_SEARCH_PROJECTION_HPP_INCLUDED

/// \file SearchProjection.hpp
/// \brief Revision-aware derived text projection values.

#include "KnowledgeUnit.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Stable projection family discriminator.
    enum class ProjectionKind : std::uint16_t {
        Original = 1,
        DenseContextual = 2,
        QAQuestion = 3,
        QAAnswer = 4,
        Summary = 5,
        CodeSymbols = 6,
        TranslatedCanonical = 7,
        QACombined = 8
    };

    /// \brief Complete freshness identity of one derived projection.
    struct ProjectionVersionRef final {
        std::uint64_t unit_revision_at_build = 0;
        std::uint64_t projection_revision = 0;
        std::uint64_t derivation_generation = 0;
        std::string derivation_fingerprint;
    };

    /// \brief Text view consumed by a retrieval projection or lexical index.
    ///
    /// The M0 Original publisher populates only `body` from the bounded
    /// envelope seed. Full locator-backed ChunkPayload hydration remains a
    /// separate capability and is never implied by this value.
    struct SearchProjection final {
        ScopeId scope_id;
        KnowledgeUnitId unit_id;
        ProjectionKind kind = ProjectionKind::Original;
        ProjectionVersionRef version;
        std::string title;
        std::string heading;
        std::string body;
        std::string code;
        std::vector<std::string> tags;
        std::vector<std::string> symbols;
    };

    [[nodiscard]] bool is_valid_projection_kind(ProjectionKind kind) noexcept;
    [[nodiscard]] bool is_valid_projection_version(
        const ProjectionVersionRef& version
    ) noexcept;
    [[nodiscard]] bool is_valid_search_projection(
        const SearchProjection& projection
    ) noexcept;

} // namespace agent_memory

#endif
