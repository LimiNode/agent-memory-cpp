#include "MdbxStorageIdentityMapping.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <type_traits>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kSchemaKey = "@schema_version";
constexpr std::string_view kSchemaVersion = "storage_identity_mapping.v1";
constexpr std::string_view kNextDocumentKey = "@next_document_id";
constexpr std::string_view kNextBlockKey = "@next_block_id";

std::size_t checked_max_records(std::size_t value) {
    if(value == 0U) throw std::invalid_argument("max_records must be positive");
    return value;
}

std::string sanitize_table_part(std::string value) {
    if(value.empty()) value = "agent_memory";
    for(char& c : value) {
        if(!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    }
    return value;
}

std::string table_name(const std::string& prefix, std::string_view suffix) {
    auto result = sanitize_table_part(prefix);
    result.push_back('_');
    result.append(suffix.data(), suffix.size());
    return result;
}

void append_string(std::string& output, std::string_view value) {
    output += std::to_string(value.size());
    output.push_back(':');
    output.append(value.data(), value.size());
}

std::string document_key(const DocumentId& id) {
    std::string result{"d:"};
    append_string(result, id.value());
    return result;
}

std::string block_key(const DocumentId& document_id, const ContentBlockId& block_id) {
    std::string result{"b:"};
    append_string(result, document_id.value());
    append_string(result, block_id.value());
    return result;
}

std::string block_prefix(const DocumentId& document_id) {
    std::string result{"b:"};
    append_string(result, document_id.value());
    return result;
}

std::uint64_t parse_id(std::string_view text) {
    if(text.empty()) throw std::runtime_error("empty storage identity sequence");
    std::uint64_t value = 0;
    for(const char c : text) {
        if(c < '0' || c > '9') throw std::runtime_error("invalid storage identity sequence");
        const auto digit = static_cast<std::uint64_t>(c - '0');
        if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
            throw std::runtime_error("storage identity sequence overflow");
        }
        value = value * 10U + digit;
    }
    if(value == 0) throw std::runtime_error("zero storage identity sequence");
    return value;
}

std::uint64_t next_id(
    mdbxc::KeyValueTable<std::string, std::string>& metadata,
    std::string_view key,
    const mdbxc::Transaction& txn
) {
    if(const auto value = metadata.find(std::string{key}, txn)) return parse_id(*value);
    return 1;
}

void validate_sequence_metadata(
    mdbxc::KeyValueTable<std::string, std::string>& metadata,
    std::string_view metadata_key,
    mdbxc::KeyValueTable<std::uint64_t, std::string>& reverse,
    const mdbxc::Transaction& txn
) {
    const auto highest = reverse.max_key(txn);
    const auto encoded_next = metadata.find(std::string{metadata_key}, txn);
    if(!encoded_next) {
        if(highest) {
            throw std::runtime_error("storage identity sequence metadata is missing");
        }
        return;
    }
    const auto next = parse_id(*encoded_next);
    if(highest && *highest >= next) {
        throw std::runtime_error("storage identity sequence metadata is behind reverse mapping");
    }
}

void validate_transaction(
    const MdbxStorageContext& context,
    const mdbxc::Transaction& transaction,
    bool writable
) {
    const auto handle = transaction.handle();
    if(!handle || mdbx_txn_env(handle) != context.connection()->env_handle()) {
        throw std::invalid_argument("identity mapping transaction belongs to another context or is inactive");
    }
    const auto flags = mdbx_txn_flags(handle);
    if(flags == MDBX_TXN_INVALID || (flags & MDBX_TXN_BLOCKED) != 0) {
        throw std::invalid_argument("identity mapping transaction is inactive or blocked");
    }
    if(writable && (flags & MDBX_TXN_RDONLY) != 0) {
        throw std::invalid_argument("identity mapping requires a writable transaction");
    }
}

void advance_id(
    mdbxc::KeyValueTable<std::string, std::string>& metadata,
    std::string_view key,
    std::uint64_t value,
    const mdbxc::Transaction& txn
) {
    if(value == std::numeric_limits<std::uint64_t>::max()) {
        throw std::runtime_error("storage identity sequence exhausted");
    }
    metadata.insert_or_assign(
        std::string{key}, std::to_string(value + 1U), txn);
}

} // namespace

