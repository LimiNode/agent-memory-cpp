#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_RESOURCE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_RESOURCE_HPP_INCLUDED

/// \file Resource.hpp
/// \brief Resource revision and derived-record manifest value types.

#include "Identifiers.hpp"

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Algorithm identifier for a portable resource-body digest.
    enum class ResourceBodyDigestAlgorithm : std::uint8_t {
        Sha256
    };

    /// \brief Algorithm-tagged digest of immutable source bytes.
    struct ResourceBodyDigest final {
        ResourceBodyDigestAlgorithm algorithm = ResourceBodyDigestAlgorithm::Sha256;
        std::array<std::uint8_t, 32> bytes{};
    };

    /// \brief Current revision identity for an original source resource.
    struct ResourceRevision final {
        ResourceId resource_id;
        std::uint64_t generation = 0;
        /// \brief Prototype-local freshness hash, not portable evidence integrity.
        std::uint64_t content_hash = 0;
        std::uint64_t pipeline_config_hash = 0;
        /// \brief Required by the future M0 importer; optional for this prototype.
        std::optional<ResourceBodyDigest> body_digest;
    };

    /// \brief Kind of derived record created from a resource revision.
    enum class DerivedRecordKind {
        Document,
        Chunk,
        Embedding,
        VectorRecord,
        BinaryBucketPosting,
        LexicalPosting,
        GraphRecord,
        Custom
    };

    /// \brief Reference to one derived record owned by a resource revision.
    /// \note Chunk, embedding, and vector records use `chunk_id`.
    /// \note Embedding and vector records use `ChunkId` while there is one
    ///       active representation per chunk and pipeline.
    /// \note Backends that keep multiple embedding variants should use `key`,
    ///       `Custom`, or a later composite identifier contract.
    /// \note Document, posting, graph, and custom records use `key`.
    /// \note `ordinal` is a resource-local order or discriminator, not a stable
    ///       offset inside compressed bucket storage.
    struct DerivedRecordRef final {
        DerivedRecordKind kind = DerivedRecordKind::Chunk;
        ChunkId chunk_id;
        std::string key;
        std::uint32_t ordinal = 0;
    };

    /// \brief Publication state of a resource-owned derived-record manifest.
    enum class ResourceManifestState : std::uint8_t {
        Active,
        ErasePending
    };

    /// \brief Manifest of records derived from one resource revision.
    struct ResourceManifest final {
        ResourceRevision revision;
        std::vector<DerivedRecordRef> records;
        ResourceManifestState state = ResourceManifestState::Active;
    };

    /// \brief Returns stable lowercase derived-record kind name.
    [[nodiscard]] std::string_view to_string(DerivedRecordKind kind) noexcept;

    /// \brief Parses a derived-record kind from a lowercase or mixed-case name.
    /// \return True when parsing succeeds.
    bool parse_derived_record_kind(std::string_view text, DerivedRecordKind& kind);

    /// \brief Returns true when a derived-record kind uses `chunk_id`.
    [[nodiscard]] bool derived_record_kind_uses_chunk_id(
        DerivedRecordKind kind
    ) noexcept;

    /// \brief Returns true when a derived-record kind uses `key`.
    [[nodiscard]] bool derived_record_kind_uses_key(
        DerivedRecordKind kind
    ) noexcept;

    /// \brief Returns true when the required reference field is present.
    [[nodiscard]] bool has_required_reference(const DerivedRecordRef& ref) noexcept;

    /// \brief Returns true when a derived-record reference has only valid fields.
    [[nodiscard]] bool is_valid_derived_record_ref(
        const DerivedRecordRef& ref
    ) noexcept;

    /// \brief Returns true when a manifest can be persisted by storage backends.
    [[nodiscard]] bool is_valid_resource_manifest(
        const ResourceManifest& manifest
    ) noexcept;

    /// \brief Returns whether a manifest is eligible for active retrieval.
    [[nodiscard]] bool is_active_resource_manifest(
        const ResourceManifest& manifest
    ) noexcept;

    /// \brief Checks whether revision hashes match source and pipeline inputs.
    [[nodiscard]] bool matches_revision_hashes(
        const ResourceRevision& revision,
        std::uint64_t content_hash,
        std::uint64_t pipeline_config_hash
    ) noexcept;

} // namespace agent_memory

#endif
