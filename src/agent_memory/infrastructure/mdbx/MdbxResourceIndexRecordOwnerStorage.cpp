#include "MdbxResourceIndexRecordOwnerStorage.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <cctype>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {

    namespace {

        std::string sanitize_table_part(std::string value) {
            if(value.empty()) {
                return "agent_memory";
            }
            for(char& c : value) {
                if(!std::isalnum(static_cast<unsigned char>(c))) {
                    c = '_';
                }
            }
            return value;
        }

        std::string table_name(const std::string& prefix, std::string_view suffix) {
            return sanitize_table_part(prefix) + "_" + std::string{suffix};
        }

        void append_size(std::string& payload, std::size_t value) {
            payload += std::to_string(value);
            payload.push_back(':');
        }

        void append_string(std::string& payload, std::string_view value) {
            append_size(payload, value.size());
            payload.append(value.data(), value.size());
        }

        class OwnerPayloadReader final {
        public:
            explicit OwnerPayloadReader(std::string_view payload)
                : m_payload(payload) {}

            [[nodiscard]] std::string read_string() {
                std::size_t size = 0;
                bool has_digit = false;
                while(m_position < m_payload.size() && m_payload[m_position] != ':') {
                    const auto c = static_cast<unsigned char>(m_payload[m_position]);
                    if(!std::isdigit(c)) {
                        throw std::runtime_error("Invalid resource owner payload size");
                    }
                    const auto digit = static_cast<std::size_t>(c - '0');
                    if(size > (std::numeric_limits<std::size_t>::max() - digit) / 10) {
                        throw std::runtime_error("Resource owner payload size overflow");
                    }
                    size = size * 10 + digit;
                    has_digit = true;
                    ++m_position;
                }
                if(!has_digit || m_position == m_payload.size()) {
                    throw std::runtime_error("Invalid resource owner payload delimiter");
                }
                ++m_position;
                if(size > m_payload.size() - m_position) {
                    throw std::runtime_error("Resource owner payload exceeds available data");
                }
                std::string result{m_payload.data() + m_position, size};
                m_position += size;
                return result;
            }

            [[nodiscard]] std::uint64_t read_uint64() {
                const auto text = read_string();
                std::uint64_t value = 0;
                if(text.empty()) {
                    throw std::runtime_error("Missing resource owner integer");
                }
                for(const auto c : text) {
                    if(!std::isdigit(static_cast<unsigned char>(c))) {
                        throw std::runtime_error("Invalid resource owner integer");
                    }
                    const auto digit = static_cast<std::uint64_t>(c - '0');
                    if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
                        throw std::runtime_error("Resource owner integer overflow");
                    }
                    value = value * 10U + digit;
                }
                return value;
            }

            void require_end() const {
                if(m_position != m_payload.size()) {
                    throw std::runtime_error("Unexpected resource owner payload data");
                }
            }

        private:
            std::string_view m_payload;
            std::size_t m_position = 0;
        };

        std::string serialize_owner(const ResourceIndexRecordOwner& owner) {
            if(!is_valid_resource_index_record_owner(owner)) {
                throw std::invalid_argument("Resource index record owner is invalid");
            }
            std::string payload;
            append_string(payload, owner.resource_id.value());
            append_string(payload, std::to_string(owner.generation));
            append_string(payload, owner.manifest_schema.schema_id);
            append_string(payload, std::to_string(owner.manifest_schema.schema_version));
            return payload;
        }

        ResourceIndexRecordOwner deserialize_owner(std::string_view payload) {
            OwnerPayloadReader reader{payload};
            ResourceIndexRecordOwner owner;
            owner.resource_id = ResourceId{reader.read_string()};
            owner.generation = reader.read_uint64();
            owner.manifest_schema.schema_id = reader.read_string();
            const auto schema_version = reader.read_uint64();
            if(schema_version > std::numeric_limits<std::uint32_t>::max()) {
                throw std::runtime_error("Resource owner schema version exceeds uint32 range");
            }
            owner.manifest_schema.schema_version = static_cast<std::uint32_t>(schema_version);
            reader.require_end();
            if(!is_valid_resource_index_record_owner(owner)) {
                throw std::runtime_error("Resource owner payload is invalid");
            }
            return owner;
        }

        mdbxc::Config make_config(const MdbxResourceIndexRecordOwnerStorageOptions& options) {
            if(options.path.empty()) {
                throw std::invalid_argument("MdbxResourceIndexRecordOwnerStorageOptions::path must not be empty");
            }
            mdbxc::Config config;
            config.pathname = options.path;
            config.max_dbs = 16;
            config.no_subdir = true;
            config.relative_to_exe = options.relative_to_exe;
            return config;
        }

    } // namespace

    class MdbxResourceIndexRecordOwnerStorage::Impl final {
    public:
        explicit Impl(MdbxResourceIndexRecordOwnerStorageOptions options)
            : m_prefix(sanitize_table_part(std::move(options.table_prefix)))
            , m_connection(mdbxc::Connection::create(make_config(options)))
            , m_documents(m_connection, table_name(m_prefix, "resource_index_document_owners"))
            , m_chunks(m_connection, table_name(m_prefix, "resource_index_chunk_owners")) {}

        [[nodiscard]] std::optional<ResourceIndexRecordOwner> find_document(
            const DocumentId& document_id
        ) const {
            const auto payload = m_documents.find(document_id.value());
            return payload ? std::optional<ResourceIndexRecordOwner>{deserialize_owner(*payload)}
                           : std::nullopt;
        }

        [[nodiscard]] std::optional<ResourceIndexRecordOwner> find_chunk(
            const ChunkId& chunk_id
        ) const {
            const auto payload = m_chunks.find(chunk_id.value());
            return payload ? std::optional<ResourceIndexRecordOwner>{deserialize_owner(*payload)}
                           : std::nullopt;
        }

        void upsert_document(DocumentId document_id, ResourceIndexRecordOwner owner) {
            m_documents.insert_or_assign(document_id.value(), serialize_owner(owner));
        }

        void upsert_chunk(ChunkId chunk_id, ResourceIndexRecordOwner owner) {
            m_chunks.insert_or_assign(chunk_id.value(), serialize_owner(owner));
        }

        [[nodiscard]] bool erase_document(const DocumentId& document_id) {
            return m_documents.erase(document_id.value());
        }

        [[nodiscard]] bool erase_chunk(const ChunkId& chunk_id) {
            return m_chunks.erase(chunk_id.value());
        }

    private:
        std::string m_prefix;
        std::shared_ptr<mdbxc::Connection> m_connection;
        mdbxc::KeyValueTable<std::string, std::string> m_documents;
        mdbxc::KeyValueTable<std::string, std::string> m_chunks;
    };

    MdbxResourceIndexRecordOwnerStorage::MdbxResourceIndexRecordOwnerStorage(
        MdbxResourceIndexRecordOwnerStorageOptions options
    )
        : m_impl(std::make_unique<Impl>(std::move(options))) {}

    MdbxResourceIndexRecordOwnerStorage::~MdbxResourceIndexRecordOwnerStorage() = default;
    std::optional<ResourceIndexRecordOwner>
    MdbxResourceIndexRecordOwnerStorage::find_document_owner(
        const DocumentId& document_id
    ) const {
        return m_impl->find_document(document_id);
    }

    std::optional<ResourceIndexRecordOwner>
    MdbxResourceIndexRecordOwnerStorage::find_chunk_owner(const ChunkId& chunk_id) const {
        return m_impl->find_chunk(chunk_id);
    }

    void MdbxResourceIndexRecordOwnerStorage::upsert_document_owner(
        DocumentId document_id,
        ResourceIndexRecordOwner owner
    ) {
        m_impl->upsert_document(std::move(document_id), std::move(owner));
    }

    void MdbxResourceIndexRecordOwnerStorage::upsert_chunk_owner(
        ChunkId chunk_id,
        ResourceIndexRecordOwner owner
    ) {
        m_impl->upsert_chunk(std::move(chunk_id), std::move(owner));
    }

    bool MdbxResourceIndexRecordOwnerStorage::erase_document_owner(
        const DocumentId& document_id
    ) {
        return m_impl->erase_document(document_id);
    }

    bool MdbxResourceIndexRecordOwnerStorage::erase_chunk_owner(const ChunkId& chunk_id) {
        return m_impl->erase_chunk(chunk_id);
    }

} // namespace agent_memory

#endif