class MdbxStorageIdentityMapping::Impl final {
public:
    explicit Impl(MdbxStorageIdentityMappingOptions options)
        : max_records(checked_max_records(options.max_records))
        , context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(std::move(options.path),
                                                             options.relative_to_exe,
                                                             options.max_dbs))
        , records(context->connection(), table_name(options.table_prefix, "identity_records_v1"))
        , document_reverse(context->connection(), table_name(options.table_prefix, "identity_documents_v1"))
        , block_reverse(context->connection(), table_name(options.table_prefix, "identity_blocks_v1"))
        , metadata(context->connection(), table_name(options.table_prefix, "identity_meta_v1")) {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        if(const auto marker = metadata.find(std::string{kSchemaKey}, txn)) {
            if(*marker != kSchemaVersion) {
                txn.rollback();
                throw std::runtime_error("unsupported storage identity mapping schema");
            }
        } else {
            if(
                !records.empty(txn)
                || !document_reverse.empty(txn)
                || !block_reverse.empty(txn)
                || metadata.find(std::string{kNextDocumentKey}, txn)
                || metadata.find(std::string{kNextBlockKey}, txn)
            ) {
                txn.rollback();
                throw std::runtime_error(
                    "storage identity mapping rejects markerless non-empty state"
                );
            }
            metadata.insert_or_assign(
                std::string{kSchemaKey}, std::string{kSchemaVersion}, txn);
        }
        validate_sequence_metadata(metadata, kNextDocumentKey, document_reverse, txn);
        validate_sequence_metadata(metadata, kNextBlockKey, block_reverse, txn);
        txn.commit();
    }

    DocumentStorageId ensure_document_in_transaction(
        const DocumentId& document_id,
        mdbxc::Transaction& txn
    ) {
        if(document_id.empty()) throw std::invalid_argument("document id must not be empty");
        const auto key = document_key(document_id);
        if(const auto encoded = records.find(key, txn)) {
            const auto decoded = decode_document_storage_identity_record(*encoded);
            if(decoded.document_id != document_id) {
                throw std::runtime_error("document identity key mismatch");
            }
            if(decoded.retired) {
                throw std::runtime_error("retired document identity cannot be reused");
            }
            const auto reverse_key = document_reverse.find(decoded.storage_id.value(), txn);
            if(!reverse_key) throw std::runtime_error("document reverse mapping is missing");
            if(*reverse_key != key) {
                throw std::runtime_error("document reverse mapping does not match forward key");
            }
            return decoded.storage_id;
        }
        // count() reads MDBX table statistics, not mapping payloads.
        if(records.count(txn) >= max_records) {
            throw std::length_error("storage identity mapping record limit reached");
        }
        const auto id = next_id(metadata, kNextDocumentKey, txn);
        if(id == std::numeric_limits<std::uint64_t>::max()) {
            throw std::runtime_error("storage identity sequence exhausted");
        }
        const DocumentStorageIdentityRecord record{DocumentStorageId{id}, document_id, false};
        records.insert_or_assign(key, encode_storage_identity_record(record), txn);
        document_reverse.insert_or_assign(id, key, txn);
        advance_id(metadata, kNextDocumentKey, id, txn);
        return record.storage_id;
    }

    BlockStorageId ensure_block_in_transaction(
        const DocumentId& document_id,
        const ContentBlockId& block_id,
        mdbxc::Transaction& txn
    ) {
        if(document_id.empty() || block_id.empty()) {
            throw std::invalid_argument("block identity must not be empty");
        }
        const auto document_payload = records.find(document_key(document_id), txn);
        if(!document_payload) {
            throw std::invalid_argument("block mapping requires an assigned document");
        }
        const auto document = decode_document_storage_identity_record(*document_payload);
        if(document.document_id != document_id) {
            throw std::runtime_error("document identity key mismatch while admitting block");
        }
        if(document.retired) {
            throw std::invalid_argument("block mapping requires an active document");
        }
        const auto key = block_key(document_id, block_id);
        if(const auto encoded = records.find(key, txn)) {
            const auto decoded = decode_block_storage_identity_record(*encoded);
            if(decoded.document_id != document_id || decoded.block_id != block_id) {
                throw std::runtime_error("block identity key mismatch");
            }
            if(decoded.retired) throw std::runtime_error("retired block identity cannot be reused");
            const auto reverse_key = block_reverse.find(decoded.storage_id.value(), txn);
            if(!reverse_key) throw std::runtime_error("block reverse mapping is missing");
            if(*reverse_key != key) {
                throw std::runtime_error("block reverse mapping does not match forward key");
            }
            return decoded.storage_id;
        }
        if(records.count(txn) >= max_records) {
            throw std::length_error("storage identity mapping record limit reached");
        }
        const auto id = next_id(metadata, kNextBlockKey, txn);
        if(id == std::numeric_limits<std::uint64_t>::max()) {
            throw std::runtime_error("storage identity sequence exhausted");
        }
        const BlockStorageIdentityRecord record{BlockStorageId{id}, document_id, block_id, false};
        records.insert_or_assign(key, encode_storage_identity_record(record), txn);
        block_reverse.insert_or_assign(id, key, txn);
        advance_id(metadata, kNextBlockKey, id, txn);
        return record.storage_id;
    }

    const std::size_t max_records;
    std::shared_ptr<MdbxStorageContext> context;
    mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::string, std::string> records;
    mdbxc::KeyValueTable<std::uint64_t, std::string> document_reverse;
    mdbxc::KeyValueTable<std::uint64_t, std::string> block_reverse;
    mdbxc::KeyValueTable<std::string, std::string> metadata;
};

