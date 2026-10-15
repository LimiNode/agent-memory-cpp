#include "MdbxDurableLexicalDocumentMemory.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/lexical/ExactLexicalIndex.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <limits>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kEntryKeyPrefix = "entry";
constexpr std::string_view kPendingKeyPrefix = "pending";
constexpr std::string_view kScopeVersionKeyPrefix = "scope-version";
constexpr std::string_view kLegacyCatalogKey = "entries";
constexpr std::string_view kEntryPayloadVersion =
    "agent_memory.durable_lexical_entry.v2";
constexpr std::string_view kPendingPayloadVersionV2 =
    "agent_memory.durable_lexical_pending.v2";
constexpr std::string_view kPendingPayloadVersionV3 =
    "agent_memory.durable_lexical_pending.v3";
constexpr std::string_view kBodyBlockId = "body";
constexpr char kKeySeparator = '\x1f';

std::string sanitize_table_part(std::string value) {
    if(value.empty()) {
        return "agent_memory";
    }
    for(char& character : value) {
        if(!std::isalnum(static_cast<unsigned char>(character))) {
            character = '_';
        }
    }
    return value;
}

std::string table_name(const std::string& prefix, std::string_view suffix) {
    auto result = sanitize_table_part(prefix);
    result.push_back('_');
    result.append(suffix.data(), suffix.size());
    return result;
}

void reject_key_separator(std::string_view value, std::string_view field) {
    if(value.empty() || value.find(kKeySeparator) != std::string_view::npos) {
        throw std::invalid_argument(
            std::string(field) + " must be non-empty and must not contain U+001F"
        );
    }
}

void append_size(std::string& payload, std::size_t value) {
    payload += std::to_string(value);
    payload.push_back(':');
}

void append_string(std::string& payload, std::string_view value) {
    append_size(payload, value.size());
    payload.append(value.data(), value.size());
}

class PayloadReader final {
public:
    explicit PayloadReader(std::string_view payload)
        : m_payload(payload) {}

    [[nodiscard]] std::size_t read_size() {
        if(m_position >= m_payload.size()) {
            throw std::runtime_error("durable lexical catalog ended before a size");
        }

        std::size_t value = 0;
        bool has_digit = false;
        while(m_position < m_payload.size() && m_payload[m_position] != ':') {
            const auto character = static_cast<unsigned char>(m_payload[m_position]);
            if(!std::isdigit(character)) {
                throw std::runtime_error("durable lexical catalog contains an invalid size");
            }
            const auto digit = static_cast<std::size_t>(character - '0');
            if(value > (std::numeric_limits<std::size_t>::max() - digit) / 10U) {
                throw std::runtime_error("durable lexical catalog size overflow");
            }
            value = value * 10U + digit;
            has_digit = true;
            ++m_position;
        }

        if(!has_digit || m_position >= m_payload.size()) {
            throw std::runtime_error("durable lexical catalog is missing a size delimiter");
        }
        ++m_position;
        return value;
    }

    [[nodiscard]] std::uint64_t read_u64() {
        const auto text = read_string();
        if(text.empty()) {
            throw std::runtime_error("durable lexical catalog contains an empty integer");
        }
        std::uint64_t value = 0;
        for(const auto character : text) {
            if(character < '0' || character > '9') {
                throw std::runtime_error("durable lexical catalog contains an invalid integer");
            }
            const auto digit = static_cast<std::uint64_t>(character - '0');
            if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
                throw std::runtime_error("durable lexical catalog integer overflow");
            }
            value = value * 10U + digit;
        }
        return value;
    }

    [[nodiscard]] std::string read_string() {
        const auto size = read_size();
        if(size > m_payload.size() - m_position) {
            throw std::runtime_error("durable lexical catalog string exceeds payload");
        }
        std::string value{m_payload.data() + m_position, size};
        m_position += size;
        return value;
    }

    void require_end() const {
        if(m_position != m_payload.size()) {
            throw std::runtime_error("durable lexical catalog contains trailing data");
        }
    }

private:
    std::string_view m_payload;
    std::size_t m_position = 0;
};

struct CatalogEntry final {
    ScopeId scope_id;
    ResourceId resource_id;
    DocumentId document_id;
    std::string source_uri;
    std::uint64_t canonical_revision = 0;
};

struct PendingOperation final {
    ScopeId scope_id;
    ResourceId resource_id;
    DocumentId document_id;
    std::string source_uri;
    std::string utf8_text;
    std::optional<DurableLexicalPublicationGuard> guard;
};

