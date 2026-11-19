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
        : max_records(options.max_records)
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
            metadata.insert_or_assign(
                std::string{kSchemaKey}, std::string{kSchemaVersion}, txn);
        }
        txn.commit();
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
    : m_impl(std::make_unique<Impl>(std::move(options))) {
    if(m_impl->max_records == 0) throw std::invalid_argument("max_records must be positive");
}

MdbxStorageIdentityMapping::~MdbxStorageIdentityMapping() = default;

DocumentStorageId MdbxStorageIdentityMapping::ensure_document(const DocumentId& document_id) {
    if(document_id.empty()) throw std::invalid_argument("document id must not be empty");
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto key = document_key(document_id);
    if(const auto encoded = m_impl->records.find(key, txn)) {
        const auto decoded = decode_document_storage_identity_record(*encoded);
        if(decoded.document_id != document_id) throw std::runtime_error("document identity key mismatch");
        if(decoded.retired) throw std::runtime_error("retired document identity cannot be reused");
        if(!m_impl->document_reverse.find(decoded.storage_id.value(), txn)) {
            throw std::runtime_error("document reverse mapping is missing");
        }
        txn.commit();
        return decoded.storage_id;
    }
    auto id = next_id(m_impl->metadata, kNextDocumentKey, txn);
    const DocumentStorageIdentityRecord record{DocumentStorageId{id}, document_id, false};
    m_impl->records.insert_or_assign(key, encode_storage_identity_record(record), txn);
    m_impl->document_reverse.insert_or_assign(id, key, txn);
    advance_id(m_impl->metadata, kNextDocumentKey, id, txn);
    txn.commit();
    return record.storage_id;
}

BlockStorageId MdbxStorageIdentityMapping::ensure_block(
    const DocumentId& document_id, const ContentBlockId& block_id) {
    if(document_id.empty() || block_id.empty()) throw std::invalid_argument("block identity must not be empty");
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto key = block_key(document_id, block_id);
    if(const auto encoded = m_impl->records.find(key, txn)) {
        const auto decoded = decode_block_storage_identity_record(*encoded);
        if(decoded.document_id != document_id || decoded.block_id != block_id) {
            throw std::runtime_error("block identity key mismatch");
        }
        if(decoded.retired) throw std::runtime_error("retired block identity cannot be reused");
        if(!m_impl->block_reverse.find(decoded.storage_id.value(), txn)) {
            throw std::runtime_error("block reverse mapping is missing");
        }
        txn.commit();
        return decoded.storage_id;
    }
    auto id = next_id(m_impl->metadata, kNextBlockKey, txn);
    const BlockStorageIdentityRecord record{BlockStorageId{id}, document_id, block_id, false};
    m_impl->records.insert_or_assign(key, encode_storage_identity_record(record), txn);
    m_impl->block_reverse.insert_or_assign(id, key, txn);
    advance_id(m_impl->metadata, kNextBlockKey, id, txn);
    txn.commit();
    return record.storage_id;
}

std::optional<DocumentStorageId> MdbxStorageIdentityMapping::find_document(
    const DocumentId& document_id) const {
    if(document_id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto encoded = m_impl->records.find(document_key(document_id), txn);
    if(!encoded) { txn.commit(); return std::nullopt; }
    const auto record = decode_document_storage_identity_record(*encoded);
    if(record.document_id != document_id) throw std::runtime_error("document identity key mismatch");
    txn.commit();
    return record.storage_id;
}

std::optional<BlockStorageId> MdbxStorageIdentityMapping::find_block(
    const DocumentId& document_id, const ContentBlockId& block_id) const {
    if(document_id.empty() || block_id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto encoded = m_impl->records.find(block_key(document_id, block_id), txn);
    if(!encoded) { txn.commit(); return std::nullopt; }
    const auto record = decode_block_storage_identity_record(*encoded);
    if(record.document_id != document_id || record.block_id != block_id) {
        throw std::runtime_error("block identity key mismatch");
    }
    txn.commit();
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
    txn.commit();
    return std::make_pair(record.document_id, record.block_id);
}

bool MdbxStorageIdentityMapping::retire_document(const DocumentId& document_id) {
    if(document_id.empty()) return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto key = document_key(document_id);
    const auto encoded = m_impl->records.find(key, txn);
    if(!encoded) { txn.commit(); return false; }
    const auto record = decode_document_storage_identity_record(*encoded);
    if(record.retired) { txn.commit(); return false; }
    auto retired = record;
    retired.retired = true;
    m_impl->records.insert_or_assign(key, encode_storage_identity_record(retired), txn);
    const auto block_rows = m_impl->records.range(
        std::string{"b:"}, std::string{"b;"}, txn);
    for(const auto& row : block_rows) {
        const auto block = decode_block_storage_identity_record(row.second);
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
    const auto rows = m_impl->records.range(std::string{}, std::string(1, static_cast<char>(0xff)), txn);
    if(rows.size() > m_impl->max_records) throw std::runtime_error("storage identity snapshot is too large");
    std::vector<StorageIdentityRecord> result;
    result.reserve(rows.size());
    for(const auto& row : rows) result.push_back(decode_storage_identity_record(row.second));
    txn.commit();
    std::sort(result.begin(), result.end(), [](const StorageIdentityRecord& lhs, const StorageIdentityRecord& rhs) {
        const auto id = [](const StorageIdentityRecord& value) { return std::visit([](const auto& item) { return item.storage_id.value(); }, value); };
        return id(lhs) < id(rhs);
    });
    return result;
}

void MdbxStorageIdentityMapping::load_records(const std::vector<StorageIdentityRecord>& input) {
    if(input.size() > m_impl->max_records) throw std::invalid_argument("storage identity snapshot is too large");
    StorageIdentityMapping candidate;
    candidate.load_records(input);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto rows = m_impl->records.range(std::string{}, std::string(1, static_cast<char>(0xff)), txn);
    for(const auto& row : rows) m_impl->records.erase(row.first, txn);
    const auto documents = m_impl->document_reverse.range(0, std::numeric_limits<std::uint64_t>::max(), txn);
    for(const auto& row : documents) m_impl->document_reverse.erase(row.first, txn);
    const auto blocks = m_impl->block_reverse.range(0, std::numeric_limits<std::uint64_t>::max(), txn);
    for(const auto& row : blocks) m_impl->block_reverse.erase(row.first, txn);
    std::uint64_t next_document = 1;
    std::uint64_t next_block = 1;
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