MdbxStorageIdentityMapping::MdbxStorageIdentityMapping(
    MdbxStorageIdentityMappingOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxStorageIdentityMapping::~MdbxStorageIdentityMapping() = default;

DocumentStorageId MdbxStorageIdentityMapping::ensure_document(const DocumentId& document_id) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto result = m_impl->ensure_document_in_transaction(document_id, txn);
    txn.commit();
    return result;
}

BlockStorageId MdbxStorageIdentityMapping::ensure_block(
    const DocumentId& document_id, const ContentBlockId& block_id) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto result = m_impl->ensure_block_in_transaction(document_id, block_id, txn);
    txn.commit();
    return result;
}

DocumentStorageId MdbxStorageIdentityMapping::ensure_document_in_transaction(
    const DocumentId& document_id,
    mdbxc::Transaction& transaction
) {
    validate_transaction(*m_impl->context, transaction, true);
    // MDBX serializes writers. Do not take the instance mutex after a caller
    // has acquired its writer: an ordinary call can hold it awaiting that writer.
    return m_impl->ensure_document_in_transaction(document_id, transaction);
}

BlockStorageId MdbxStorageIdentityMapping::ensure_block_in_transaction(
    const DocumentId& document_id,
    const ContentBlockId& block_id,
    mdbxc::Transaction& transaction
) {
    validate_transaction(*m_impl->context, transaction, true);
    return m_impl->ensure_block_in_transaction(document_id, block_id, transaction);
}