enum class PublicationStatus {
    Published,
    Contended,
    Fenced
};

struct PublicationResult final {
    PublicationStatus status = PublicationStatus::Contended;
    std::uint64_t scope_version = 0;
};

using EntriesByResource = std::map<ResourceId, CatalogEntry>;
using EntriesByScope = std::map<ScopeId, EntriesByResource>;

std::string logical_key(const ScopeId& scope_id, const ResourceId& resource_id) {
    return scope_id.value() + std::string(1, kKeySeparator) + resource_id.value();
}

std::string record_key(
    std::string_view prefix,
    const ScopeId& scope_id,
    const ResourceId& resource_id
) {
    return std::string{prefix} + std::string(1, kKeySeparator) +
        logical_key(scope_id, resource_id);
}

std::string entry_key(const ScopeId& scope_id, const ResourceId& resource_id) {
    return record_key(kEntryKeyPrefix, scope_id, resource_id);
}

std::string pending_key(const ScopeId& scope_id, const ResourceId& resource_id) {
    return record_key(kPendingKeyPrefix, scope_id, resource_id);
}

std::string scope_version_key(const ScopeId& scope_id) {
    return std::string{kScopeVersionKeyPrefix} + std::string(1, kKeySeparator) +
        scope_id.value();
}

bool has_key_prefix(std::string_view key, std::string_view prefix) {
    return key.size() > prefix.size() &&
        key.compare(0, prefix.size(), prefix) == 0 &&
        key[prefix.size()] == kKeySeparator;
}

std::string encode_document_component(std::string_view value) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string encoded;
    encoded.reserve(value.size() * 2U);
    for(const auto byte : value) {
        const auto octet = static_cast<unsigned char>(byte);
        encoded.push_back(digits[octet >> 4U]);
        encoded.push_back(digits[octet & 0x0fU]);
    }
    return encoded;
}

std::string document_key(const ScopeId& scope_id, const ResourceId& resource_id) {
    // CanonicalContentStore reserves U+001F in document identifiers. Hex
    // encoding keeps the durable identity deterministic without leaking that
    // storage separator into the backend-neutral DocumentId.
    return "durable-lexical:" + encode_document_component(scope_id.value()) +
           ':' + encode_document_component(resource_id.value());
}

ChunkId chunk_id(const ScopeId& scope_id, const ResourceId& resource_id) {
    return ChunkId{
        logical_key(scope_id, resource_id) + std::string(1, kKeySeparator) + "chunk"
    };
}

void validate_identity(
    const ScopeId& scope_id,
    const ResourceId& resource_id,
    const DocumentId& document_id
) {
    reject_key_separator(scope_id.value(), "scope id");
    reject_key_separator(resource_id.value(), "resource id");
    reject_key_separator(document_id.value(), "document id");
    if(document_id.value() != document_key(scope_id, resource_id)) {
        throw std::runtime_error("durable lexical catalog contains an invalid document identity");
    }
}

std::string serialize_entry(const CatalogEntry& entry) {
    std::string payload;
    append_string(payload, kEntryPayloadVersion);
    append_string(payload, entry.scope_id.value());
    append_string(payload, entry.resource_id.value());
    append_string(payload, entry.document_id.value());
    append_string(payload, entry.source_uri);
    append_string(payload, std::to_string(entry.canonical_revision));
    return payload;
}

CatalogEntry deserialize_entry(std::string_view payload) {
    PayloadReader reader{payload};
    if(reader.read_string() != kEntryPayloadVersion) {
        throw std::runtime_error("unsupported durable lexical entry payload version");
    }
    CatalogEntry entry{
        ScopeId{reader.read_string()},
        ResourceId{reader.read_string()},
        DocumentId{reader.read_string()},
        reader.read_string(),
        reader.read_u64()
    };
    reader.require_end();
    validate_identity(entry.scope_id, entry.resource_id, entry.document_id);
    if(entry.source_uri.empty()) {
        throw std::runtime_error("durable lexical catalog contains an empty source URI");
    }
    return entry;
}

