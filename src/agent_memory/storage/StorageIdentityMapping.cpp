#include "StorageIdentityMapping.hpp"

#include <algorithm>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace agent_memory {

    namespace {

        constexpr std::string_view kMagic = "AMSI";
        constexpr std::size_t kHeaderBytes = 24U;
        constexpr std::uint8_t kDocumentKind = 1U;
        constexpr std::uint8_t kBlockKind = 2U;
        constexpr std::uint8_t kRetiredFlag = 1U;

        [[nodiscard]] bool checked_add(std::size_t left, std::size_t right, std::size_t& result) {
            if(right > std::numeric_limits<std::size_t>::max() - left) {
                return false;
            }
            result = left + right;
            return true;
        }

        [[nodiscard]] bool valid_identifier(std::string_view value) noexcept {
            return !value.empty();
        }

        void require_codec_options(const StorageIdentityCodecOptions& options) {
            if(options.max_record_bytes < kHeaderBytes || options.max_identifier_bytes == 0U) {
                throw std::invalid_argument("StorageIdentityCodecOptions bounds are too small");
            }
        }

        void require_identifier_size(
            std::string_view value,
            const StorageIdentityCodecOptions& options,
            const char* label
        ) {
            if(!valid_identifier(value)) {
                throw std::invalid_argument(std::string(label) + " must not be empty");
            }
            if(value.size() > options.max_identifier_bytes
               || value.size() > std::numeric_limits<std::uint32_t>::max()) {
                throw std::invalid_argument(std::string(label) + " exceeds codec bound");
            }
        }

        void append_u32(std::string& output, std::uint32_t value) {
            for(unsigned shift = 0U; shift < 32U; shift += 8U) {
                output.push_back(static_cast<char>((value >> shift) & 0xffU));
            }
        }

        void append_u64(std::string& output, std::uint64_t value) {
            for(unsigned shift = 0U; shift < 64U; shift += 8U) {
                output.push_back(static_cast<char>((value >> shift) & 0xffU));
            }
        }

        [[nodiscard]] std::uint32_t read_u32(std::string_view input, std::size_t offset) {
            if(offset > input.size() || input.size() - offset < 4U) {
                throw std::runtime_error("storage identity record is truncated");
            }
            std::uint32_t value = 0U;
            for(unsigned shift = 0U; shift < 32U; shift += 8U) {
                value |= static_cast<std::uint32_t>(
                    static_cast<unsigned char>(input[offset + shift / 8U])
                ) << shift;
            }
            return value;
        }

        [[nodiscard]] std::uint64_t read_u64(std::string_view input, std::size_t offset) {
            if(offset > input.size() || input.size() - offset < 8U) {
                throw std::runtime_error("storage identity record is truncated");
            }
            std::uint64_t value = 0U;
            for(unsigned shift = 0U; shift < 64U; shift += 8U) {
                value |= static_cast<std::uint64_t>(
                    static_cast<unsigned char>(input[offset + shift / 8U])
                ) << shift;
            }
            return value;
        }

        template<typename Record>
        [[nodiscard]] std::string encode_record(
            const Record& record,
            std::string_view parent_id,
            std::string_view public_id,
            std::uint64_t storage_id,
            StorageIdentityCodecOptions options,
            std::uint8_t kind
        ) {
            require_codec_options(options);
            if(storage_id == 0U) {
                throw std::invalid_argument("storage identity record id must be non-zero");
            }
            require_identifier_size(public_id, options, "public identity");
            if(kind == kBlockKind) {
                require_identifier_size(parent_id, options, "document identity");
            } else if(!parent_id.empty()) {
                throw std::invalid_argument("document record must not have a parent identity");
            }

            std::size_t size = kHeaderBytes;
            if(!checked_add(size, parent_id.size(), size)
               || !checked_add(size, public_id.size(), size)
               || size > options.max_record_bytes) {
                throw std::invalid_argument("storage identity record exceeds codec bound");
            }

            std::string output;
            output.reserve(size);
            output.append(kMagic.data(), kMagic.size());
            output.push_back(static_cast<char>(kStorageIdentityRecordVersion));
            output.push_back(static_cast<char>(kind));
            output.push_back(static_cast<char>(record.retired ? kRetiredFlag : 0U));
            output.push_back('\0');
            append_u64(output, storage_id);
            append_u32(output, static_cast<std::uint32_t>(parent_id.size()));
            append_u32(output, static_cast<std::uint32_t>(public_id.size()));
            output.append(parent_id.data(), parent_id.size());
            output.append(public_id.data(), public_id.size());
            return output;
        }

        [[nodiscard]] std::string_view checked_field(
            std::string_view bytes,
            std::size_t& position,
            std::uint32_t length,
            const StorageIdentityCodecOptions& options,
            const char* label
        ) {
            if(length > options.max_identifier_bytes) {
                throw std::runtime_error(std::string(label) + " exceeds decode bound");
            }
            if(position > bytes.size() || bytes.size() - position < length) {
                throw std::runtime_error("storage identity record is truncated");
            }
            const auto result = bytes.substr(position, length);
            position += length;
            return result;
        }

        [[nodiscard]] std::string make_block_key(
            const DocumentId& document_id,
            const ContentBlockId& block_id
        ) {
            // Length-prefixing avoids ambiguity if either public id contains a
            // separator.  This key is local to the in-memory adapter only.
            return std::to_string(document_id.value().size()) + ":"
                + document_id.value() + block_id.value();
        }

        struct DocumentEntry final {
            DocumentStorageId storage_id;
            bool retired = false;
        };

        struct BlockEntry final {
            DocumentId document_id;
            ContentBlockId block_id;
            BlockStorageId storage_id;
            bool retired = false;
        };

        [[nodiscard]] std::uint64_t next_id_after(
            std::uint64_t value
        ) {
            if(value == std::numeric_limits<std::uint64_t>::max()) {
                // Zero is an internal exhausted sentinel; it can never be
                // returned as an assigned storage identity.
                return 0U;
            }
            return value + 1U;
        }

    } // namespace

    IStorageIdentityMapping::~IStorageIdentityMapping() = default;

    std::string encode_storage_identity_record(
        const StorageIdentityRecord& record,
        StorageIdentityCodecOptions options
    ) {
        return std::visit(
            [&options](const auto& value) {
                using Record = std::decay_t<decltype(value)>;
                if constexpr(std::is_same_v<Record, DocumentStorageIdentityRecord>) {
                    return encode_record(
                        value,
                        std::string_view{},
                        value.document_id.value(),
                        value.storage_id.value(),
                        options,
                        kDocumentKind
                    );
                } else {
                    return encode_record(
                        value,
                        value.document_id.value(),
                        value.block_id.value(),
                        value.storage_id.value(),
                        options,
                        kBlockKind
                    );
                }
            },
            record
        );
    }

    StorageIdentityRecord decode_storage_identity_record(
        std::string_view bytes,
        StorageIdentityCodecOptions options
    ) {
        require_codec_options(options);
        if(bytes.size() > options.max_record_bytes) {
            throw std::runtime_error("storage identity record exceeds decode bound");
        }
        if(bytes.size() < kHeaderBytes) {
            throw std::runtime_error("storage identity record is truncated");
        }
        if(bytes.substr(0U, kMagic.size()) != kMagic) {
            throw std::runtime_error("storage identity record magic is invalid");
        }
        if(static_cast<std::uint8_t>(bytes[4]) != kStorageIdentityRecordVersion) {
            throw std::runtime_error("unsupported storage identity record version");
        }

        const auto kind = static_cast<std::uint8_t>(bytes[5]);
        const auto flags = static_cast<std::uint8_t>(bytes[6]);
        if(kind != kDocumentKind && kind != kBlockKind) {
            throw std::runtime_error("unknown storage identity record kind");
        }
        if((flags & static_cast<std::uint8_t>(~kRetiredFlag)) != 0U || bytes[7] != '\0') {
            throw std::runtime_error("storage identity record flags are invalid");
        }

        const auto storage_id = read_u64(bytes, 8U);
        if(storage_id == 0U) {
            throw std::runtime_error("storage identity record id is zero");
        }
        const auto parent_length = read_u32(bytes, 16U);
        const auto public_length = read_u32(bytes, 20U);
        std::size_t position = kHeaderBytes;
        const auto parent = checked_field(
            bytes,
            position,
            parent_length,
            options,
            "document identity"
        );
        const auto public_id = checked_field(
            bytes,
            position,
            public_length,
            options,
            "public identity"
        );
        if(position != bytes.size()) {
            throw std::runtime_error("storage identity record has trailing bytes");
        }
        if(!valid_identifier(public_id)) {
            throw std::runtime_error("storage identity record public identity is empty");
        }

        if(kind == kDocumentKind) {
            if(!parent.empty()) {
                throw std::runtime_error("document record has a parent identity");
            }
            return DocumentStorageIdentityRecord{
                DocumentStorageId{storage_id},
                DocumentId{std::string(public_id)},
                (flags & kRetiredFlag) != 0U
            };
        }

        if(parent.empty()) {
            throw std::runtime_error("block record has no document identity");
        }
        return BlockStorageIdentityRecord{
            BlockStorageId{storage_id},
            DocumentId{std::string(parent)},
            ContentBlockId{std::string(public_id)},
            (flags & kRetiredFlag) != 0U
        };
    }

    DocumentStorageIdentityRecord decode_document_storage_identity_record(
        std::string_view bytes,
        StorageIdentityCodecOptions options
    ) {
        const auto decoded = decode_storage_identity_record(bytes, options);
        if(!std::holds_alternative<DocumentStorageIdentityRecord>(decoded)) {
            throw std::runtime_error("storage identity record is not a document record");
        }
        return std::get<DocumentStorageIdentityRecord>(decoded);
    }

    BlockStorageIdentityRecord decode_block_storage_identity_record(
        std::string_view bytes,
        StorageIdentityCodecOptions options
    ) {
        const auto decoded = decode_storage_identity_record(bytes, options);
        if(!std::holds_alternative<BlockStorageIdentityRecord>(decoded)) {
            throw std::runtime_error("storage identity record is not a block record");
        }
        return std::get<BlockStorageIdentityRecord>(decoded);
    }

    struct StorageIdentityMapping::Impl final {
        explicit Impl(StorageIdentityMappingOptions options)
            : options(std::move(options))
            , next_document_id(this->options.first_document_id)
            , next_block_id(this->options.first_block_id) {
            if(this->options.first_document_id == 0U || this->options.first_block_id == 0U) {
                throw std::invalid_argument("StorageIdentityMapping first ids must be non-zero");
            }
            if(this->options.max_records == 0U) {
                throw std::invalid_argument("StorageIdentityMapping max_records must be non-zero");
            }
        }

        StorageIdentityMappingOptions options;
        std::uint64_t next_document_id;
        std::uint64_t next_block_id;
        std::map<std::string, DocumentEntry> documents;
        std::map<std::string, BlockEntry> blocks;
    };

    StorageIdentityMapping::StorageIdentityMapping(StorageIdentityMappingOptions options)
        : m_impl(std::make_unique<Impl>(std::move(options))) {}

    StorageIdentityMapping::~StorageIdentityMapping() = default;

    DocumentStorageId StorageIdentityMapping::ensure_document(const DocumentId& document_id) {
        if(document_id.empty()) {
            throw std::invalid_argument("document id must not be empty");
        }
        const auto found = m_impl->documents.find(document_id.value());
        if(found != m_impl->documents.end()) {
            if(found->second.retired) {
                throw std::invalid_argument("retired document id cannot be reused");
            }
            return found->second.storage_id;
        }
        if(m_impl->documents.size() + m_impl->blocks.size() >= m_impl->options.max_records) {
            throw std::length_error("storage identity mapping record limit reached");
        }
        if(m_impl->next_document_id == 0U) {
            throw std::overflow_error("document storage id sequence exhausted");
        }
        const auto id = DocumentStorageId{m_impl->next_document_id};
        m_impl->next_document_id = next_id_after(
            m_impl->next_document_id
        );
        m_impl->documents.emplace(document_id.value(), DocumentEntry{id, false});
        return id;
    }

    BlockStorageId StorageIdentityMapping::ensure_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) {
        if(document_id.empty() || block_id.empty()) {
            throw std::invalid_argument("document and block ids must not be empty");
        }
        const auto document = m_impl->documents.find(document_id.value());
        if(document == m_impl->documents.end()) {
            throw std::invalid_argument("block mapping requires an assigned document");
        }
        if(document->second.retired) {
            throw std::invalid_argument("block mapping requires an active document");
        }
        const auto key = make_block_key(document_id, block_id);
        const auto found = m_impl->blocks.find(key);
        if(found != m_impl->blocks.end()) {
            if(found->second.retired) {
                throw std::invalid_argument("retired block id cannot be reused");
            }
            return found->second.storage_id;
        }
        if(m_impl->documents.size() + m_impl->blocks.size() >= m_impl->options.max_records) {
            throw std::length_error("storage identity mapping record limit reached");
        }
        if(m_impl->next_block_id == 0U) {
            throw std::overflow_error("block storage id sequence exhausted");
        }
        const auto id = BlockStorageId{m_impl->next_block_id};
        m_impl->next_block_id = next_id_after(m_impl->next_block_id);
        m_impl->blocks.emplace(key, BlockEntry{document_id, block_id, id, false});
        return id;
    }

    std::optional<DocumentStorageId> StorageIdentityMapping::find_document(
        const DocumentId& document_id
    ) const {
        if(document_id.empty()) {
            return std::nullopt;
        }
        const auto found = m_impl->documents.find(document_id.value());
        if(found == m_impl->documents.end()) {
            return std::nullopt;
        }
        return found->second.storage_id;
    }

    std::optional<BlockStorageId> StorageIdentityMapping::find_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) const {
        if(document_id.empty() || block_id.empty()) {
            return std::nullopt;
        }
        const auto found = m_impl->blocks.find(make_block_key(document_id, block_id));
        if(found == m_impl->blocks.end()) {
            return std::nullopt;
        }
        return found->second.storage_id;
    }

    bool StorageIdentityMapping::retire_document(const DocumentId& document_id) {
        const auto found = m_impl->documents.find(document_id.value());
        if(found == m_impl->documents.end()) {
            return false;
        }
        if(found->second.retired) {
            return false;
        }
        found->second.retired = true;
        for(auto& block : m_impl->blocks) {
            if(block.second.document_id == document_id) {
                block.second.retired = true;
            }
        }
        return true;
    }

    bool StorageIdentityMapping::retire_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) {
        const auto found = m_impl->blocks.find(make_block_key(document_id, block_id));
        if(found == m_impl->blocks.end() || found->second.retired) {
            return false;
        }
        found->second.retired = true;
        return true;
    }

    std::vector<StorageIdentityRecord> StorageIdentityMapping::snapshot_records() const {
        std::vector<StorageIdentityRecord> result;
        result.reserve(m_impl->documents.size() + m_impl->blocks.size());
        for(const auto& document : m_impl->documents) {
            result.emplace_back(DocumentStorageIdentityRecord{
                document.second.storage_id,
                DocumentId{document.first},
                document.second.retired
            });
        }
        for(const auto& block : m_impl->blocks) {
            result.emplace_back(BlockStorageIdentityRecord{
                block.second.storage_id,
                block.second.document_id,
                block.second.block_id,
                block.second.retired
            });
        }
        std::stable_sort(
            result.begin(),
            result.end(),
            [](const StorageIdentityRecord& lhs, const StorageIdentityRecord& rhs) {
                const auto lhs_id = std::visit(
                    [](const auto& value) { return value.storage_id.value(); },
                    lhs
                );
                const auto rhs_id = std::visit(
                    [](const auto& value) { return value.storage_id.value(); },
                    rhs
                );
                if(lhs_id != rhs_id) {
                    return lhs_id < rhs_id;
                }
                return lhs.index() < rhs.index();
            }
        );
        return result;
    }

    void StorageIdentityMapping::load_records(
        const std::vector<StorageIdentityRecord>& records
    ) {
        if(records.size() > m_impl->options.max_records) {
            throw std::length_error("storage identity mapping record limit reached");
        }

        auto candidate = std::make_unique<Impl>(m_impl->options);
        const auto load_document = [&candidate](const StorageIdentityRecord& record) {
            if(!std::holds_alternative<DocumentStorageIdentityRecord>(record)) {
                return;
            }
            const auto& value = std::get<DocumentStorageIdentityRecord>(record);
            if(value.storage_id.empty() || value.document_id.empty()) {
                throw std::invalid_argument("invalid document storage identity record");
            }
            const auto [it, inserted] = candidate->documents.emplace(
                value.document_id.value(),
                DocumentEntry{value.storage_id, value.retired}
            );
            (void)it;
            if(!inserted) {
                throw std::invalid_argument("duplicate document storage identity record");
            }
            if(candidate->next_document_id != 0U
               && value.storage_id.value() >= candidate->next_document_id) {
                candidate->next_document_id = next_id_after(
                    value.storage_id.value()
                );
            }
        };
        for(const auto& record : records) {
            load_document(record);
        }
        for(const auto& record : records) {
            std::visit(
                [&candidate](const auto& value) {
                    using Record = std::decay_t<decltype(value)>;
                    if constexpr(std::is_same_v<Record, DocumentStorageIdentityRecord>) {
                        // Document records are loaded in a first pass so that
                        // block records may arrive in any durable order.
                        (void)value;
                    } else {
                        if(value.storage_id.empty() || value.document_id.empty() || value.block_id.empty()) {
                            throw std::invalid_argument("invalid block storage identity record");
                        }
                        const auto document = candidate->documents.find(value.document_id.value());
                        if(document == candidate->documents.end()
                           || (document->second.retired && !value.retired)) {
                            throw std::invalid_argument(
                                "block storage identity record requires an active document record"
                            );
                        }
                        const auto inserted = candidate->blocks.emplace(
                            make_block_key(value.document_id, value.block_id),
                            BlockEntry{
                                value.document_id,
                                value.block_id,
                                value.storage_id,
                                value.retired
                            }
                        );
                        if(!inserted.second) {
                            throw std::invalid_argument("duplicate block storage identity record");
                        }
                        if(candidate->next_block_id != 0U
                           && value.storage_id.value() >= candidate->next_block_id) {
                            candidate->next_block_id = next_id_after(
                                value.storage_id.value()
                            );
                        }
                    }
                },
                record
            );
            if(candidate->documents.size() + candidate->blocks.size() > candidate->options.max_records) {
                throw std::length_error("storage identity mapping record limit reached");
            }
        }

        // Numeric identity spaces are independent, so the same number may be
        // used once for a document and once for a block.  Within each space,
        // reject duplicate numeric values even when textual keys differ.
        std::map<std::uint64_t, std::string> document_ids;
        for(const auto& item : candidate->documents) {
            const auto inserted = document_ids.emplace(
                item.second.storage_id.value(),
                item.first
            );
            if(!inserted.second) {
                throw std::invalid_argument("duplicate document storage id");
            }
        }
        std::map<std::uint64_t, std::string> block_ids;
        for(const auto& item : candidate->blocks) {
            const auto inserted = block_ids.emplace(
                item.second.storage_id.value(),
                item.first
            );
            if(!inserted.second) {
                throw std::invalid_argument("duplicate block storage id");
            }
        }
        m_impl = std::move(candidate);
    }

    bool StorageIdentityMapping::is_document_retired(const DocumentId& document_id) const {
        const auto found = m_impl->documents.find(document_id.value());
        return found != m_impl->documents.end() && found->second.retired;
    }

    bool StorageIdentityMapping::is_block_retired(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) const {
        const auto found = m_impl->blocks.find(make_block_key(document_id, block_id));
        return found != m_impl->blocks.end() && found->second.retired;
    }

} // namespace agent_memory
