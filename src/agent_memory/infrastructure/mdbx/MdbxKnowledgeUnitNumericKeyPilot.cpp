#include "MdbxKnowledgeUnitNumericKeyPilot.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kSchemaKey = "@schema_version";
constexpr std::string_view kSchemaVersion = "knowledge_unit_numeric_key.v1";
constexpr std::string_view kUsedMarker = "used";

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

bool has_any_row(
    mdbxc::KeyValueTable<std::uint64_t, std::string>& table,
    const mdbxc::Transaction& transaction
) {
    bool present = false;
    table.for_each_range(
        0U,
        std::numeric_limits<std::uint64_t>::max(),
        [&present](const std::uint64_t, const std::string&) {
            present = true;
            return false;
        },
        transaction
    );
    return present;
}

void require_used_marker(
    const std::optional<std::string>& marker,
    const char* invalid_message
) {
    if(!marker) {
        throw std::runtime_error("numeric knowledge-unit ledger is missing");
    }
    if(*marker != kUsedMarker) {
        throw std::runtime_error(invalid_message);
    }
}

} // namespace

class MdbxKnowledgeUnitNumericKeyPilot::Impl final {
  public:
    explicit Impl(MdbxKnowledgeUnitNumericKeyPilotOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(std::move(options.path),
                                                             options.relative_to_exe,
                                                             options.max_dbs)),
          records(context->connection(),
                  table_name(options.table_prefix, "knowledge_units_numeric_v1")),
          used_ids(context->connection(),
                   table_name(options.table_prefix, "knowledge_units_numeric_v1_used")),
          metadata(context->connection(),
                   table_name(options.table_prefix, "knowledge_units_numeric_v1_meta")) {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto marker = metadata.find(std::string{kSchemaKey}, txn);
        if(marker && *marker != kSchemaVersion) {
            txn.rollback();
            throw std::runtime_error("unsupported numeric knowledge-unit pilot schema");
        }
        if(!marker) {
            if(has_any_row(records, txn) || has_any_row(used_ids, txn)) {
                txn.rollback();
                throw std::runtime_error(
                    "numeric knowledge-unit pilot rejects markerless non-empty tables"
                );
            }
            metadata.insert_or_assign(
                std::string{kSchemaKey}, std::string{kSchemaVersion}, txn);
        }
        txn.commit();
    }

    std::shared_ptr<MdbxStorageContext> context;
    mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::uint64_t, std::string> records;
    mdbxc::KeyValueTable<std::uint64_t, std::string> used_ids;
    mdbxc::KeyValueTable<std::string, std::string> metadata;
};

MdbxKnowledgeUnitNumericKeyPilot::MdbxKnowledgeUnitNumericKeyPilot(
    MdbxKnowledgeUnitNumericKeyPilotOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxKnowledgeUnitNumericKeyPilot::~MdbxKnowledgeUnitNumericKeyPilot() = default;

void MdbxKnowledgeUnitNumericKeyPilot::put(
    KnowledgeUnitId id, std::string_view payload) {
    if(id.empty()) {
        throw std::invalid_argument("numeric knowledge-unit pilot reserves id zero");
    }
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto key = id.value();
    const auto current = m_impl->records.find(key, txn);
    const auto ledger = m_impl->used_ids.find(key, txn);
    if(ledger && *ledger != kUsedMarker) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit ledger marker is invalid");
    }
    if(ledger && !current) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit id reuse is forbidden");
    }
    if(current && !ledger) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit ledger is missing");
    }
    m_impl->records.insert_or_assign(key, std::string{payload}, txn);
    if(!ledger) m_impl->used_ids.insert_or_assign(key, std::string{kUsedMarker}, txn);
    txn.commit();
}

std::optional<std::string> MdbxKnowledgeUnitNumericKeyPilot::find(
    KnowledgeUnitId id) const {
    if(id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto payload = m_impl->records.find(id.value(), txn);
    const auto ledger = m_impl->used_ids.find(id.value(), txn);
    if(ledger && *ledger != kUsedMarker) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit ledger marker is invalid");
    }
    if(payload && !ledger) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit ledger is missing");
    }
    txn.commit();
    return payload;
}

bool MdbxKnowledgeUnitNumericKeyPilot::erase(KnowledgeUnitId id) {
    if(id.empty()) return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto ledger = m_impl->used_ids.find(id.value(), txn);
    if(ledger && *ledger != kUsedMarker) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit ledger marker is invalid");
    }
    const auto payload = m_impl->records.find(id.value(), txn);
    if(!payload) {
        txn.rollback();
        return false;
    }
    if(!ledger) {
        txn.rollback();
        throw std::runtime_error("numeric knowledge-unit ledger is missing");
    }
    m_impl->records.erase(id.value(), txn);
    txn.commit();
    return true;
}

std::vector<KnowledgeUnitId> MdbxKnowledgeUnitNumericKeyPilot::scan(
    KnowledgeUnitId first, KnowledgeUnitId last, std::size_t limit) const {
    if(first.empty() || last.empty() || first.value() > last.value() || limit == 0U) {
        return {};
    }
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    std::vector<KnowledgeUnitId> result;
    result.reserve(std::min<std::size_t>(limit, 64U));
    m_impl->records.for_each_range(
        first.value(), last.value(),
        [&result, limit](const std::uint64_t key, const std::string&) {
            if(result.size() >= limit) return false;
            result.emplace_back(key);
            return result.size() < limit;
        }, txn);
    for(const auto id : result) {
        try {
            require_used_marker(
                m_impl->used_ids.find(id.value(), txn),
                "numeric knowledge-unit ledger marker is invalid"
            );
        } catch(...) {
            txn.rollback();
            throw;
        }
    }
    txn.commit();
    return result;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