std::string serialize_pending(const PendingOperation& operation) {
    std::string payload;
    append_string(
        payload,
        operation.guard ? kPendingPayloadVersionV3 : kPendingPayloadVersionV2
    );
    append_string(payload, operation.scope_id.value());
    append_string(payload, operation.resource_id.value());
    append_string(payload, operation.document_id.value());
    append_string(payload, operation.source_uri);
    append_string(payload, operation.utf8_text);
    if(operation.guard) {
        append_string(payload, operation.guard->table_name);
        append_string(payload, operation.guard->key);
        append_string(payload, operation.guard->expected_value);
    }
    return payload;
}

PendingOperation deserialize_pending(std::string_view payload) {
    PayloadReader reader{payload};
    const auto version = reader.read_string();
    if(version != kPendingPayloadVersionV2 && version != kPendingPayloadVersionV3) {
        throw std::runtime_error("unsupported durable lexical pending payload version");
    }
    PendingOperation operation{
        ScopeId{reader.read_string()},
        ResourceId{reader.read_string()},
        DocumentId{reader.read_string()},
        reader.read_string(),
        reader.read_string(),
        std::nullopt
    };
    if(version == kPendingPayloadVersionV3) {
        operation.guard = DurableLexicalPublicationGuard{
            reader.read_string(),
            reader.read_string(),
            reader.read_string()
        };
        if(operation.guard->table_name.empty() || operation.guard->key.empty()) {
            throw std::runtime_error("durable lexical publication guard is incomplete");
        }
    }
    reader.require_end();
    validate_identity(
        operation.scope_id,
        operation.resource_id,
        operation.document_id
    );
    if(operation.source_uri.empty() || operation.utf8_text.empty()) {
        throw std::runtime_error("durable lexical pending operation is incomplete");
    }
    return operation;
}

std::uint64_t parse_u64(std::string_view text, std::string_view field) {
    if(text.empty()) {
        throw std::runtime_error(std::string(field) + " is empty");
    }
    std::uint64_t value = 0;
    for(const auto character : text) {
        if(character < '0' || character > '9') {
            throw std::runtime_error(std::string(field) + " is invalid");
        }
        const auto digit = static_cast<std::uint64_t>(character - '0');
        if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
            throw std::runtime_error(std::string(field) + " overflow");
        }
        value = value * 10U + digit;
    }
    return value;
}

std::string block_text(const CanonicalDocumentRevision& revision) {
    if(revision.blocks.size() != 1 ||
       revision.blocks.front().id.value() != kBodyBlockId) {
        throw std::runtime_error(
            "durable lexical document has an invalid body block topology"
        );
    }
    return revision.blocks.front().text;
}

std::optional<LexicalDocumentRecord> make_lexical_record(
    const CatalogEntry& entry,
    const CanonicalDocumentRevision& revision,
    const StandardTokenizer& tokenizer
) {
    const auto text = block_text(revision);
    const auto tokenization = tokenizer.tokenize(text);
    if(tokenization.tokens.empty()) {
        return std::nullopt;
    }

    ResourceRevision resource_revision;
    resource_revision.resource_id = entry.resource_id;
    resource_revision.generation = revision.revision + 1;
    resource_revision.pipeline_config_hash = 1;
    LexicalDocumentRecord record{
        chunk_id(entry.scope_id, entry.resource_id),
        resource_revision,
        tokenization.tokens,
        {},
        0,
        0
    };
    if(!is_valid(record)) {
        throw std::runtime_error("durable lexical document produced an invalid index record");
    }
    return record;
}

} // namespace

class MdbxDurableLexicalDocumentMemory::Impl final {
public:
    explicit Impl(MdbxDurableLexicalDocumentMemoryOptions options)
        : m_context(options.context ? std::move(options.context)
                                    : MdbxStorageContext::open(
                                          options.path,
                                          options.relative_to_exe,
                                          options.max_dbs))
        , m_catalog(
              m_context->connection(),
              table_name(options.table_prefix, "durable_lexical_catalog"))
        , m_canonical([&] {
            MdbxCanonicalContentStoreOptions canonical_options;
            canonical_options.table_prefix = options.table_prefix;
            canonical_options.context = m_context;
            return canonical_options;
        }())
        , m_fault_injector(std::move(options.fault_injector)) {
        reject_legacy_catalog();
        recover_pending_operations();
        m_entries = load_entries();
        rebuild_indexes();
        for(const auto& scope : m_entries) {
            m_scope_versions[scope.first] = load_scope_version(scope.first);
        }
    }

    [[nodiscard]] std::uint64_t upsert_text(
        ScopeId scope_id,
        ResourceId resource_id,
        std::string source_uri,
        std::string utf8_text
    ) {
        return upsert_text_impl(
            std::move(scope_id),
            std::move(resource_id),
            std::move(source_uri),
            std::move(utf8_text),
            std::nullopt
        );
    }

