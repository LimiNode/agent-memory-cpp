#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_IDENTIFIERS_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_IDENTIFIERS_HPP_INCLUDED

/// \file Identifiers.hpp
/// \brief Strong string identifiers for domain entities.

#include <string>

namespace agent_memory {

    /// \brief Stable identifier of an original source resource.
    class ResourceId final {
    public:
        ResourceId() = default;
        explicit ResourceId(std::string value);

        /// \brief Returns identifier text.
        [[nodiscard]] const std::string& value() const noexcept;

        /// \brief Checks whether the identifier has no value.
        [[nodiscard]] bool empty() const noexcept;

    private:
        std::string m_value;
    };

    /// \brief Stable globally unique identifier of an ingested source document.
    /// \note A document identifier must never be reused for a different resource
    /// revision within one storage backend.
    class DocumentId final {
    public:
        DocumentId() = default;
        explicit DocumentId(std::string value);

        /// \brief Returns identifier text.
        [[nodiscard]] const std::string& value() const noexcept;

        /// \brief Checks whether the identifier has no value.
        [[nodiscard]] bool empty() const noexcept;

    private:
        std::string m_value;
    };

    /// \brief Stable identifier of a chunk derived from a document.
    class ChunkId final {
    public:
        ChunkId() = default;
        explicit ChunkId(std::string value);

        /// \brief Returns identifier text.
        [[nodiscard]] const std::string& value() const noexcept;

        /// \brief Checks whether the identifier has no value.
        [[nodiscard]] bool empty() const noexcept;

    private:
        std::string m_value;
    };

    [[nodiscard]] bool operator==(const ResourceId& lhs, const ResourceId& rhs) noexcept;
    [[nodiscard]] bool operator!=(const ResourceId& lhs, const ResourceId& rhs) noexcept;
    [[nodiscard]] bool operator<(const ResourceId& lhs, const ResourceId& rhs) noexcept;

    [[nodiscard]] bool operator==(const DocumentId& lhs, const DocumentId& rhs) noexcept;
    [[nodiscard]] bool operator!=(const DocumentId& lhs, const DocumentId& rhs) noexcept;
    [[nodiscard]] bool operator<(const DocumentId& lhs, const DocumentId& rhs) noexcept;

    [[nodiscard]] bool operator==(const ChunkId& lhs, const ChunkId& rhs) noexcept;
    [[nodiscard]] bool operator!=(const ChunkId& lhs, const ChunkId& rhs) noexcept;
    [[nodiscard]] bool operator<(const ChunkId& lhs, const ChunkId& rhs) noexcept;

} // namespace agent_memory

#endif
