#include "MdbxResourceArtifactBindingStore.hpp"
#include "MdbxStorageErrors.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <cctype>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

std::string sanitize_table_part(std::string value) {
    if(value.empty()) return "agent_memory";
    for(char& c : value) {
        if(!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    }
    return value;
}

std::string table_name(const std::string& prefix) {
    return sanitize_table_part(prefix) + "_resource_artifact_bindings";
}

void append_size(std::string& out, std::size_t value) {
    out += std::to_string(value);
    out.push_back(':');
}

void append_string(std::string& out, std::string_view value) {
    append_size(out, value.size());
    out.append(value.data(), value.size());
}

void append_u64(std::string& out, std::uint64_t value) {
    append_string(out, std::to_string(value));
}

void append_digest(std::string& out, const ResourceBodyDigest& digest) {
    if(digest.algorithm != ResourceBodyDigestAlgorithm::Sha256) {
        throw std::invalid_argument("unsupported resource body digest algorithm");
    }
    append_string(out, "sha256");
    std::string bytes;
    bytes.reserve(digest.bytes.size());
    for(const auto byte : digest.bytes) bytes.push_back(static_cast<char>(byte));
    append_string(out, bytes);
}

class Reader final {
public:
    explicit Reader(std::string_view value) : m_value(value) {}

    std::string string() {
        std::size_t size = 0;
        bool digit = false;
        while(m_position < m_value.size() && m_value[m_position] != ':') {
            const auto c = static_cast<unsigned char>(m_value[m_position++]);
            if(c < '0' || c > '9') throw MdbxPersistedMetadataError("invalid binding length");
            const auto d = static_cast<std::size_t>(c - '0');
            if(size > ((std::numeric_limits<std::size_t>::max)() - d) / 10U)
                throw MdbxPersistedMetadataError("binding length overflow");
            size = size * 10U + d;
            digit = true;
        }
        if(!digit || m_position == m_value.size())
            throw MdbxPersistedMetadataError("binding length delimiter missing");
        ++m_position;
        if(size > m_value.size() - m_position)
            throw MdbxPersistedMetadataError("binding field exceeds payload");
        std::string result{m_value.data() + m_position, size};
        m_position += size;
        return result;
    }

    std::uint64_t u64() {
        const auto value = string();
        if(value.empty()) throw MdbxPersistedMetadataError("empty binding integer");
        std::uint64_t result = 0;
        for(const auto c : value) {
            if(c < '0' || c > '9') throw MdbxPersistedMetadataError("invalid binding integer");
            const auto digit = static_cast<std::uint64_t>(c - '0');
            if(result > ((std::numeric_limits<std::uint64_t>::max)() - digit) / 10U)
                throw MdbxPersistedMetadataError("binding integer overflow");
            result = result * 10U + digit;
        }
        return result;
    }

    void end() const {
        if(m_position != m_value.size()) throw MdbxPersistedMetadataError("binding payload has trailing data");
    }

private:
    std::string_view m_value;
    std::size_t m_position = 0;
};

std::string key_for(const ResourceRevisionRef& revision) {
    if(revision.resource_id.empty() || revision.generation == 0 ||
       !is_valid_resource_body_digest(revision.body_digest))
        throw std::invalid_argument("resource artifact binding revision is incomplete");
    std::string key;
    append_string(key, revision.resource_id.value());
    append_u64(key, revision.generation);
    append_digest(key, revision.body_digest);
    return key;
}

std::string encode(const ResourceArtifactBinding& binding) {
    std::string value;
    append_string(value, binding.source_revision_id.value());
    append_string(value, binding.artifact_binding_id.value());
    return value;
}

ResourceArtifactBinding decode(
    const ResourceRevisionRef& revision,
    std::string_view payload
) {
    Reader reader{payload};
    ResourceArtifactBinding result{
        revision,
        SourceRevisionId{reader.string()},
        ArtifactBindingId{reader.string()}
    };
    reader.end();
    if(!is_valid_resource_artifact_binding(result))
        throw MdbxPersistedMetadataError("invalid persisted resource artifact binding");
    return result;
}

} // namespace

class MdbxResourceArtifactBindingStore::Impl final {
public:
    explicit Impl(MdbxResourceArtifactBindingStoreOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(
                                        options.path,
                                        options.relative_to_exe,
                                        options.max_dbs)),
          table(context->connection(), table_name(options.table_prefix)) {}

    std::shared_ptr<MdbxStorageContext> context;
    mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::string, std::string> table;
};

MdbxResourceArtifactBindingStore::MdbxResourceArtifactBindingStore(
    MdbxResourceArtifactBindingStoreOptions options
) : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxResourceArtifactBindingStore::~MdbxResourceArtifactBindingStore() = default;

void MdbxResourceArtifactBindingStore::put(ResourceArtifactBinding binding) {
    if(!is_valid_resource_artifact_binding(binding))
        throw std::invalid_argument("resource artifact binding is incomplete");
    const auto key = key_for(binding.resource_revision);
    const auto value = encode(binding);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto existing = m_impl->table.find(key, txn);
    if(existing) {
        const auto decoded = decode(binding.resource_revision, *existing);
        if(!(decoded == binding)) {
            txn.rollback();
            throw std::runtime_error("resource revision already has another artifact binding");
        }
        txn.commit();
        return;
    }
    m_impl->table.insert_or_assign(key, value, txn);
    txn.commit();
}

std::optional<ResourceArtifactBinding>
MdbxResourceArtifactBindingStore::find(const ResourceRevisionRef& revision) const {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto value = m_impl->table.find(key_for(revision), txn);
    txn.commit();
    if(!value) return std::nullopt;
    return decode(revision, *value);
}

std::optional<ResourceArtifactBinding>
MdbxResourceArtifactBindingStore::find_in_transaction(
    const ResourceRevisionRef& revision,
    const mdbxc::Transaction& transaction
) const {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto value = m_impl->table.find(key_for(revision), transaction);
    if(!value) return std::nullopt;
    return decode(revision, *value);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