    [[nodiscard]] std::uint64_t upsert_text_guarded(
        ScopeId scope_id,
        ResourceId resource_id,
        std::string source_uri,
        std::string utf8_text,
        DurableLexicalPublicationGuard guard
    ) {
        if(guard.table_name.empty() || guard.key.empty()) {
            throw std::invalid_argument("durable lexical publication guard is incomplete");
        }
        return upsert_text_impl(
            std::move(scope_id),
            std::move(resource_id),
            std::move(source_uri),
            std::move(utf8_text),
            std::move(guard)
        );
    }

private:
    [[nodiscard]] std::uint64_t upsert_text_impl(
        ScopeId scope_id,
        ResourceId resource_id,
        std::string source_uri,
        std::string utf8_text,
        std::optional<DurableLexicalPublicationGuard> guard
    ) {
        reject_key_separator(scope_id.value(), "scope id");
        reject_key_separator(resource_id.value(), "resource id");
        if(source_uri.empty()) {
            throw std::invalid_argument("source URI must not be empty");
        }
        if(utf8_text.empty()) {
            throw std::invalid_argument("UTF-8 body must not be empty");
        }

        const auto requested_document_id = DocumentId{
            document_key(scope_id, resource_id)
        };
        const PendingOperation requested{
            std::move(scope_id),
            std::move(resource_id),
            requested_document_id,
            std::move(source_uri),
            std::move(utf8_text),
            std::move(guard)
        };

        std::lock_guard<std::mutex> lock(m_mutex);
        synchronize_scope(requested.scope_id);
        const auto observed_scope_version = [&] {
            const auto local = m_scope_versions.find(requested.scope_id);
            return local == m_scope_versions.end() ? std::uint64_t{0} : local->second;
        }();
        const auto requested_payload = serialize_pending(requested);

        for(;;) {
            const auto existing = acquire_pending(requested, requested_payload);
            if(existing) {
                complete_operation(deserialize_pending(*existing), *existing, false);
                continue;
            }

            const auto revision = ensure_canonical(requested);
            inject_fault(DurableLexicalDocumentMemoryFaultPoint::AfterCanonicalWrite);
            inject_fault(DurableLexicalDocumentMemoryFaultPoint::BeforeCatalogPublication);
            const auto publication = publish_operation(
                requested,
                requested_payload,
                revision.revision
            );
            if(publication.status == PublicationStatus::Contended) {
                continue;
            }
            if(publication.status == PublicationStatus::Fenced) {
                throw std::runtime_error(
                    "durable lexical publication fence rejected the operation"
                );
            }
            const auto published_version = publication.scope_version;

            try {
                inject_fault(DurableLexicalDocumentMemoryFaultPoint::AfterCatalogPublication);
                CatalogEntry entry{
                    requested.scope_id,
                    requested.resource_id,
                    requested.document_id,
                    requested.source_uri,
                    revision.revision
                };
                m_entries[entry.scope_id][entry.resource_id] = entry;
                update_index_entry(entry, revision);
                m_scope_versions[entry.scope_id] = published_version;

                const auto scope_changed_during_upsert =
                    observed_scope_version == std::numeric_limits<std::uint64_t>::max() ||
                    published_version != observed_scope_version + 1;
                if(scope_changed_during_upsert) {
                    // A different instance may have published between our initial
                    // synchronization and publication. Reconcile the complete scope
                    // before reporting a current local snapshot.
                    m_scope_versions.erase(entry.scope_id);
                    synchronize_scope(entry.scope_id);
                }
                return revision.revision + 1;
            } catch(...) {
                // The durable catalog is already committed. Force the next read or
                // retry to rebuild local state instead of trusting a partial index.
                m_scope_versions.erase(requested.scope_id);
                throw;
            }
        }
    }

public:
    [[nodiscard]] std::vector<DurableLexicalDocumentHit> search(
        const ScopeId& scope_id,
        std::string_view query,
        std::size_t limit
    ) const {
        if(query.empty()) {
            throw std::invalid_argument("lexical query must not be empty");
        }
        if(limit == 0) {
            throw std::invalid_argument("lexical query limit must not be zero");
        }

        std::lock_guard<std::mutex> lock(m_mutex);
        synchronize_scope(scope_id);
        const auto index_it = m_indexes.find(scope_id);
        if(index_it == m_indexes.end()) {
            return {};
        }
        const auto tokenization = m_tokenizer.tokenize(query);
        if(tokenization.empty()) {
            return {};
        }
        LexicalSearchQuery lexical_query;
        lexical_query.limit = limit;
        for(const auto& token : tokenization.tokens) {
            lexical_query.terms.push_back(token.text);
        }

        const auto lexical_hits = index_it->second.search(lexical_query);
        const auto scope_entries = m_entries.find(scope_id);
        if(scope_entries == m_entries.end()) {
            return {};
        }

        std::vector<DurableLexicalDocumentHit> hits;
        hits.reserve(lexical_hits.size());
        for(const auto& lexical_hit : lexical_hits) {
            const auto resource_it = std::find_if(
                scope_entries->second.begin(),
                scope_entries->second.end(),
                [&](const auto& item) {
                    return chunk_id(scope_id, item.first) == lexical_hit.chunk_id;
                }
            );
            if(resource_it == scope_entries->second.end()) {
                throw std::runtime_error("lexical index returned an unregistered chunk");
            }
            const auto& entry = resource_it->second;
            const auto revision = m_canonical.read_revision(
                entry.document_id,
                entry.canonical_revision
            );
            if(!revision) {
                throw std::runtime_error("lexical catalog entry lost its canonical revision");
            }
            hits.push_back(DurableLexicalDocumentHit{
                scope_id,
                entry.resource_id,
                lexical_hit.chunk_id,
                entry.source_uri,
                block_text(*revision),
                revision->revision + 1,
                lexical_hit.score
            });
        }
        return hits;
    }

private:
    void reject_legacy_catalog() const {
        const auto legacy_payload = m_catalog.find(std::string{kLegacyCatalogKey});
        if(!legacy_payload) {
            return;
        }
        throw std::runtime_error(
            "durable lexical catalog v1 is unsupported: canonical revision binding is "
            "unverified; export and explicitly migrate the legacy data before opening"
        );
    }

