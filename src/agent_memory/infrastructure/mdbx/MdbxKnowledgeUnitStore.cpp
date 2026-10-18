#include "MdbxKnowledgeUnitStore.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <limits>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {

    namespace {

        constexpr std::string_view PAYLOAD_VERSION =
            "agent_memory.knowledge_unit.v1";
        constexpr std::string_view NEXT_ID_KEY = "@next_id";
        constexpr std::string_view NEXT_INDEX_SEQUENCE_KEY = "@next_index_sequence";
        constexpr std::string_view INDEX_SCHEMA_VERSION_KEY =
            "@knowledge_units_by_kind_schema_version";
        constexpr std::string_view INDEX_SCHEMA_VERSION_V1 = "1";
        constexpr std::string_view INDEX_SCHEMA_VERSION_V2 = "2";
        constexpr std::size_t MAX_SOURCE_SUMMARIES = 3;
        constexpr std::size_t MAX_SOURCE_PREVIEW_BYTES = 256;

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

        std::string table_name(
            const std::string& prefix,
            std::string_view suffix
        ) {
            auto result = sanitize_table_part(prefix);
            result.push_back('_');
            result.append(suffix.data(), suffix.size());
            return result;
        }

        void append_size(std::string& payload, std::size_t value) {
            payload += std::to_string(value);
            payload.push_back(':');
        }

        void append_string(std::string& payload, std::string_view value) {
            append_size(payload, value.size());
            payload.append(value.data(), value.size());
        }

        void append_uint64(std::string& payload, std::uint64_t value) {
            append_string(payload, std::to_string(value));
        }

        void append_uint64_be(std::string& payload, std::uint64_t value) {
            for(int shift = 56; shift >= 0; shift -= 8) {
                payload.push_back(static_cast<char>((value >> shift) & 0xffU));
            }
        }

        void append_int64(std::string& payload, std::int64_t value) {
            append_string(payload, std::to_string(value));
        }

        void append_bool(std::string& payload, bool value) {
            append_string(payload, value ? "1" : "0");
        }

        void append_bytes(std::string& payload, const std::uint8_t* bytes, std::size_t size) {
            append_string(payload, std::string_view{
                reinterpret_cast<const char*>(bytes), size
            });
        }

        class PayloadReader final {
        public:
            explicit PayloadReader(std::string_view payload)
                : m_payload(payload) {}

            [[nodiscard]] std::string read_string() {
                if(m_position >= m_payload.size()) {
                    throw std::runtime_error("Unexpected end of knowledge-unit payload");
                }
                std::size_t size = 0;
                bool has_digit = false;
                while(m_position < m_payload.size() && m_payload[m_position] != ':') {
                    const auto c = static_cast<unsigned char>(m_payload[m_position]);
                    if(!std::isdigit(c)) {
                        throw std::runtime_error("Invalid knowledge-unit payload size");
                    }
                    const auto digit = static_cast<std::size_t>(c - '0');
                    if(size > (std::numeric_limits<std::size_t>::max() - digit) / 10U) {
                        throw std::runtime_error("Knowledge-unit payload size overflow");
                    }
                    size = size * 10U + digit;
                    has_digit = true;
                    ++m_position;
                }
                if(!has_digit || m_position >= m_payload.size()) {
                    throw std::runtime_error("Missing knowledge-unit payload delimiter");
                }
                ++m_position;
                if(size > m_payload.size() - m_position) {
                    throw std::runtime_error("Knowledge-unit payload exceeds available data");
                }
                std::string value{m_payload.data() + m_position, size};
                m_position += size;
                return value;
            }

            [[nodiscard]] std::uint64_t read_uint64() {
                const auto text = read_string();
                if(text.empty()) {
                    throw std::runtime_error("Missing knowledge-unit uint64");
                }
                std::uint64_t value = 0;
                for(const auto c : text) {
                    if(c < '0' || c > '9') {
                        throw std::runtime_error("Invalid knowledge-unit uint64");
                    }
                    const auto digit = static_cast<std::uint64_t>(c - '0');
                    if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
                        throw std::runtime_error("Knowledge-unit uint64 overflow");
                    }
                    value = value * 10U + digit;
                }
                return value;
            }

            [[nodiscard]] std::int64_t read_int64() {
                const auto text = read_string();
                if(text.empty()) {
                    throw std::runtime_error("Missing knowledge-unit int64");
                }
                std::size_t consumed = 0;
                std::int64_t value = 0;
                try {
                    value = std::stoll(text, &consumed);
                } catch(const std::exception&) {
                    throw std::runtime_error("Invalid knowledge-unit int64");
                }
                if(consumed != text.size()) {
                    throw std::runtime_error("Invalid knowledge-unit int64");
                }
                return value;
            }

            [[nodiscard]] double read_double() {
                const auto text = read_string();
                double value = 0.0;
                std::istringstream stream{text};
                stream.imbue(std::locale::classic());
                stream >> value;
                if(stream.fail()) {
                    throw std::runtime_error("Invalid knowledge-unit double");
                }
                char trailing = 0;
                if(stream >> trailing) {
                    throw std::runtime_error("Invalid knowledge-unit double");
                }
                if(!std::isfinite(value)) {
                    throw std::runtime_error("Invalid knowledge-unit double");
                }
                return value;
            }

            [[nodiscard]] bool read_bool() {
                const auto value = read_string();
                if(value == "0") return false;
                if(value == "1") return true;
                throw std::runtime_error("Invalid knowledge-unit boolean");
            }

            void require_end() const {
                if(m_position != m_payload.size()) {
                    throw std::runtime_error("Trailing knowledge-unit payload data");
                }
            }

        private:
            std::string_view m_payload;
            std::size_t m_position = 0;
        };

        void append_hash(std::string& payload, const ContentHash& hash) {
            append_bytes(payload, hash.bytes.data(), hash.bytes.size());
        }

        ContentHash read_hash(PayloadReader& reader) {
            const auto bytes = reader.read_string();
            if(bytes.size() != ContentHash{}.bytes.size()) {
                throw std::runtime_error("Invalid knowledge-unit content hash length");
            }
            ContentHash hash;
            std::copy(bytes.begin(), bytes.end(), hash.bytes.begin());
            return hash;
        }

        void append_digest(std::string& payload, const ResourceBodyDigest& digest) {
            if(digest.algorithm != ResourceBodyDigestAlgorithm::Sha256) {
                throw std::invalid_argument("Unsupported source revision digest algorithm");
            }
            append_string(payload, "sha256");
            append_bytes(payload, digest.bytes.data(), digest.bytes.size());
        }

        ResourceBodyDigest read_digest(PayloadReader& reader) {
            if(reader.read_string() != "sha256") {
                throw std::runtime_error("Unsupported source revision digest algorithm");
            }
            const auto bytes = reader.read_string();
            if(bytes.size() != ResourceBodyDigest{}.bytes.size()) {
                throw std::runtime_error("Invalid source revision digest length");
            }
            ResourceBodyDigest digest;
            std::copy(bytes.begin(), bytes.end(), digest.bytes.begin());
            return digest;
        }

        void append_source_summary(
            std::string& payload,
            const SourceRefSummary& summary
        ) {
            append_string(payload, summary.resource_id.value());
            append_string(
                payload,
                summary.reference_mode == SourceReferenceMode::RevisionBoundQuote
                    ? "revision_bound"
                    : "legacy_preview"
            );
            append_bool(payload, summary.resource_revision.has_value());
            if(summary.resource_revision) {
                append_string(payload, summary.resource_revision->resource_id.value());
                append_uint64(payload, summary.resource_revision->generation);
                append_digest(payload, summary.resource_revision->body_digest);
            }
            append_string(payload, summary.uri);
            append_uint64(payload, summary.excerpt.offset);
            append_uint64(payload, summary.excerpt.length);
            append_bytes(payload, summary.quote_hash.data(), summary.quote_hash.size());
            std::ostringstream confidence;
            confidence.imbue(std::locale::classic());
            confidence << std::setprecision(std::numeric_limits<double>::max_digits10)
                       << summary.confidence;
            append_string(payload, confidence.str());
            append_string(
                payload,
                summary.text_origin == SourceTextOrigin::OriginalText
                    ? "original"
                    : "derived"
            );
            append_bool(payload, summary.observed_at_ms.has_value());
            if(summary.observed_at_ms) {
                append_uint64(payload, *summary.observed_at_ms);
            }
            append_string(payload, summary.preview);
        }

        bool source_summaries_equal(
            const std::vector<SourceRefSummary>& left,
            const std::vector<SourceRefSummary>& right
        ) {
            if(left.size() != right.size()) return false;
            for(std::size_t index = 0; index < left.size(); ++index) {
                std::string left_payload;
                std::string right_payload;
                append_source_summary(left_payload, left[index]);
                append_source_summary(right_payload, right[index]);
                if(left_payload != right_payload) return false;
            }
            return true;
        }

        bool has_m0_chunk_identity_source(
            const std::vector<SourceRefSummary>& sources
        ) noexcept {
            return sources.size() == 1
                && sources.front().reference_mode == SourceReferenceMode::RevisionBoundQuote
                && sources.front().resource_revision.has_value();
        }

        SourceRefSummary read_source_summary(PayloadReader& reader) {
            SourceRefSummary summary;
            summary.resource_id = ResourceId{reader.read_string()};
            const auto mode = reader.read_string();
            if(mode == "revision_bound") {
                summary.reference_mode = SourceReferenceMode::RevisionBoundQuote;
            } else if(mode == "legacy_preview") {
                summary.reference_mode = SourceReferenceMode::LegacyPreviewOnly;
            } else {
                throw std::runtime_error("Invalid source reference mode");
            }
            if(reader.read_bool()) {
                ResourceRevisionRef revision;
                revision.resource_id = ResourceId{reader.read_string()};
                revision.generation = reader.read_uint64();
                revision.body_digest = read_digest(reader);
                summary.resource_revision = std::move(revision);
            }
            summary.uri = reader.read_string();
            summary.excerpt.offset = reader.read_uint64();
            summary.excerpt.length = reader.read_uint64();
            const auto quote_hash = reader.read_string();
            if(quote_hash.size() != summary.quote_hash.size()) {
                throw std::runtime_error("Invalid source quote hash length");
            }
            std::copy(quote_hash.begin(), quote_hash.end(), summary.quote_hash.begin());
            summary.confidence = reader.read_double();
            const auto origin = reader.read_string();
            if(origin == "original") {
                summary.text_origin = SourceTextOrigin::OriginalText;
            } else if(origin == "derived") {
                summary.text_origin = SourceTextOrigin::DerivedExtraction;
            } else {
                throw std::runtime_error("Invalid source text origin");
            }
            if(reader.read_bool()) {
                summary.observed_at_ms = reader.read_uint64();
            }
            summary.preview = reader.read_string();
            return summary;
        }

        std::string lifecycle_name(KnowledgeUnitLifecycleState state) {
            switch(state) {
            case KnowledgeUnitLifecycleState::Active: return "active";
            case KnowledgeUnitLifecycleState::Superseded: return "superseded";
            case KnowledgeUnitLifecycleState::Deprecated: return "deprecated";
            case KnowledgeUnitLifecycleState::Erased: return "erased";
            }
            return {};
        }

        KnowledgeUnitLifecycleState parse_lifecycle(std::string_view value) {
            if(value == "active") return KnowledgeUnitLifecycleState::Active;
            if(value == "superseded") return KnowledgeUnitLifecycleState::Superseded;
            if(value == "deprecated") return KnowledgeUnitLifecycleState::Deprecated;
            if(value == "erased") return KnowledgeUnitLifecycleState::Erased;
            throw std::runtime_error("Invalid knowledge-unit lifecycle state");
        }

        std::string serialize_envelope(const KnowledgeUnitEnvelope& envelope) {
            std::string payload;
            append_string(payload, PAYLOAD_VERSION);
            append_uint64(payload, envelope.id.value());
            append_string(payload, to_string(envelope.kind));
            append_string(payload, envelope.scope_id.value());
            append_string(payload, envelope.primary_text);
            append_string(payload, envelope.display_text);
            append_string(payload, lifecycle_name(envelope.lifecycle_state));
            append_uint64(payload, envelope.sources.size());
            for(const auto& source : envelope.sources) {
                append_source_summary(payload, source);
            }
            append_int64(payload, envelope.created_at_ms);
            append_int64(payload, envelope.updated_at_ms);
            append_int64(payload, envelope.observed_at_ms);
            append_uint64(payload, envelope.revision);
            append_hash(payload, envelope.content_hash);
            append_uint64(payload, envelope.content_hash_recipe_version);
            std::ostringstream priority;
            priority.imbue(std::locale::classic());
            priority << std::setprecision(std::numeric_limits<double>::max_digits10)
                     << envelope.priority_weight;
            append_string(payload, priority.str());
            append_uint64(payload, envelope.supersedes.size());
            for(const auto id : envelope.supersedes) {
                append_uint64(payload, id.value());
            }
            append_bool(payload, envelope.superseded_by.has_value());
            if(envelope.superseded_by) append_uint64(payload, envelope.superseded_by->value());
            append_bool(payload, envelope.derived_from.has_value());
            if(envelope.derived_from) append_uint64(payload, envelope.derived_from->value());
            return payload;
        }

        KnowledgeUnitEnvelope deserialize_envelope(std::string_view payload) {
            PayloadReader reader{payload};
            if(reader.read_string() != PAYLOAD_VERSION) {
                throw std::runtime_error("Unsupported knowledge-unit payload version");
            }
            KnowledgeUnitEnvelope envelope;
            envelope.id = KnowledgeUnitId{reader.read_uint64()};
            if(!parse_knowledge_unit_kind(reader.read_string(), envelope.kind)) {
                throw std::runtime_error("Invalid knowledge-unit kind");
            }
            envelope.scope_id = ScopeId{reader.read_string()};
            envelope.primary_text = reader.read_string();
            envelope.display_text = reader.read_string();
            envelope.lifecycle_state = parse_lifecycle(reader.read_string());
            const auto source_count = reader.read_uint64();
            if(source_count > MAX_SOURCE_SUMMARIES) {
                throw std::runtime_error("Too many source summaries");
            }
            envelope.sources.reserve(static_cast<std::size_t>(source_count));
            for(std::uint64_t i = 0; i < source_count; ++i) {
                envelope.sources.push_back(read_source_summary(reader));
            }
            envelope.created_at_ms = reader.read_int64();
            envelope.updated_at_ms = reader.read_int64();
            envelope.observed_at_ms = reader.read_int64();
            envelope.revision = reader.read_uint64();
            envelope.content_hash = read_hash(reader);
            const auto recipe = reader.read_uint64();
            if(recipe > std::numeric_limits<std::uint16_t>::max()) {
                throw std::runtime_error("Knowledge-unit hash recipe overflow");
            }
            envelope.content_hash_recipe_version = static_cast<std::uint16_t>(recipe);
            envelope.priority_weight = reader.read_double();
            const auto supersedes_count = reader.read_uint64();
            if(supersedes_count > 1024) {
                throw std::runtime_error("Too many superseded knowledge units");
            }
            envelope.supersedes.reserve(static_cast<std::size_t>(supersedes_count));
            for(std::uint64_t i = 0; i < supersedes_count; ++i) {
                envelope.supersedes.emplace_back(reader.read_uint64());
            }
            if(reader.read_bool()) envelope.superseded_by = KnowledgeUnitId{reader.read_uint64()};
            if(reader.read_bool()) envelope.derived_from = KnowledgeUnitId{reader.read_uint64()};
            reader.require_end();
            if(!is_valid_knowledge_unit_envelope(envelope)) {
                throw std::runtime_error("Invalid persisted knowledge-unit envelope");
            }
            if(
                envelope.kind == KnowledgeUnitKind::Chunk
                && (
                    envelope.content_hash_recipe_version != 2
                    || !has_m0_chunk_identity_source(envelope.sources)
                    || envelope.content_hash != compute_chunk_content_hash(
                        envelope.scope_id,
                        envelope.primary_text,
                        envelope.sources,
                        envelope.content_hash_recipe_version
                    )
                )
            ) {
                throw std::runtime_error(
                    "Persisted M0 Chunk identity does not match its canonical hash"
                );
            }
            return envelope;
        }

        std::string content_key(const KnowledgeUnitKey& key) {
            std::string result;
            append_string(result, to_string(key.kind));
            append_string(result, key.scope_id.value());
            append_bytes(result, key.content_hash.bytes.data(), key.content_hash.bytes.size());
            return result;
        }

        std::string index_key(const ScopeId& scope, KnowledgeUnitKind kind) {
            std::string result;
            append_string(result, scope.value());
            append_string(result, to_string(kind));
            return result;
        }

        std::string index_entry_key(std::string_view base, std::uint64_t sequence) {
            // Big-endian bytes make MDBX's byte ordering match insertion order.
            std::string result{base};
            append_uint64_be(result, sequence);
            return result;
        }

        std::string index_entry_lower_bound(std::string_view base) {
            return index_entry_key(base, 0);
        }

        std::string index_entry_upper_bound(std::string_view base) {
            return index_entry_key(base, std::numeric_limits<std::uint64_t>::max());
        }

        bool is_index_schema_version(std::string_view version) {
            return version.empty()
                || version == INDEX_SCHEMA_VERSION_V1
                || version == INDEX_SCHEMA_VERSION_V2;
        }

        std::vector<KnowledgeUnitId> read_index(std::string_view payload) {
            PayloadReader reader{payload};
            const auto count = reader.read_uint64();
            if(count > 1'000'000) throw std::runtime_error("Knowledge-unit index too large");
            std::vector<KnowledgeUnitId> ids;
            ids.reserve(static_cast<std::size_t>(count));
            for(std::uint64_t i = 0; i < count; ++i) ids.emplace_back(reader.read_uint64());
            reader.require_end();
            return ids;
        }

        ContentHash validate_create_request(const KnowledgeUnitCreateRequest& request) {
            if(request.kind != KnowledgeUnitKind::Chunk
               || request.content_hash_recipe_version != 2) {
                throw std::invalid_argument("M0 store supports only recipe-v2 Chunk requests");
            }
            if(!has_m0_chunk_identity_source(request.sources)) {
                throw std::invalid_argument(
                    "M0 Chunk requests require one revision-bound source summary"
                );
            }
            const auto expected_hash = compute_chunk_content_hash(
                request.scope_id,
                request.primary_text,
                request.sources,
                request.content_hash_recipe_version
            );
            if(request.content_hash && *request.content_hash != expected_hash) {
                throw std::invalid_argument(
                    "Knowledge-unit content_hash assertion does not match canonical Chunk identity"
                );
            }
            KnowledgeUnitEnvelope probe;
            probe.id = KnowledgeUnitId{1};
            probe.kind = request.kind;
            probe.scope_id = request.scope_id;
            probe.primary_text = request.primary_text;
            probe.display_text = request.display_text;
            probe.sources = request.sources;
            probe.content_hash = expected_hash;
            probe.content_hash_recipe_version = request.content_hash_recipe_version;
            probe.priority_weight = request.priority_weight;
            if(!is_valid_knowledge_unit_envelope(probe)) {
                throw std::invalid_argument("Invalid knowledge-unit create request");
            }
            return expected_hash;
        }

    } // namespace

    class MdbxKnowledgeUnitStore::Impl final {
    public:
        explicit Impl(MdbxKnowledgeUnitStoreOptions options)
            : m_prefix(sanitize_table_part(std::move(options.table_prefix)))
            , m_context(options.context ? std::move(options.context)
                                        : MdbxStorageContext::open(
                                            options.path,
                                            options.relative_to_exe,
                                            options.max_dbs))
            , m_units(m_context->connection(), table_name(m_prefix, "knowledge_units"))
            , m_content_keys(
                m_context->connection(),
                table_name(m_prefix, "content_key_to_unit_id")
            )
            , m_kind_index(
                m_context->connection(),
                table_name(m_prefix, "knowledge_units_by_kind")
            ) {
        }

        void upsert(KnowledgeUnitEnvelope envelope) {
            if(!is_valid_knowledge_unit_envelope(envelope)) {
                throw std::invalid_argument("Invalid knowledge-unit envelope");
            }
            if(envelope.kind != KnowledgeUnitKind::Chunk
               || envelope.revision == 0
               || envelope.content_hash_recipe_version != 2) {
                throw std::invalid_argument("M0 store supports only recipe-v2 Chunk envelopes");
            }
            if(!has_m0_chunk_identity_source(envelope.sources)) {
                throw std::invalid_argument(
                    "M0 Chunk envelopes require one revision-bound source summary"
                );
            }
            if(envelope.content_hash != compute_chunk_content_hash(
                   envelope.scope_id,
                   envelope.primary_text,
                   envelope.sources,
                   envelope.content_hash_recipe_version)) {
                throw std::invalid_argument(
                    "Knowledge-unit content_hash does not match canonical Chunk identity"
                );
            }
            const KnowledgeUnitKey key{
                envelope.kind,
                envelope.scope_id,
                envelope.content_hash
            };
            const auto key_payload = content_key(key);
            auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            const auto existing_payload = m_units.find(id_key(envelope.id), txn);
            if(existing_payload) {
                const auto existing = deserialize_envelope(*existing_payload);
                if(serialize_envelope(existing) != serialize_envelope(envelope)) {
                    throw std::invalid_argument(
                        "Knowledge-unit upsert only accepts an exact idempotent replay"
                    );
                }
                const auto indexed_id = m_content_keys.find(key_payload, txn);
                if(!indexed_id || parse_id(*indexed_id) != envelope.id) {
                    throw std::runtime_error(
                        "Knowledge-unit content index does not match its envelope"
                    );
                }
                m_units.insert_or_assign(id_key(envelope.id), serialize_envelope(envelope), txn);
                txn.commit();
                return;
            }

            if(const auto indexed_id = m_content_keys.find(key_payload, txn)) {
                if(parse_id(*indexed_id) != envelope.id) {
                    throw std::invalid_argument(
                        "Knowledge-unit content key already belongs to another id"
                    );
                }
            }
            const auto used_key = used_id_key(envelope.id);
            if(m_units.find(used_key, txn)) {
                throw std::invalid_argument("Knowledge-unit id was already used");
            }
            if(envelope.lifecycle_state != KnowledgeUnitLifecycleState::Active) {
                throw std::invalid_argument(
                    "A new M0 knowledge unit must start in the Active lifecycle state"
                );
            }
            m_units.insert_or_assign(id_key(envelope.id), serialize_envelope(envelope), txn);
            m_units.insert_or_assign(used_key, "1", txn);
            m_content_keys.insert_or_assign(key_payload, id_key(envelope.id), txn);
            append_index_entries(envelope, envelope.id, txn);
            advance_next_id(envelope.id, txn);
            txn.commit();
        }

        bool erase(const KnowledgeUnitId& id) {
            if(id.empty()) return false;
            auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            const auto payload = m_units.find(id_key(id), txn);
            if(!payload) {
                txn.commit();
                return false;
            }
            const auto envelope = deserialize_envelope(*payload);
            const KnowledgeUnitKey key{
                envelope.kind,
                envelope.scope_id,
                envelope.content_hash
            };
            remove_index_entry(index_key(envelope.scope_id, envelope.kind), id, txn);
            m_content_keys.erase(content_key(key), txn);
            m_units.erase(id_key(id), txn);
            txn.commit();
            return true;
        }

        std::vector<KnowledgeUnitId> scan_by_scope_and_kind(
            const ScopeId& scope_id,
            KnowledgeUnitKind kind
        ) const {
            if(scope_id.empty() || !is_valid_knowledge_unit_kind(kind)) return {};
            const auto key = index_key(scope_id, kind);
            std::vector<KnowledgeUnitId> ids;
            const auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            const auto schema_version = index_schema_version(txn);
            bool has_legacy_entries = false;
            bool has_v2_entries = false;
            for(const auto& pair : m_kind_index.range<std::vector>(
                    key, index_entry_upper_bound(key), txn)) {
                if(pair.first == key) {
                    has_legacy_entries = true;
                    const auto legacy_ids = read_index(pair.second);
                    ids.insert(ids.end(), legacy_ids.begin(), legacy_ids.end());
                } else {
                    has_v2_entries = true;
                    ids.push_back(parse_id(pair.second));
                }
            }
            validate_index_format(schema_version, has_legacy_entries, has_v2_entries);
            return ids;
        }

        std::vector<KnowledgeUnitId> scan_by_scope(const ScopeId& scope_id) const {
            if(scope_id.empty()) return {};
            std::vector<KnowledgeUnitId> ids;
            for(std::uint16_t raw = 0; raw <= 16; ++raw) {
                const auto kind = static_cast<KnowledgeUnitKind>(raw);
                const auto kind_ids = scan_by_scope_and_kind(scope_id, kind);
                ids.insert(ids.end(), kind_ids.begin(), kind_ids.end());
            }
            return ids;
        }

        KnowledgeUnitEnvelope create_or_get_unit(KnowledgeUnitCreateRequest request) {
            const auto content_hash = validate_create_request(request);
            const KnowledgeUnitKey key{request.kind, request.scope_id, content_hash};
            const auto key_payload = content_key(key);
            auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            if(const auto existing_id_payload = m_content_keys.find(key_payload, txn)) {
                const auto existing_id = parse_id(*existing_id_payload);
                const auto existing_payload = m_units.find(id_key(existing_id), txn);
                if(!existing_payload) {
                    throw std::runtime_error(
                        "Knowledge-unit content index references a missing envelope"
                    );
                }
                auto existing = deserialize_envelope(*existing_payload);
                if(existing.kind == KnowledgeUnitKind::Chunk && existing.sources.size() != 1) {
                    throw std::runtime_error(
                        "Persisted M0 Chunk has unsupported multi-source identity"
                    );
                }
                txn.commit();
                return existing;
            }

            const auto id = allocate_id(txn);
            KnowledgeUnitEnvelope envelope;
            envelope.id = id;
            envelope.kind = request.kind;
            envelope.scope_id = std::move(request.scope_id);
            envelope.primary_text = std::move(request.primary_text);
            envelope.display_text = std::move(request.display_text);
            envelope.sources = std::move(request.sources);
            envelope.created_at_ms = request.created_at_ms;
            envelope.updated_at_ms = request.created_at_ms;
            envelope.observed_at_ms = request.observed_at_ms;
            envelope.revision = 1;
            envelope.content_hash = content_hash;
            envelope.content_hash_recipe_version = request.content_hash_recipe_version;
            envelope.priority_weight = request.priority_weight;
            if(!is_valid_knowledge_unit_envelope(envelope)) {
                throw std::invalid_argument("Invalid knowledge-unit envelope");
            }
            m_units.insert_or_assign(id_key(id), serialize_envelope(envelope), txn);
            m_units.insert_or_assign(used_id_key(id), "1", txn);
            m_content_keys.insert_or_assign(key_payload, id_key(id), txn);

            append_index_entries(envelope, id, txn);
            txn.commit();
            return envelope;
        }

        std::optional<KnowledgeUnitEnvelope> find(const KnowledgeUnitId& id) const {
            if(id.empty()) return std::nullopt;
            auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            const auto payload = m_units.find(id_key(id), txn);
            if(!payload) return std::nullopt;
            auto envelope = deserialize_envelope(*payload);
            if(envelope.kind == KnowledgeUnitKind::Chunk && envelope.sources.size() != 1) {
                throw std::runtime_error(
                    "Persisted M0 Chunk has unsupported multi-source identity"
                );
            }
            txn.commit();
            return envelope;
        }

        std::optional<KnowledgeUnitId> find_by_content_key(const KnowledgeUnitKey& key) const {
            if(key.scope_id.empty() || !is_valid_knowledge_unit_kind(key.kind)) {
                return std::nullopt;
            }
            const auto payload = m_content_keys.find(content_key(key));
            if(!payload) return std::nullopt;
            return parse_id(*payload);
        }

        KnowledgeUnitEnvelope update_mutable_fields(
            const KnowledgeUnitId& id,
            KnowledgeUnitMutablePatch patch
        ) {
            if(id.empty()) throw std::invalid_argument("Knowledge-unit id must not be empty");
            auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            const auto payload = m_units.find(id_key(id), txn);
            if(!payload) throw std::invalid_argument("Unknown knowledge-unit id");
            auto envelope = deserialize_envelope(*payload);
            if(envelope.kind == KnowledgeUnitKind::Chunk && envelope.sources.size() != 1) {
                throw std::runtime_error(
                    "Persisted M0 Chunk has unsupported multi-source identity"
                );
            }
            const auto previous = envelope;
            bool retrieval_visible_change = false;
            if(patch.display_text) envelope.display_text = std::move(*patch.display_text);
            if(patch.display_text && envelope.display_text != previous.display_text) {
                retrieval_visible_change = true;
            }
            if(patch.sources) {
                envelope.sources = std::move(*patch.sources);
                retrieval_visible_change |= !source_summaries_equal(
                    envelope.sources, previous.sources
                );
            }
            if(patch.lifecycle_state) {
                if(*patch.lifecycle_state != envelope.lifecycle_state
                   && !is_valid_knowledge_unit_lifecycle_transition(
                       envelope.lifecycle_state,
                       *patch.lifecycle_state
                   )) {
                    throw std::invalid_argument("Invalid knowledge-unit lifecycle transition");
                }
                envelope.lifecycle_state = *patch.lifecycle_state;
                if(envelope.lifecycle_state != previous.lifecycle_state) {
                    retrieval_visible_change = true;
                }
            }
            if(patch.observed_at_ms) envelope.observed_at_ms = *patch.observed_at_ms;
            if(patch.priority_weight) envelope.priority_weight = *patch.priority_weight;
            if(envelope.kind == KnowledgeUnitKind::Chunk && envelope.sources.size() != 1) {
                throw std::invalid_argument(
                    "M0 Chunk identity supports exactly one source summary"
                );
            }
            if(envelope.kind == KnowledgeUnitKind::Chunk
               && envelope.content_hash != compute_chunk_content_hash(
                   envelope.scope_id,
                   envelope.primary_text,
                   envelope.sources,
                   envelope.content_hash_recipe_version)) {
                throw std::invalid_argument(
                    "Mutable source update changes the immutable Chunk identity"
                );
            }
            const bool changed = serialize_envelope(envelope) != serialize_envelope(previous);
            if(!changed) {
                txn.commit();
                return envelope;
            }
            envelope.revision = retrieval_visible_change
                ? previous.revision + 1
                : previous.revision;
            envelope.updated_at_ms = std::max(
                previous.updated_at_ms,
                envelope.observed_at_ms
            );
            if(!is_valid_knowledge_unit_envelope(envelope)) {
                throw std::invalid_argument("Invalid mutable knowledge-unit patch");
            }
            m_units.insert_or_assign(id_key(id), serialize_envelope(envelope), txn);
            txn.commit();
            return envelope;
        }

    private:
        void append_index_entry(
            const std::string& key,
            KnowledgeUnitId id,
            const mdbxc::Transaction& txn
        ) {
            ensure_index_admission(key, txn);
            migrate_legacy_index(key, txn);
            const auto sequence = allocate_index_sequence(txn);
            m_kind_index.insert_or_assign(
                index_entry_key(key, sequence),
                id_key(id),
                txn
            );
            mark_v2_index(txn);
        }

        void append_index_entries(
            const KnowledgeUnitEnvelope& envelope,
            KnowledgeUnitId id,
            const mdbxc::Transaction& txn
        ) {
            append_index_entry(index_key(envelope.scope_id, envelope.kind), id, txn);
        }

        void remove_index_entry(
            const std::string& key,
            KnowledgeUnitId id,
            const mdbxc::Transaction& txn
        ) {
            ensure_index_admission(key, txn);
            migrate_legacy_index(key, txn);
            const auto expected = id_key(id);
            for(const auto& pair : m_kind_index.range<std::vector>(
                    index_entry_lower_bound(key), index_entry_upper_bound(key), txn)) {
                if(pair.second == expected) {
                    m_kind_index.erase(pair.first, txn);
                    break;
                }
            }
            mark_v2_index(txn);
        }

        std::string index_schema_version(const mdbxc::Transaction& txn) const {
            const auto payload = m_units.find(std::string{INDEX_SCHEMA_VERSION_KEY}, txn);
            if(!payload) return {};
            if(!is_index_schema_version(*payload)) {
                throw std::runtime_error(
                    "Unsupported knowledge-unit by-kind index schema version"
                );
            }
            return *payload;
        }

        static void validate_index_format(
            std::string_view schema_version,
            bool has_legacy_entries,
            bool has_v2_entries
        ) {
            if(has_v2_entries && schema_version != INDEX_SCHEMA_VERSION_V2) {
                throw std::runtime_error(
                    "Knowledge-unit by-kind index has v2 entries without a v2 marker"
                );
            }
            if(has_legacy_entries && has_v2_entries) {
                throw std::runtime_error(
                    "Knowledge-unit by-kind index mixes legacy and v2 entries"
                );
            }
        }

        void ensure_index_admission(
            const std::string& key,
            const mdbxc::Transaction& txn
        ) {
            const auto payload = m_units.find(std::string{INDEX_SCHEMA_VERSION_KEY}, txn);
            const std::string schema_version = payload ? *payload : std::string{};
            if(!is_index_schema_version(schema_version)) {
                throw std::runtime_error(
                    "Unsupported knowledge-unit by-kind index schema version"
                );
            }
            const bool has_legacy_entries = m_kind_index.find(key, txn).has_value();
            bool has_v2_entries = false;
            m_kind_index.for_each_range(
                index_entry_lower_bound(key),
                index_entry_upper_bound(key),
                [&has_v2_entries](const std::string&, const std::string&) {
                    has_v2_entries = true;
                    return false;
                },
                txn
            );
            validate_index_format(schema_version, has_legacy_entries, has_v2_entries);
        }

        void mark_v2_index(const mdbxc::Transaction& txn) {
            m_units.insert_or_assign(
                std::string{INDEX_SCHEMA_VERSION_KEY},
                std::string{INDEX_SCHEMA_VERSION_V2},
                txn
            );
        }

        void migrate_legacy_index(
            const std::string& key,
            const mdbxc::Transaction& txn
        ) {
            const auto payload = m_kind_index.find(key, txn);
            if(!payload) return;
            const auto ids = read_index(*payload);
            m_kind_index.erase(key, txn);
            for(const auto id : ids) {
                m_kind_index.insert_or_assign(
                    index_entry_key(key, allocate_index_sequence(txn)),
                    id_key(id),
                    txn
                );
            }
        }

        static std::string id_key(KnowledgeUnitId id) {
            return std::to_string(id.value());
        }

        static std::string used_id_key(KnowledgeUnitId id) {
            return "@used:" + id_key(id);
        }

        static KnowledgeUnitId parse_id(std::string_view text) {
            if(text.empty()) throw std::runtime_error("Empty knowledge-unit id");
            std::uint64_t value = 0;
            for(const auto c : text) {
                if(c < '0' || c > '9') throw std::runtime_error("Invalid knowledge-unit id");
                const auto digit = static_cast<std::uint64_t>(c - '0');
                if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
                    throw std::runtime_error("Knowledge-unit id overflow");
                }
                value = value * 10U + digit;
            }
            if(value == 0) throw std::runtime_error("Zero knowledge-unit id");
            return KnowledgeUnitId{value};
        }

        KnowledgeUnitId allocate_id(const mdbxc::Transaction& txn) {
            std::uint64_t next = 1;
            const std::string next_id_key{NEXT_ID_KEY};
            if(const auto payload = m_units.find(next_id_key, txn)) {
                next = parse_id(*payload).value();
            }
            if(next == std::numeric_limits<std::uint64_t>::max()) {
                throw std::runtime_error("Knowledge-unit id space exhausted");
            }
            m_units.insert_or_assign(next_id_key, std::to_string(next + 1), txn);
            return KnowledgeUnitId{next};
        }

        void advance_next_id(KnowledgeUnitId id, const mdbxc::Transaction& txn) {
            const std::string next_id_key{NEXT_ID_KEY};
            std::uint64_t next = 1;
            if(const auto payload = m_units.find(next_id_key, txn)) {
                next = parse_id(*payload).value();
            }
            if(id.value() >= next) {
                if(id.value() == std::numeric_limits<std::uint64_t>::max()) {
                    throw std::runtime_error("Knowledge-unit id space exhausted");
                }
                m_units.insert_or_assign(
                    next_id_key,
                    std::to_string(id.value() + 1),
                    txn
                );
            }
        }

        std::uint64_t allocate_index_sequence(const mdbxc::Transaction& txn) {
            std::uint64_t next = 1;
            const std::string sequence_key{NEXT_INDEX_SEQUENCE_KEY};
            if(const auto payload = m_units.find(sequence_key, txn)) {
                next = parse_id(*payload).value();
            }
            if(next == std::numeric_limits<std::uint64_t>::max()) {
                throw std::runtime_error("Knowledge-unit index sequence space exhausted");
            }
            m_units.insert_or_assign(sequence_key, std::to_string(next + 1), txn);
            return next;
        }

        std::string m_prefix;
        std::shared_ptr<MdbxStorageContext> m_context;
        mdbxc::KeyValueTable<std::string, std::string> m_units;
        mdbxc::KeyValueTable<std::string, std::string> m_content_keys;
        mdbxc::KeyValueTable<std::string, std::string> m_kind_index;
    };

    MdbxKnowledgeUnitStore::MdbxKnowledgeUnitStore(
        MdbxKnowledgeUnitStoreOptions options
    )
        : m_impl(std::make_unique<Impl>(std::move(options))) {}

    MdbxKnowledgeUnitStore::~MdbxKnowledgeUnitStore() = default;

    void MdbxKnowledgeUnitStore::upsert(KnowledgeUnitEnvelope envelope) {
        m_impl->upsert(std::move(envelope));
    }

    bool MdbxKnowledgeUnitStore::erase(const KnowledgeUnitId& id) {
        return m_impl->erase(id);
    }

    std::vector<KnowledgeUnitId> MdbxKnowledgeUnitStore::scan_by_scope_and_kind(
        const ScopeId& scope_id,
        KnowledgeUnitKind kind
    ) const {
        return m_impl->scan_by_scope_and_kind(scope_id, kind);
    }

    std::vector<KnowledgeUnitId> MdbxKnowledgeUnitStore::scan_by_scope(
        const ScopeId& scope_id
    ) const {
        return m_impl->scan_by_scope(scope_id);
    }

    KnowledgeUnitEnvelope MdbxKnowledgeUnitStore::create_or_get_unit(
        KnowledgeUnitCreateRequest request
    ) {
        return m_impl->create_or_get_unit(std::move(request));
    }

    std::optional<KnowledgeUnitEnvelope> MdbxKnowledgeUnitStore::find(
        const KnowledgeUnitId& id
    ) const {
        return m_impl->find(id);
    }

    std::optional<KnowledgeUnitId> MdbxKnowledgeUnitStore::find_by_content_key(
        const KnowledgeUnitKey& key
    ) const {
        return m_impl->find_by_content_key(key);
    }

    KnowledgeUnitEnvelope MdbxKnowledgeUnitStore::update_mutable_fields(
        const KnowledgeUnitId& id,
        KnowledgeUnitMutablePatch patch
    ) {
        return m_impl->update_mutable_fields(id, std::move(patch));
    }

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
