#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_STORAGE_IDENTITY_MAPPING_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_STORAGE_IDENTITY_MAPPING_HPP_INCLUDED

/// \file StorageIdentityMapping.hpp
/// \brief Opt-in numeric identities for physical storage mappings.
///
/// Public document and block identifiers remain textual domain identities.
/// This header provides a separate, optional mapping for backends that benefit
/// from compact numeric keys.  The mapping is deliberately not wired into the
/// canonical-content or retrieval paths.  A backend can persist the returned
/// records and restore them after a restart.

#include <agent_memory/domain/CanonicalContent.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace agent_memory {

    /// \brief Stable numeric identity of a physical document mapping.
    ///
    /// Zero is reserved for an unassigned value.  Assigned values are
    /// allocated monotonically by \ref StorageIdentityMapping and are never
    /// reused, including after a mapping is retired.
    class DocumentStorageId final {
    public:
        constexpr DocumentStorageId() noexcept = default;
        explicit constexpr DocumentStorageId(std::uint64_t value) noexcept
            : m_value(value) {}

        [[nodiscard]] constexpr std::uint64_t value() const noexcept {
            return m_value;
        }

        [[nodiscard]] constexpr bool empty() const noexcept {
            return m_value == 0;
        }

    private:
        std::uint64_t m_value = 0;
    };

    /// \brief Stable numeric identity of a physical block mapping.
    ///
    /// Block identities use a sequence independent from document identities.
    /// A block's textual key is paired with its textual document id before a
    /// numeric value is assigned, so equal block strings in different
    /// documents cannot alias one another.
    class BlockStorageId final {
    public:
        constexpr BlockStorageId() noexcept = default;
        explicit constexpr BlockStorageId(std::uint64_t value) noexcept
            : m_value(value) {}

        [[nodiscard]] constexpr std::uint64_t value() const noexcept {
            return m_value;
        }

        [[nodiscard]] constexpr bool empty() const noexcept {
            return m_value == 0;
        }

    private:
        std::uint64_t m_value = 0;
    };

    [[nodiscard]] constexpr bool operator==(
        DocumentStorageId lhs,
        DocumentStorageId rhs
    ) noexcept {
        return lhs.value() == rhs.value();
    }

    [[nodiscard]] constexpr bool operator!=(
        DocumentStorageId lhs,
        DocumentStorageId rhs
    ) noexcept {
        return !(lhs == rhs);
    }

    [[nodiscard]] constexpr bool operator<(
        DocumentStorageId lhs,
        DocumentStorageId rhs
    ) noexcept {
        return lhs.value() < rhs.value();
    }

    [[nodiscard]] constexpr bool operator==(
        BlockStorageId lhs,
        BlockStorageId rhs
    ) noexcept {
        return lhs.value() == rhs.value();
    }

    [[nodiscard]] constexpr bool operator!=(
        BlockStorageId lhs,
        BlockStorageId rhs
    ) noexcept {
        return !(lhs == rhs);
    }

    [[nodiscard]] constexpr bool operator<(
        BlockStorageId lhs,
        BlockStorageId rhs
    ) noexcept {
        return lhs.value() < rhs.value();
    }

    /// \brief One versioned document identity record for durable persistence.
    struct DocumentStorageIdentityRecord final {
        DocumentStorageId storage_id;
        DocumentId document_id;
        /// Retired records remain reserved and must not be allocated again.
        bool retired = false;
    };

    /// \brief One versioned block identity record for durable persistence.
    struct BlockStorageIdentityRecord final {
        BlockStorageId storage_id;
        DocumentId document_id;
        ContentBlockId block_id;
        /// Retired records remain reserved and must not be allocated again.
        bool retired = false;
    };

    /// \brief Short aliases for code that names records after their storage id.
    using DocumentStorageIdRecord = DocumentStorageIdentityRecord;
    using BlockStorageIdRecord = BlockStorageIdentityRecord;

    /// \brief Record alternatives emitted by the storage-identity codec.
    using StorageIdentityRecord = std::variant<
        DocumentStorageIdentityRecord,
        BlockStorageIdentityRecord
    >;

    /// \brief Wire format version for storage-identity records.
    inline constexpr std::uint8_t kStorageIdentityRecordVersion = 1;
    /// \brief Alias naming the codec version explicitly.
    inline constexpr std::uint8_t kStorageIdentityCodecVersion =
        kStorageIdentityRecordVersion;

    /// \brief Maximum sizes accepted by the binary record codec.
    ///
    /// Limits are checked before allocating or reserving memory while decoding
    /// untrusted bytes.  Callers that need larger textual ids must opt in with
    /// an explicitly larger bound.
    struct StorageIdentityCodecOptions final {
        std::size_t max_record_bytes = 64U * 1024U;
        std::size_t max_identifier_bytes = 4U * 1024U;
    };

    /// \brief Encodes one document or block mapping record as AMSI v1 bytes.
    [[nodiscard]] std::string encode_storage_identity_record(
        const StorageIdentityRecord& record,
        StorageIdentityCodecOptions options = {}
    );

    /// \brief Decodes one bounded, versioned AMSI record.
    /// \throws std::runtime_error for truncation, corruption, unsupported
    ///         versions, or values outside \p options.
    [[nodiscard]] StorageIdentityRecord decode_storage_identity_record(
        std::string_view bytes,
        StorageIdentityCodecOptions options = {}
    );

    /// \brief Decodes and requires a document record alternative.
    [[nodiscard]] DocumentStorageIdentityRecord decode_document_storage_identity_record(
        std::string_view bytes,
        StorageIdentityCodecOptions options = {}
    );

    /// \brief Decodes and requires a block record alternative.
    [[nodiscard]] BlockStorageIdentityRecord decode_block_storage_identity_record(
        std::string_view bytes,
        StorageIdentityCodecOptions options = {}
    );

    /// \brief Named facade for callers that prefer a codec type.
    class StorageIdentityRecordCodec final {
    public:
        [[nodiscard]] static std::string encode(
            const StorageIdentityRecord& record,
            StorageIdentityCodecOptions options = {}
        ) {
            return encode_storage_identity_record(record, options);
        }

        [[nodiscard]] static StorageIdentityRecord decode(
            std::string_view bytes,
            StorageIdentityCodecOptions options = {}
        ) {
            return decode_storage_identity_record(bytes, options);
        }
    };

    /// \brief Configuration for the opt-in in-memory durable-state adapter.
    struct StorageIdentityMappingOptions final {
        /// First numeric value to allocate in each independent sequence.
        std::uint64_t first_document_id = 1;
        std::uint64_t first_block_id = 1;
        /// Maximum number of live plus retired records accepted by a load.
        std::size_t max_records = 1U * 1024U * 1024U;
    };

    /// \brief Backend-neutral contract for optional numeric identity mapping.
    ///
    /// Implementations own persistence of \ref StorageIdentityRecord values.
    /// No production storage or retrieval component depends on this contract;
    /// callers opt in explicitly when a physical backend needs numeric keys.
    class IStorageIdentityMapping {
    public:
        virtual ~IStorageIdentityMapping();

        /// \brief Returns an existing active mapping or allocates one.
        [[nodiscard]] virtual DocumentStorageId ensure_document(
            const DocumentId& document_id
        ) = 0;

        /// \brief Returns an existing active mapping or allocates one.
        [[nodiscard]] virtual BlockStorageId ensure_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) = 0;

        /// \brief Finds an assigned id, including a retired historical record.
        [[nodiscard]] virtual std::optional<DocumentStorageId> find_document(
            const DocumentId& document_id
        ) const = 0;

        /// \brief Finds an assigned id, including a retired historical record.
        [[nodiscard]] virtual std::optional<BlockStorageId> find_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) const = 0;

        /// \brief Retires a document mapping and all of its block mappings.
        [[nodiscard]] virtual bool retire_document(const DocumentId& document_id) = 0;

        /// \brief Retires one block mapping.
        [[nodiscard]] virtual bool retire_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) = 0;

        /// \brief Returns deterministic records suitable for durable storage.
        [[nodiscard]] virtual std::vector<StorageIdentityRecord> snapshot_records() const = 0;

        /// \brief Replaces state from validated durable records.
        virtual void load_records(const std::vector<StorageIdentityRecord>& records) = 0;
    };

    /// \brief In-memory state machine for an optional durable identity map.
    ///
    /// The adapter is intentionally backend-neutral: callers persist
    /// \ref snapshot_records() using the versioned codec and restore them with
    /// \ref load_records().  Retired records remain in the state and reserve
    /// both the numeric and textual identities, so a delete/recreate sequence
    /// cannot silently alias historical storage.
    class StorageIdentityMapping final : public IStorageIdentityMapping {
    public:
        explicit StorageIdentityMapping(StorageIdentityMappingOptions options = {});
        ~StorageIdentityMapping() override;

        [[nodiscard]] DocumentStorageId ensure_document(
            const DocumentId& document_id
        ) override;

        [[nodiscard]] BlockStorageId ensure_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) override;

        [[nodiscard]] std::optional<DocumentStorageId> find_document(
            const DocumentId& document_id
        ) const override;

        [[nodiscard]] std::optional<BlockStorageId> find_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) const override;

        [[nodiscard]] bool retire_document(const DocumentId& document_id) override;

        [[nodiscard]] bool retire_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) override;

        [[nodiscard]] std::vector<StorageIdentityRecord> snapshot_records() const override;

        void load_records(const std::vector<StorageIdentityRecord>& records) override;

        /// \brief Alias emphasizing explicit allocation at an opt-in boundary.
        [[nodiscard]] DocumentStorageId assign_document(const DocumentId& document_id) {
            return ensure_document(document_id);
        }

        /// \brief Alias emphasizing explicit allocation at an opt-in boundary.
        [[nodiscard]] BlockStorageId assign_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) {
            return ensure_block(document_id, block_id);
        }

        /// \brief Alias for the durable record snapshot boundary.
        [[nodiscard]] std::vector<StorageIdentityRecord> records() const {
            return snapshot_records();
        }

        /// \brief Alias for \ref retire_document.
        [[nodiscard]] bool erase_document(const DocumentId& document_id) {
            return retire_document(document_id);
        }

        /// \brief Alias for \ref retire_block.
        [[nodiscard]] bool erase_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) {
            return retire_block(document_id, block_id);
        }

        /// \brief Reports whether a document record is retired.
        [[nodiscard]] bool is_document_retired(const DocumentId& document_id) const;

        /// \brief Reports whether a block record is retired.
        [[nodiscard]] bool is_block_retired(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) const;

    private:
        struct Impl;
        std::unique_ptr<Impl> m_impl;
    };

    /// \brief Explicitly named alias for callers documenting durability.
    using DurableStorageIdentityMapping = StorageIdentityMapping;

} // namespace agent_memory

#endif