    [[nodiscard]] EntriesByScope load_entries() const {
        EntriesByScope entries;
        const auto records = m_catalog.retrieve_all<std::map>();
        for(const auto& record : records) {
            if(!has_key_prefix(record.first, kEntryKeyPrefix)) {
                continue;
            }
            auto entry = deserialize_entry(record.second);
            if(record.first != entry_key(entry.scope_id, entry.resource_id)) {
                throw std::runtime_error("durable lexical entry key does not match its payload");
            }
            auto& resources = entries[entry.scope_id];
            if(!resources.emplace(entry.resource_id, std::move(entry)).second) {
                throw std::runtime_error("durable lexical catalog contains a duplicate resource");
            }
        }
        return entries;
    }

    [[nodiscard]] EntriesByResource load_scope_entries(const ScopeId& scope_id) const {
        EntriesByResource entries;
        const auto records = m_catalog.retrieve_all<std::map>();
        for(const auto& record : records) {
            if(!has_key_prefix(record.first, kEntryKeyPrefix)) {
                continue;
            }
            auto entry = deserialize_entry(record.second);
            if(record.first != entry_key(entry.scope_id, entry.resource_id)) {
                throw std::runtime_error("durable lexical entry key does not match its payload");
            }
            if(entry.scope_id == scope_id &&
               !entries.emplace(entry.resource_id, std::move(entry)).second) {
                throw std::runtime_error("durable lexical catalog contains a duplicate resource");
            }
        }
        return entries;
    }

    [[nodiscard]] std::uint64_t load_scope_version(const ScopeId& scope_id) const {
        const auto stored = m_catalog.find(scope_version_key(scope_id));
        return stored ? parse_u64(*stored, "durable lexical scope version") : 0;
    }

    [[nodiscard]] std::optional<std::string> acquire_pending(
        const PendingOperation& operation,
        const std::string& payload
    ) {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto key = pending_key(operation.scope_id, operation.resource_id);
        if(m_catalog.insert(key, payload, txn)) {
            txn.commit();
            return std::nullopt;
        }
        const auto existing = m_catalog.find(key, txn);
        txn.commit();
        if(!existing) {
            throw std::runtime_error("durable lexical pending operation disappeared");
        }
        return existing;
    }

