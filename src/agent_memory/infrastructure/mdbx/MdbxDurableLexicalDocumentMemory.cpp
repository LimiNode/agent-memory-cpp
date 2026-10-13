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
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kCatalogKey = "entries";
constexpr std::string_view kCatalogPayloadVersion =
    "agent_memory.durable_lexical_catalog.v1";
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
        throw std::invalid_argument(std::string(field) + " must be non-empty and must not contain U+001F");
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
};

using EntriesByResource = std::map<ResourceId, CatalogEntry>;
using EntriesByScope = std::map<ScopeId, EntriesByResource>;

std::string logical_key(const ScopeId& scope_id, const ResourceId& resource_id) {
    return scope_id.value() + std::string{1, kKeySeparator} + resource_id.value();
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
    return ChunkId{logical_key(scope_id, resource_id) + std::string{1, kKeySeparator} + "chunk"};
}

std::string serialize_catalog(const EntriesByScope& entries) {
    std::string payload;
    append_string(payload, kCatalogPayloadVersion);
    std::size_t entry_count = 0;
    for(const auto& scope : entries) {
        entry_count += scope.second.size();
    }
    append_size(payload, entry_count);
    for(const auto& scope : entries) {
        for(const auto& resource : scope.second) {
            const auto& entry = resource.second;
            append_string(payload, entry.scope_id.value());
            append_string(payload, entry.resource_id.value());
            append_string(payload, entry.document_id.value());
            append_string(payload, entry.source_uri);
        }
    }
    return payload;
}

EntriesByScope deserialize_catalog(std::string_view payload) {
    PayloadReader reader{payload};
    if(reader.read_string() != kCatalogPayloadVersion) {
        throw std::runtime_error("unsupported durable lexical catalog payload version");
    }

    EntriesByScope entries;
    const auto count = reader.read_size();
    for(std::size_t index = 0; index < count; ++index) {
        CatalogEntry entry{
            ScopeId{reader.read_string()},
            ResourceId{reader.read_string()},
            DocumentId{reader.read_string()},
            reader.read_string()
        };
        reject_key_separator(entry.scope_id.value(), "scope id");
        reject_key_separator(entry.resource_id.value(), "resource id");
        reject_key_separator(entry.document_id.value(), "document id");
        if(entry.source_uri.empty()) {
            throw std::runtime_error("durable lexical catalog contains an empty source URI");
        }
        const auto scope_inserted = entries.emplace(entry.scope_id, EntriesByResource{});
        if(!scope_inserted.first->second.emplace(entry.resource_id, std::move(entry)).second) {
            throw std::runtime_error("durable lexical catalog contains a duplicate resource");
        }
    }
    reader.require_end();
    return entries;
}

std::string block_text(const CanonicalDocumentRevision& revision) {
    if(revision.blocks.size() != 1 || revision.blocks.front().id.value() != kBodyBlockId) {
        throw std::runtime_error("durable lexical document has an invalid body block topology");
    }
    return revision.blocks.front().text;
}