std::optional<DocumentStorageId> MdbxStorageIdentityMapping::find_document(
    const DocumentId& document_id) const {
    if(document_id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto result = find_document_in_transaction(document_id, txn);
    txn.commit();
    return result;
}

std::optional<DocumentStorageId> MdbxStorageIdentityMapping::find_document_in_transaction(
    const DocumentId& document_id, const mdbxc::Transaction& transaction) const {
    validate_transaction(*m_impl->context, transaction, false);
    if(document_id.empty()) return std::nullopt;
    const auto encoded = m_impl->records.find(document_key(document_id), transaction);
    if(!encoded) return std::nullopt;
    const auto record = decode_document_storage_identity_record(*encoded);
    if(record.document_id != document_id) throw std::runtime_error("document identity key mismatch");
    return record.storage_id;
}

std::optional<BlockStorageId> MdbxStorageIdentityMapping::find_block(
    const DocumentId& document_id, const ContentBlockId& block_id) const {
    if(document_id.empty() || block_id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto result = find_block_in_transaction(document_id, block_id, txn);
    txn.commit();
    return result;
}

std::optional<BlockStorageId> MdbxStorageIdentityMapping::find_block_in_transaction(
    const DocumentId& document_id, const ContentBlockId& block_id,
    const mdbxc::Transaction& transaction) const {
    validate_transaction(*m_impl->context, transaction, false);
    if(document_id.empty() || block_id.empty()) return std::nullopt;
    const auto encoded = m_impl->records.find(block_key(document_id, block_id), transaction);
    if(!encoded) return std::nullopt;
    const auto record = decode_block_storage_identity_record(*encoded);
    if(record.document_id != document_id || record.block_id != block_id) {
        throw std::runtime_error("block identity key mismatch");
    }
    return record.storage_id;
}

std::optional<DocumentId> MdbxStorageIdentityMapping::resolve_document(
    DocumentStorageId id) const {
    if(id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto key = m_impl->document_reverse.find(id.value(), txn);
    if(!key) { txn.commit(); return std::nullopt; }
    const auto encoded = m_impl->records.find(*key, txn);
    if(!encoded) throw std::runtime_error("document reverse record is missing");
    const auto record = decode_document_storage_identity_record(*encoded);
    if(record.storage_id != id) throw std::runtime_error("document reverse id mismatch");
    if(document_key(record.document_id) != *key) {
        throw std::runtime_error("document reverse key mismatch");
    }
    txn.commit();
    return record.document_id;
}

std::optional<std::pair<DocumentId, ContentBlockId>> MdbxStorageIdentityMapping::resolve_block(
    BlockStorageId id) const {
    if(id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto key = m_impl->block_reverse.find(id.value(), txn);
    if(!key) { txn.commit(); return std::nullopt; }
    const auto encoded = m_impl->records.find(*key, txn);
    if(!encoded) throw std::runtime_error("block reverse record is missing");
    const auto record = decode_block_storage_identity_record(*encoded);
    if(record.storage_id != id) throw std::runtime_error("block reverse id mismatch");
    if(block_key(record.document_id, record.block_id) != *key) {
        throw std::runtime_error("block reverse key mismatch");
    }
    txn.commit();
    return std::make_pair(record.document_id, record.block_id);
}

bool MdbxStorageIdentityMapping::retire_document(const DocumentId& document_id) {
    if(document_id.empty()) return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto count = m_impl->records.count(txn);
    if(count > m_impl->max_records) throw std::length_error("storage identity namespace is too large");
    const auto key = document_key(document_id);
    const auto encoded = m_impl->records.find(key, txn);
    if(!encoded) { txn.commit(); return false; }
    const auto record = decode_document_storage_identity_record(*encoded);
    if(record.document_id != document_id) {
        throw std::runtime_error("document identity key mismatch while retiring");
    }
    if(record.retired) { txn.commit(); return false; }
    auto retired = record;
    retired.retired = true;
    m_impl->records.insert_or_assign(key, encode_storage_identity_record(retired), txn);
    const auto prefix = block_prefix(document_id);
    std::vector<std::pair<std::string, std::string>> block_rows;
    block_rows.reserve(count);
    m_impl->records.for_each_range(
        prefix,
        prefix + std::string(1, static_cast<char>(0xff)),
        [&](const std::string& row_key, const std::string& payload) {
            if(block_rows.size() >= m_impl->max_records) {
                throw std::length_error("storage identity namespace is too large");
            }
            block_rows.emplace_back(row_key, payload);
            return true;
        }, txn);
    for(const auto& row : block_rows) {
        const auto block = decode_block_storage_identity_record(row.second);
        if(block_key(block.document_id, block.block_id) != row.first) {
            throw std::runtime_error("block identity key mismatch while retiring document");
        }
        if(block.document_id == document_id && !block.retired) {
            auto retired_block = block;
            retired_block.retired = true;
            m_impl->records.insert_or_assign(
                row.first, encode_storage_identity_record(retired_block), txn);
        }
    }
    txn.commit();
    return true;
}

bool MdbxStorageIdentityMapping::retire_block(
    const DocumentId& document_id, const ContentBlockId& block_id) {
    if(document_id.empty() || block_id.empty()) return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto key = block_key(document_id, block_id);
    const auto encoded = m_impl->records.find(key, txn);
    if(!encoded) { txn.commit(); return false; }
    const auto record = decode_block_storage_identity_record(*encoded);
    if(record.document_id != document_id || record.block_id != block_id) {
        throw std::runtime_error("block identity key mismatch while retiring");
    }
    if(record.retired) { txn.commit(); return false; }
    auto retired = record;
    retired.retired = true;
    m_impl->records.insert_or_assign(key, encode_storage_identity_record(retired), txn);
    txn.commit();
    return true;
}

std::vector<StorageIdentityRecord> MdbxStorageIdentityMapping::snapshot_records() const {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto count = m_impl->records.count(txn);
    if(count > m_impl->max_records) throw std::length_error("storage identity snapshot is too large");
    std::vector<StorageIdentityRecord> result;
    result.reserve(count);
    m_impl->records.for_each_range(
        std::string{}, std::string(1, static_cast<char>(0xff)),
        [&](const std::string& key, const std::string& payload) {
            if(result.size() >= m_impl->max_records) {
                throw std::length_error("storage identity snapshot is too large");
            }
            auto record = decode_storage_identity_record(payload);
            const auto expected_key = std::visit([](const auto& value) {
                if constexpr(std::is_same_v<std::decay_t<decltype(value)>, DocumentStorageIdentityRecord>) {
                    return document_key(value.document_id);
                } else {
                    return block_key(value.document_id, value.block_id);
                }
            }, record);
            if(key != expected_key) throw std::runtime_error("storage identity snapshot key mismatch");
            result.push_back(std::move(record));
            return true;
        }, txn);
    if(result.size() != count) throw std::runtime_error("storage identity snapshot contains an invalid key");
    txn.commit();
    std::sort(result.begin(), result.end(), [](const StorageIdentityRecord& lhs, const StorageIdentityRecord& rhs) {
        const auto id = [](const StorageIdentityRecord& value) { return std::visit([](const auto& item) { return item.storage_id.value(); }, value); };
        if(id(lhs) != id(rhs)) return id(lhs) < id(rhs);
        if(lhs.index() != rhs.index()) return lhs.index() < rhs.index();
        return encode_storage_identity_record(lhs) < encode_storage_identity_record(rhs);
    });
    return result;
}

void MdbxStorageIdentityMapping::load_records(const std::vector<StorageIdentityRecord>& input) {
    if(input.size() > m_impl->max_records) throw std::invalid_argument("storage identity snapshot is too large");
    StorageIdentityMappingOptions candidate_options;
    candidate_options.max_records = m_impl->max_records;
    StorageIdentityMapping candidate(candidate_options);
    candidate.load_records(input);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    // High-water preservation alone cannot prevent a snapshot from rebinding
    // a previously assigned number. Only a fresh namespace admits an import.
    if(!m_impl->records.empty(txn) || !m_impl->document_reverse.empty(txn)
       || !m_impl->block_reverse.empty(txn)
       || m_impl->metadata.find(std::string{kNextDocumentKey}, txn)
       || m_impl->metadata.find(std::string{kNextBlockKey}, txn)) {
        throw std::invalid_argument("identity snapshot import requires a fresh empty namespace");
    }
    std::uint64_t next_document = 1U;
    std::uint64_t next_block = 1U;
    for(const auto& record : input) {
        std::visit([&](const auto& value) {
            if constexpr(std::is_same_v<std::decay_t<decltype(value)>, DocumentStorageIdentityRecord>) {
                const auto key = document_key(value.document_id);
                m_impl->records.insert_or_assign(key, encode_storage_identity_record(value), txn);
                m_impl->document_reverse.insert_or_assign(value.storage_id.value(), key, txn);
                if(value.storage_id.value() == std::numeric_limits<std::uint64_t>::max()) {
                    throw std::invalid_argument("document storage identity space exhausted");
                }
                next_document = std::max(next_document, value.storage_id.value() + 1U);
            } else {
                const auto key = block_key(value.document_id, value.block_id);
                m_impl->records.insert_or_assign(key, encode_storage_identity_record(value), txn);
                m_impl->block_reverse.insert_or_assign(value.storage_id.value(), key, txn);
                if(value.storage_id.value() == std::numeric_limits<std::uint64_t>::max()) {
                    throw std::invalid_argument("block storage identity space exhausted");
                }
                next_block = std::max(next_block, value.storage_id.value() + 1U);
            }
        }, record);
    }
    m_impl->metadata.insert_or_assign(std::string{kNextDocumentKey}, std::to_string(next_document), txn);
    m_impl->metadata.insert_or_assign(std::string{kNextBlockKey}, std::to_string(next_block), txn);
    txn.commit();
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