    [[nodiscard]] CanonicalDocumentRevision ensure_canonical(
        const PendingOperation& operation
    ) {
        for(;;) {
            const auto stored = m_canonical.read_current(operation.document_id);
            if(!stored) {
                CanonicalDocumentRevision initial{
                    operation.document_id,
                    0,
                    {},
                    {ContentBlock{
                        ContentBlockId{std::string{kBodyBlockId}},
                        1,
                        ContentBlockKind::Paragraph,
                        std::nullopt,
                        operation.utf8_text
                    }}
                };
                if(m_canonical.create_document(initial)) {
                    return initial;
                }
                continue;
            }

            if(block_text(*stored) == operation.utf8_text) {
                return *stored;
            }
            const auto result = m_canonical.commit({
                operation.document_id,
                stored->revision,
                {ReplaceBlockText{
                    ContentBlockId{std::string{kBodyBlockId}},
                    operation.utf8_text
                }}
            });
            if(result.succeeded() && result.revision) {
                return *result.revision;
            }
            if(result.status != CanonicalEditStatus::Conflict) {
                throw std::runtime_error(
                    "durable lexical canonical publication failed: " + result.message
                );
            }
        }
    }

    [[nodiscard]] PublicationResult publish_operation(
        const PendingOperation& operation,
        const std::string& pending_payload,
        std::uint64_t canonical_revision
    ) {
        auto* guard_table = operation.guard
            ? &publication_guard_table(operation.guard->table_name)
            : nullptr;
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto pending = pending_key(operation.scope_id, operation.resource_id);
        const auto stored_pending = m_catalog.find(pending, txn);
        if(!stored_pending || *stored_pending != pending_payload) {
            txn.rollback();
            return {PublicationStatus::Contended, 0};
        }

        if(guard_table) {
            const auto current_guard = guard_table->find(operation.guard->key, txn);
            if(!current_guard || *current_guard != operation.guard->expected_value) {
                if(!m_catalog.erase(pending, txn)) {
                    txn.rollback();
                    throw std::runtime_error(
                        "fenced durable lexical operation could not clear its intent"
                    );
                }
                txn.commit();
                return {PublicationStatus::Fenced, 0};
            }
        }

        const CatalogEntry entry{
            operation.scope_id,
            operation.resource_id,
            operation.document_id,
            operation.source_uri,
            canonical_revision
        };
        m_catalog.insert_or_assign(
            entry_key(entry.scope_id, entry.resource_id),
            serialize_entry(entry),
            txn
        );

        const auto next_version = increment_scope_version(entry.scope_id, txn);
        if(!m_catalog.erase(pending, txn)) {
            throw std::runtime_error("durable lexical pending operation was not cleared");
        }
        txn.commit();
        return {PublicationStatus::Published, next_version};
    }

    mdbxc::KeyValueTable<std::string, std::string>& publication_guard_table(
        const std::string& name
    ) {
        const auto found = m_publication_guard_tables.find(name);
        if(found != m_publication_guard_tables.end()) {
            return *found->second;
        }
        auto table = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(
            m_context->connection(),
            name
        );
        auto* result = table.get();
        m_publication_guard_tables.emplace(name, std::move(table));
        return *result;
    }

    [[nodiscard]] std::uint64_t increment_scope_version(
        const ScopeId& scope_id,
        const mdbxc::Transaction& txn
    ) {
        const auto version_key = scope_version_key(scope_id);
        const auto stored_version = m_catalog.find(version_key, txn);
        const auto previous_version = stored_version
            ? parse_u64(*stored_version, "durable lexical scope version")
            : 0;
        if(previous_version == std::numeric_limits<std::uint64_t>::max()) {
            throw std::runtime_error("durable lexical scope version overflow");
        }
        const auto next_version = previous_version + 1;
        m_catalog.insert_or_assign(version_key, std::to_string(next_version), txn);
        return next_version;
    }

    void complete_operation(
        const PendingOperation& operation,
        const std::string& pending_payload,
        bool inject_faults
    ) {
        const auto revision = ensure_canonical(operation);
        if(inject_faults) {
            inject_fault(DurableLexicalDocumentMemoryFaultPoint::AfterCanonicalWrite);
            inject_fault(DurableLexicalDocumentMemoryFaultPoint::BeforeCatalogPublication);
        }
        static_cast<void>(publish_operation(
            operation,
            pending_payload,
            revision.revision
        ));
    }