LexicalDocumentRecord make_lexical_record(
    const ScopeId& scope_id,
    const ResourceId& resource_id,
    const CanonicalDocumentRevision& revision,
    const StandardTokenizer& tokenizer
) {
    const auto text = block_text(revision);
    const auto tokenization = tokenizer.tokenize(text);
    ResourceRevision resource_revision;
    resource_revision.resource_id = resource_id;
    resource_revision.generation = revision.revision + 1;
    resource_revision.pipeline_config_hash = 1;
    LexicalDocumentRecord record{
        chunk_id(scope_id, resource_id),
        resource_revision,
        tokenization.tokens,
        {},
        0,
        0
    };
    if(!is_valid(record)) {
        throw std::runtime_error("durable lexical document produced no searchable tokens");
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
        , m_catalog(m_context->connection(), table_name(options.table_prefix, "durable_lexical_catalog"))
        , m_canonical([&] {
            MdbxCanonicalContentStoreOptions canonical_options;
            canonical_options.table_prefix = options.table_prefix;
            canonical_options.context = m_context;
            return canonical_options;
        }())
        , m_entries(load_entries()) {
        rebuild_indexes();
    }

    [[nodiscard]] std::uint64_t upsert_text(
        ScopeId scope_id,
        ResourceId resource_id,
        std::string source_uri,
        std::string utf8_text
    ) {
        reject_key_separator(scope_id.value(), "scope id");
        reject_key_separator(resource_id.value(), "resource id");
        if(source_uri.empty()) {
            throw std::invalid_argument("source URI must not be empty");
        }
        if(utf8_text.empty()) {
            throw std::invalid_argument("UTF-8 body must not be empty");
        }

        auto scope_it = m_entries.find(scope_id);
        if(scope_it == m_entries.end()) {
            scope_it = m_entries.emplace(scope_id, EntriesByResource{}).first;
        }
        auto resource_it = scope_it->second.find(resource_id);
        const auto document_id = resource_it == scope_it->second.end()
            ? DocumentId{document_key(scope_id, resource_id)}
            : resource_it->second.document_id;

        CanonicalDocumentRevision current;
        if(resource_it == scope_it->second.end()) {
            current = CanonicalDocumentRevision{
                document_id,
                0,
                {},
                {ContentBlock{
                    ContentBlockId{std::string{kBodyBlockId}},
                    1,
                    ContentBlockKind::Paragraph,
                    std::nullopt,
                    utf8_text
                }}
            };
            if(!m_canonical.create_document(current)) {
                throw std::runtime_error("durable lexical document already exists");
            }
        } else {
            const auto stored = m_canonical.read_current(document_id);
            if(!stored) {
                throw std::runtime_error("durable lexical catalog points to a missing canonical document");
            }
            current = *stored;
            if(block_text(current) != utf8_text) {
                const auto result = m_canonical.commit({
                    document_id,
                    current.revision,
                    {ReplaceBlockText{ContentBlockId{std::string{kBodyBlockId}}, utf8_text}}
                });
                if(!result.succeeded() || !result.revision) {
                    throw std::runtime_error("durable lexical document update was not published");
                }
                current = *result.revision;
            }
        }

        CatalogEntry entry{scope_id, resource_id, document_id, std::move(source_uri)};
        scope_it->second[resource_id] = entry;
        persist_entries();
        rebuild_scope(scope_id);
        return current.revision + 1;
    }

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
            const auto current = m_canonical.read_current(resource_it->second.document_id);
            if(!current) {
                throw std::runtime_error("lexical catalog entry lost its canonical body");
            }
            hits.push_back(DurableLexicalDocumentHit{
                scope_id,
                resource_it->second.resource_id,
                lexical_hit.chunk_id,
                resource_it->second.source_uri,
                block_text(*current),
                current->revision + 1,
                lexical_hit.score
            });
        }
        return hits;
    }

private:
    EntriesByScope load_entries() const {
        const auto payload = m_catalog.find(std::string{kCatalogKey});
        return payload ? deserialize_catalog(*payload) : EntriesByScope{};
    }

    void persist_entries() {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        m_catalog.insert_or_assign(
            std::string{kCatalogKey},
            serialize_catalog(m_entries),
            txn
        );
        txn.commit();
    }

    void rebuild_scope(const ScopeId& scope_id) {
        const auto scope_it = m_entries.find(scope_id);
        ExactLexicalIndex rebuilt;
        if(scope_it != m_entries.end()) {
            for(const auto& item : scope_it->second) {
                const auto current = m_canonical.read_current(item.second.document_id);
                if(!current) {
                    throw std::runtime_error("durable lexical catalog points to a missing canonical document");
                }
                rebuilt.upsert(make_lexical_record(
                    scope_id,
                    item.first,
                    *current,
                    m_tokenizer
                ));
            }
        }
        m_indexes[scope_id] = std::move(rebuilt);
    }

    void rebuild_indexes() {
        for(const auto& scope : m_entries) {
            rebuild_scope(scope.first);
        }
    }

    std::shared_ptr<MdbxStorageContext> m_context;
    mdbxc::KeyValueTable<std::string, std::string> m_catalog;
    MdbxCanonicalContentStore m_canonical;
    EntriesByScope m_entries;
    std::map<ScopeId, ExactLexicalIndex> m_indexes;
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

std::vector<DurableLexicalDocumentHit> MdbxDurableLexicalDocumentMemory::search(
    const ScopeId& scope_id,
    std::string_view query,
    std::size_t limit
) const {
    return m_impl->search(scope_id, query, limit);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
