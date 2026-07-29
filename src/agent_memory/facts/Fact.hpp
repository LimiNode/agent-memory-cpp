#pragma once
#ifndef AGENT_MEMORY_HEADER_FACTS_FACT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_FACTS_FACT_HPP_INCLUDED

/// \file Fact.hpp
/// \brief Forward-looking structured-fact value types.
///
/// Structured facts let a retriever answer precise questions ("the inflation
/// rate in 2023 was 3.4%") without surfacing whole chunks. This header
/// reserves the value types; runtime extraction will be added later.

#include <agent_memory/domain/Metadata.hpp>
#include <agent_memory/domain/Resource.hpp>
#include <agent_memory/memory/MemoryObject.hpp>

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Stable identifier for a structured fact.
    class FactId final {
    public:
        FactId() = default;
        explicit FactId(std::string value);

        /// \brief Returns identifier text.
        [[nodiscard]] const std::string& value() const noexcept;

        /// \brief Checks whether the identifier has no value.
        [[nodiscard]] bool empty() const noexcept;

    private:
        std::string m_value;
    };

    /// \brief Legacy fact-local provenance pointer.
    ///
    /// This lightweight record intentionally does not model a source revision,
    /// representation, or evidence anchor. The planned canonical `SourceRef`
    /// contract is owned by the knowledge-unit/provenance domain.
    struct FactSourceRef final {
        ResourceId resource_id;
        MemoryObjectId object_id;
        /// \brief Byte/char offset of the supporting span (0 when unknown).
        std::uint64_t offset = 0;
        /// \brief Length of the supporting span (0 when unknown).
        std::uint64_t length = 0;
    };

    /// \brief Structured fact extracted from a chunk or memory object.
    ///
    /// Designed to support both free-form and tabular facts:
    /// - Free-form: `subject` is a natural-language phrase, `metric` and
    ///   `period` may be empty.
    /// - Tabular: `subject` is an entity name, `metric` is the column name,
    ///   `period` is the row key (e.g. "2023-Q4"), `value` is the cell value.
    struct Fact final {
        FactId id;
        std::string subject;
        std::string metric;
        std::string value;
        std::string period;
        FactSourceRef source;
        /// \brief Confidence in [0.0, 1.0]. Empty when not modelled.
        std::optional<float> confidence;
        Metadata metadata;
    };

    /// \brief Aggregate of facts produced by a retrieval/fact-extraction pass.
    struct FactBundle final {
        std::vector<Fact> facts;
    };

    [[nodiscard]] bool operator==(const FactId& lhs, const FactId& rhs) noexcept;
    [[nodiscard]] bool operator!=(const FactId& lhs, const FactId& rhs) noexcept;
    [[nodiscard]] bool operator<(const FactId& lhs, const FactId& rhs) noexcept;

    /// \brief Returns true when the fact carries enough structure to be useful.
    [[nodiscard]] bool is_valid(const Fact& fact) noexcept;

} // namespace agent_memory

#endif