    void recover_pending_operations() {
        const auto records = m_catalog.retrieve_all<std::map>();
        for(const auto& record : records) {
            if(!has_key_prefix(record.first, kPendingKeyPrefix)) {
                continue;
            }
            const auto operation = deserialize_pending(record.second);
            if(record.first != pending_key(operation.scope_id, operation.resource_id)) {
                throw std::runtime_error("durable lexical pending key does not match its payload");
            }
            complete_operation(operation, record.second, false);
        }
    }

    void inject_fault(DurableLexicalDocumentMemoryFaultPoint point) const {
        if(m_fault_injector) {
            m_fault_injector(point);
        }
    }

    void update_index_entry(
        const CatalogEntry& entry,
        const CanonicalDocumentRevision& revision
    ) const {
        // Build anything that can fail before removing the previous posting.
        const auto record = make_lexical_record(entry, revision, m_tokenizer);
        auto& index = m_indexes[entry.scope_id];
        static_cast<void>(index.erase(chunk_id(entry.scope_id, entry.resource_id)));
        if(record) {
            index.upsert(*record);
        }
    }

    void rebuild_scope(const ScopeId& scope_id) const {
        const auto scope_it = m_entries.find(scope_id);
        ExactLexicalIndex rebuilt;
        if(scope_it != m_entries.end()) {
            for(const auto& item : scope_it->second) {
                const auto& entry = item.second;
                const auto revision = m_canonical.read_revision(
                    entry.document_id,
                    entry.canonical_revision
                );
                if(!revision) {
                    throw std::runtime_error(
                        "durable lexical catalog points to a missing canonical revision"
                    );
                }
                const auto record = make_lexical_record(entry, *revision, m_tokenizer);
                if(record) {
                    rebuilt.upsert(*record);
                }
            }
        }
        m_indexes[scope_id] = std::move(rebuilt);
    }

    void rebuild_indexes() const {
        m_indexes.clear();
        for(const auto& scope : m_entries) {
            rebuild_scope(scope.first);
        }
    }

    void synchronize_scope(const ScopeId& scope_id) const {
        const auto persisted_version = load_scope_version(scope_id);
        const auto local = m_scope_versions.find(scope_id);
        if(local != m_scope_versions.end() && local->second == persisted_version) {
            return;
        }

        auto entries = load_scope_entries(scope_id);
        if(entries.empty()) {
            m_entries.erase(scope_id);
        } else {
            m_entries[scope_id] = std::move(entries);
        }
        rebuild_scope(scope_id);
        m_scope_versions[scope_id] = persisted_version;
    }

    std::shared_ptr<MdbxStorageContext> m_context;
    mutable mdbxc::KeyValueTable<std::string, std::string> m_catalog;
    MdbxCanonicalContentStore m_canonical;
    std::function<void(DurableLexicalDocumentMemoryFaultPoint)> m_fault_injector;
    mutable std::mutex m_mutex;
    mutable EntriesByScope m_entries;
    mutable std::map<ScopeId, ExactLexicalIndex> m_indexes;
    mutable std::map<ScopeId, std::uint64_t> m_scope_versions;
    std::map<
        std::string,
        std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>>
    > m_publication_guard_tables;
    StandardTokenizer m_tokenizer;
};

MdbxDurableLexicalDocumentMemory::MdbxDurableLexicalDocumentMemory(
    MdbxDurableLexicalDocumentMemoryOptions options
)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxDurableLexicalDocumentMemory::~MdbxDurableLexicalDocumentMemory() = default;

std::uint64_t MdbxDurableLexicalDocumentMemory::upsert_text(
    ScopeId scope_id,
    ResourceId resource_id,
    std::string source_uri,
    std::string utf8_text
) {
    return m_impl->upsert_text(
        std::move(scope_id),
        std::move(resource_id),
        std::move(source_uri),
        std::move(utf8_text)
    );
}

std::uint64_t MdbxDurableLexicalDocumentMemory::upsert_text_guarded(
    ScopeId scope_id,
    ResourceId resource_id,
    std::string source_uri,
    std::string utf8_text,
    DurableLexicalPublicationGuard guard
) {
    return m_impl->upsert_text_guarded(
        std::move(scope_id),
        std::move(resource_id),
        std::move(source_uri),
        std::move(utf8_text),
        std::move(guard)
    );
}

std::vector<DurableLexicalDocumentHit> MdbxDurableLexicalDocumentMemory::search(
    const ScopeId& scope_id,
    std::string_view query,
    std::size_t limit
) const {
    return m_impl->search(scope_id, query, limit);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
