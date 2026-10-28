#include "MdbxResourceImporter.hpp"
#include "MdbxKnowledgeUnitStore.hpp"
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <algorithm>
#include <cctype>
#include <chrono>
#include <limits>
#include <map>
#include <mutex>
#include <random>
#include <stdexcept>
#include <unordered_set>
#include <utility>
#include <vector>

namespace agent_memory {
namespace {

constexpr std::string_view kIntentVersionV3 =
    "agent_memory.resource_import_intent.v3";
constexpr std::string_view kIntentVersionV4 =
    "agent_memory.resource_import_intent.v4";
constexpr std::string_view kFencingSchemaVersion =
    "agent_memory.resource_import_fencing.v1";
constexpr std::string_view kFencingSchemaKey = "schema";
constexpr std::string_view kFencingTokenTableSuffix = "resource_import_fencing_tokens";
constexpr char kSeparator = '\x1f';
constexpr std::size_t kMaxRawChunkPreviewBytes = 256;

std::string fencing_token(std::string_view serialized_intent);

void append_size(std::string& out, std::size_t value) {
    out += std::to_string(value);
    out.push_back(':');
}

void append_string(std::string& out, std::string_view value) {
    append_size(out, value.size());
    out.append(value.data(), value.size());
}

class Reader final {
public:
    explicit Reader(std::string_view payload) : m_payload(payload) {}

    std::string string() {
        std::size_t size = 0;
        bool has_digit = false;
        while(m_position < m_payload.size() && m_payload[m_position] != ':') {
            const auto c = static_cast<unsigned char>(m_payload[m_position++]);
            if(c < '0' || c > '9') {
                throw std::runtime_error("resource import intent has an invalid length");
            }
            const auto digit = static_cast<std::size_t>(c - '0');
            if(size > ((std::numeric_limits<std::size_t>::max)() - digit) / 10U) {
                throw std::runtime_error("resource import intent length overflow");
            }
            size = size * 10U + digit;
            has_digit = true;
        }
        if(!has_digit || m_position == m_payload.size()) {
            throw std::runtime_error("resource import intent is missing a length delimiter");
        }
        ++m_position;
        if(size > m_payload.size() - m_position) {
            throw std::runtime_error("resource import intent exceeds its payload");
        }
        std::string result{m_payload.data() + m_position, size};
        m_position += size;
        return result;
    }

    std::uint64_t u64() {
        const auto text = string();
        if(text.empty()) {
            throw std::runtime_error("resource import intent has an empty integer");
        }
        std::uint64_t value = 0;
        for(const auto c : text) {
            if(c < '0' || c > '9') {
                throw std::runtime_error("resource import intent has an invalid integer");
            }
            const auto digit = static_cast<std::uint64_t>(c - '0');
            if(value > ((std::numeric_limits<std::uint64_t>::max)() - digit) / 10U) {
                throw std::runtime_error("resource import intent integer overflow");
            }
            value = value * 10U + digit;
        }
        return value;
    }

    void require_end() const {
        if(m_position != m_payload.size()) {
            throw std::runtime_error("resource import intent has trailing data");
        }
    }

private:
    std::string_view m_payload;
    std::size_t m_position = 0;
};

class Sha256 final {
public:
    void update(std::string_view bytes) {
        for(const auto byte : bytes) {
            m_buffer[m_size++] = static_cast<std::uint8_t>(byte);
            m_bits += 8U;
            if(m_size == m_buffer.size()) {
                transform(m_buffer.data());
                m_size = 0;
            }
        }
    }

    [[nodiscard]] std::array<std::uint8_t, 32> digest() {
        const auto bits = m_bits;
        m_buffer[m_size++] = 0x80U;
        if(m_size > 56U) {
            while(m_size < m_buffer.size()) m_buffer[m_size++] = 0U;
            transform(m_buffer.data());
            m_size = 0;
        }
        while(m_size < 56U) m_buffer[m_size++] = 0U;
        for(int shift = 56; shift >= 0; shift -= 8) {
            m_buffer[m_size++] = static_cast<std::uint8_t>((bits >> shift) & 0xffU);
        }
        transform(m_buffer.data());
        std::array<std::uint8_t, 32> result{};
        for(std::size_t i = 0; i < m_state.size(); ++i) {
            result[i * 4U] = static_cast<std::uint8_t>(m_state[i] >> 24U);
            result[i * 4U + 1U] = static_cast<std::uint8_t>(m_state[i] >> 16U);
            result[i * 4U + 2U] = static_cast<std::uint8_t>(m_state[i] >> 8U);
            result[i * 4U + 3U] = static_cast<std::uint8_t>(m_state[i]);
        }
        return result;
    }

private:
    static std::uint32_t rotr(std::uint32_t value, int bits) {
        return (value >> bits) | (value << (32 - bits));
    }

    void transform(const std::uint8_t* input) {
        static constexpr std::array<std::uint32_t, 64> k{ {
            0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
            0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
            0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
            0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
            0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
            0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
            0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
            0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
        } };
        std::array<std::uint32_t, 64> words{};
        for(std::size_t i = 0; i < 16U; ++i) {
            words[i] = (static_cast<std::uint32_t>(input[i * 4U]) << 24U)
                | (static_cast<std::uint32_t>(input[i * 4U + 1U]) << 16U)
                | (static_cast<std::uint32_t>(input[i * 4U + 2U]) << 8U)
                | static_cast<std::uint32_t>(input[i * 4U + 3U]);
        }
        for(std::size_t i = 16U; i < words.size(); ++i) {
            const auto s0 = rotr(words[i - 15U], 7) ^ rotr(words[i - 15U], 18)
                ^ (words[i - 15U] >> 3U);
            const auto s1 = rotr(words[i - 2U], 17) ^ rotr(words[i - 2U], 19)
                ^ (words[i - 2U] >> 10U);
            words[i] = s1 + words[i - 7U] + s0 + words[i - 16U];
        }
        auto a = m_state[0]; auto b = m_state[1]; auto c = m_state[2]; auto d = m_state[3];
        auto e = m_state[4]; auto f = m_state[5]; auto g = m_state[6]; auto h = m_state[7];
        for(std::size_t i = 0; i < words.size(); ++i) {
            const auto s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const auto choose = (e & f) ^ (~e & g);
            const auto t1 = h + s1 + choose + k[i] + words[i];
            const auto s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const auto majority = (a & b) ^ (a & c) ^ (b & c);
            const auto t2 = s0 + majority;
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        m_state[0] += a; m_state[1] += b; m_state[2] += c; m_state[3] += d;
        m_state[4] += e; m_state[5] += f; m_state[6] += g; m_state[7] += h;
    }

    std::array<std::uint32_t, 8> m_state{{
        0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,
        0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U
    }};
    std::array<std::uint8_t, 64> m_buffer{};
    std::size_t m_size = 0;
    std::uint64_t m_bits = 0;
};

struct PendingImport final {
    ObservedResource resource;
    ResourceImportPublicationProfile publication_profile =
        ResourceImportPublicationProfile::LexicalOnly;
    std::string owner_id;
    std::uint64_t fencing_epoch = 0;
    std::uint64_t lease_expires_at_ms = 0;
};

struct ClaimedImport final {
    PendingImport operation;
    std::string payload;
    std::string token;
    bool created = false;
};

std::string make_owner_id() {
    static constexpr char digits[] = "0123456789abcdef";
    std::random_device source;
    std::string id;
    id.reserve(32);
    for(std::size_t word = 0; word < 4; ++word) {
        const auto value = static_cast<std::uint32_t>(source());
        for(int shift = 28; shift >= 0; shift -= 4) {
            id.push_back(digits[(value >> shift) & 0x0fU]);
        }
    }
    return id;
}

std::uint64_t system_clock_ms() {
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    if(elapsed < 0) {
        throw std::runtime_error("system clock precedes the Unix epoch");
    }
    return static_cast<std::uint64_t>(elapsed);
}

std::uint64_t lease_deadline(std::uint64_t now, std::uint64_t duration) {
    if(duration == 0) {
        throw std::invalid_argument("resource importer ownership lease must be positive");
    }
    if(now > (std::numeric_limits<std::uint64_t>::max)() - duration) {
        throw std::runtime_error("resource importer ownership lease overflow");
    }
    return now + duration;
}

std::uint64_t parse_decimal_u64(std::string_view text, std::string_view field) {
    if(text.empty()) {
        throw std::runtime_error(std::string{field} + " is empty");
    }
    std::uint64_t value = 0;
    for(const auto character : text) {
        if(character < '0' || character > '9') {
            throw std::runtime_error(std::string{field} + " is invalid");
        }
        const auto digit = static_cast<std::uint64_t>(character - '0');
        if(value > ((std::numeric_limits<std::uint64_t>::max)() - digit) / 10U) {
            throw std::runtime_error(std::string{field} + " overflow");
        }
        value = value * 10U + digit;
    }
    return value;
}

std::string sanitize_table_prefix(std::string prefix) {
    if(prefix.empty()) prefix = "agent_memory";
    for(auto& c : prefix) {
        if(!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    }
    return prefix;
}

std::string table_name(std::string prefix, std::string_view suffix) {
    prefix = sanitize_table_prefix(std::move(prefix));
    prefix.push_back('_');
    prefix.append(suffix.data(), suffix.size());
    return prefix;
}

void preflight_fencing_schema(
    const std::shared_ptr<MdbxStorageContext>& context,
    std::string_view table_prefix
) {
    mdbxc::KeyValueTable<std::string, std::string> marker(
        context->connection(),
        table_name(std::string{table_prefix}, "resource_import_fencing_schema")
    );
    mdbxc::KeyValueTable<std::string, std::string> intents(
        context->connection(),
        table_name(std::string{table_prefix}, "resource_import_intents")
    );
    mdbxc::KeyValueTable<std::string, std::string> tokens(
        context->connection(),
        table_name(std::string{table_prefix}, kFencingTokenTableSuffix)
    );
    const auto validate_snapshot = [&](const mdbxc::Transaction& txn,
                                       bool allow_missing_marker) {
        const auto stored_marker = marker.find(std::string{kFencingSchemaKey}, txn);
        std::vector<std::pair<std::string, std::string>> existing_intents;
        std::vector<std::pair<std::string, std::string>> existing_tokens;
        intents.load(existing_intents, txn);
        tokens.load(existing_tokens, txn);
        if(!stored_marker) {
            if(!allow_missing_marker || !existing_intents.empty() ||
               !existing_tokens.empty()) {
                throw std::runtime_error(
                    "resource import fencing schema requires a quiesced offline upgrade"
                );
            }
            return false;
        }
        if(*stored_marker != kFencingSchemaVersion) {
            throw std::runtime_error("unsupported resource import fencing schema marker");
        }
        for(const auto& item : existing_intents) {
            const auto token = tokens.find(item.first, txn);
            if(!token || *token != fencing_token(item.second)) {
                throw std::runtime_error(
                    "resource import fencing token is missing or does not match intent"
                );
            }
        }
        std::unordered_set<std::string> intent_keys;
        intent_keys.reserve(existing_intents.size());
        for(const auto& intent : existing_intents) {
            intent_keys.insert(intent.first);
        }
        for(const auto& item : existing_tokens) {
            if(intent_keys.find(item.first) == intent_keys.end()) {
                throw std::runtime_error("resource import fencing token is orphaned");
            }
        }
        return true;
    };

    auto read_txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto initialized = validate_snapshot(read_txn, true);
    read_txn.rollback();
    if(initialized) return;

    // A writable recheck closes the first-open TOCTOU window: only one
    // concurrent opener can install the marker, and it must still observe an
    // empty intent/token set.
    auto init_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto stored_marker = marker.find(std::string{kFencingSchemaKey}, init_txn);
    if(stored_marker) {
        static_cast<void>(validate_snapshot(init_txn, false));
        init_txn.commit();
        return;
    }
    std::vector<std::pair<std::string, std::string>> existing_intents;
    std::vector<std::pair<std::string, std::string>> existing_tokens;
    intents.load(existing_intents, init_txn);
    tokens.load(existing_tokens, init_txn);
    if(!existing_intents.empty() || !existing_tokens.empty()) {
        init_txn.rollback();
        throw std::runtime_error(
            "resource import fencing schema requires a quiesced offline upgrade"
        );
    }
    marker.insert_or_assign(
        std::string{kFencingSchemaKey},
        std::string{kFencingSchemaVersion}, init_txn
    );
    init_txn.commit();
}

std::string publication_schema_id(
    ResourceImportPublicationProfile profile,
    std::string_view table_prefix
) {
    if(profile == ResourceImportPublicationProfile::LexicalOnly) {
        return "agent_memory.m0_resource_importer";
    }
    if(profile == ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections) {
        return "agent_memory.m0_resource_importer.raw_chunks_original@" +
            sanitize_table_prefix(std::string{table_prefix});
    }
    return "agent_memory.m0_resource_importer.raw_chunks@" +
        sanitize_table_prefix(std::string{table_prefix});
}

bool is_valid_publication_profile(ResourceImportPublicationProfile profile) {
    return profile == ResourceImportPublicationProfile::LexicalOnly ||
        profile == ResourceImportPublicationProfile::LexicalWithRawChunks ||
        profile == ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
}

void validate_resource(const ObservedResource& resource) {
    if(resource.scope_id.empty() || resource.resource_id.empty()) {
        throw std::invalid_argument("resource scope and id must not be empty");
    }
    if(resource.scope_id.value().find(kSeparator) != std::string::npos ||
       resource.resource_id.value().find(kSeparator) != std::string::npos) {
        throw std::invalid_argument("resource scope and id must not contain U+001F");
    }
    if(resource.source_uri.empty() || resource.utf8_body.empty()) {
        throw std::invalid_argument("resource URI and UTF-8 body must not be empty");
    }
    for(std::size_t i = 0; i < resource.utf8_body.size();) {
        const auto lead = static_cast<unsigned char>(resource.utf8_body[i]);
        std::size_t width = 0;
        std::uint32_t code_point = 0;
        if(lead <= 0x7fU) {
            width = 1;
            code_point = lead;
        } else if(lead >= 0xc2U && lead <= 0xdfU) {
            width = 2;
            code_point = lead & 0x1fU;
        } else if(lead >= 0xe0U && lead <= 0xefU) {
            width = 3;
            code_point = lead & 0x0fU;
        } else if(lead >= 0xf0U && lead <= 0xf4U) {
            width = 4;
            code_point = lead & 0x07U;
        } else {
            throw std::invalid_argument("resource body is not valid UTF-8");
        }
        if(i + width > resource.utf8_body.size()) {
            throw std::invalid_argument("resource body is not valid UTF-8");
        }
        for(std::size_t offset = 1; offset < width; ++offset) {
            const auto continuation = static_cast<unsigned char>(
                resource.utf8_body[i + offset]
            );
            if((continuation & 0xc0U) != 0x80U) {
                throw std::invalid_argument("resource body is not valid UTF-8");
            }
            code_point = (code_point << 6U) | (continuation & 0x3fU);
        }
        if((width == 2 && code_point < 0x80U) ||
           (width == 3 && code_point < 0x800U) ||
           (width == 4 && code_point < 0x10000U) ||
           code_point > 0x10ffffU ||
           (code_point >= 0xd800U && code_point <= 0xdfffU)) {
            throw std::invalid_argument("resource body is not valid UTF-8");
        }
        i += width;
    }
    if(resource.text_origin == SourceTextOrigin::OriginalText) {
        if(resource.derived_text_provenance) {
            throw std::invalid_argument("OriginalText must not carry derived provenance");
        }
    } else if(resource.text_origin == SourceTextOrigin::DerivedExtraction) {
        if(!resource.derived_text_provenance ||
           !is_valid_derived_text_provenance(*resource.derived_text_provenance)) {
            throw std::invalid_argument(
                "DerivedExtraction requires complete extractor provenance"
            );
        }
    } else {
        throw std::invalid_argument("unsupported resource text origin");
    }
}

std::string serialize_intent(const PendingImport& pending) {
    const auto& resource = pending.resource;
    std::string payload;
    append_string(payload, kIntentVersionV4);
    append_string(
        payload,
        pending.publication_profile == ResourceImportPublicationProfile::LexicalOnly
            ? "lexical_only"
            : pending.publication_profile == ResourceImportPublicationProfile::LexicalWithRawChunks
                ? "lexical_with_raw_chunks"
                : "lexical_with_raw_and_original_projections"
    );
    append_string(payload, pending.owner_id);
    append_string(payload, std::to_string(pending.fencing_epoch));
    append_string(payload, std::to_string(pending.lease_expires_at_ms));
    append_string(payload, resource.scope_id.value());
    append_string(payload, resource.resource_id.value());
    append_string(payload, resource.source_uri);
    append_string(payload, resource.utf8_body);
    append_string(
        payload,
        resource.text_origin == SourceTextOrigin::OriginalText ? "original" : "derived"
    );
    if(resource.derived_text_provenance) {
        append_string(payload, resource.derived_text_provenance->source_media_type);
        append_string(payload, resource.derived_text_provenance->extractor_id);
        append_string(payload, resource.derived_text_provenance->extractor_version);
    } else {
        append_string(payload, "");
        append_string(payload, "");
        append_string(payload, "");
    }
    append_string(payload, std::to_string(resource.pipeline_config_hash));
    return payload;
}

std::string fencing_token(std::string_view serialized_intent) {
    Sha256 sha;
    static constexpr std::string_view domain = "agent-memory-fencing-token-v1\\0";
    sha.update(domain);
    sha.update(serialized_intent);
    const auto digest = sha.digest();
    return std::string{
        reinterpret_cast<const char*>(digest.data()),
        digest.size()
    };
}

PendingImport deserialize_intent(std::string_view payload) {
    Reader reader{payload};
    const auto version = reader.string();
    if(version != kIntentVersionV3 && version != kIntentVersionV4) {
        throw std::runtime_error("unsupported resource import intent version");
    }
    PendingImport pending;
    if(version == kIntentVersionV4) {
        const auto profile = reader.string();
        if(profile == "lexical_only") {
            pending.publication_profile = ResourceImportPublicationProfile::LexicalOnly;
        } else if(profile == "lexical_with_raw_chunks") {
            pending.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawChunks;
        } else if(profile == "lexical_with_raw_and_original_projections") {
            pending.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        } else {
            throw std::runtime_error("resource import intent has an invalid publication profile");
        }
    }
    pending.owner_id = reader.string();
    pending.fencing_epoch = reader.u64();
    pending.lease_expires_at_ms = reader.u64();
    if(pending.owner_id.empty() || pending.fencing_epoch == 0 ||
       pending.lease_expires_at_ms == 0) {
        throw std::runtime_error("resource import intent has invalid ownership metadata");
    }
    pending.resource.scope_id = ScopeId{reader.string()};
    pending.resource.resource_id = ResourceId{reader.string()};
    pending.resource.source_uri = reader.string();
    pending.resource.utf8_body = reader.string();
    const auto origin = reader.string();
    const auto media_type = reader.string();
    const auto extractor_id = reader.string();
    const auto extractor_version = reader.string();
    pending.resource.pipeline_config_hash = reader.u64();
    reader.require_end();
    if(origin == "original") {
        pending.resource.text_origin = SourceTextOrigin::OriginalText;
    } else if(origin == "derived") {
        pending.resource.text_origin = SourceTextOrigin::DerivedExtraction;
        pending.resource.derived_text_provenance = DerivedTextProvenance{
            media_type, extractor_id, extractor_version
        };
    } else {
        throw std::runtime_error("resource import intent has an invalid text origin");
    }
    validate_resource(pending.resource);
    return pending;
}

std::string encode_component(std::string_view value) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size() * 2U);
    for(const auto byte : value) {
        const auto octet = static_cast<unsigned char>(byte);
        result.push_back(digits[octet >> 4U]);
        result.push_back(digits[octet & 0x0fU]);
    }
    return result;
}

DocumentId document_id(const ObservedResource& resource) {
    return DocumentId{
        "durable-lexical:" + encode_component(resource.scope_id.value()) + ':' +
        encode_component(resource.resource_id.value())
    };
}

ChunkId chunk_id(const ObservedResource& resource) {
    return ChunkId{
        resource.scope_id.value() + std::string(1, kSeparator) +
        resource.resource_id.value() + std::string(1, kSeparator) + "chunk"
    };
}

ResourceBodyDigest body_digest(std::string_view body) {
    Sha256 sha;
    sha.update(body);
    ResourceBodyDigest digest;
    digest.bytes = sha.digest();
    return digest;
}

std::size_t utf8_code_point_width(std::string_view text, std::size_t offset) {
    const auto lead = static_cast<unsigned char>(text[offset]);
    if(lead <= 0x7fU) return 1;
    if(lead <= 0xdfU) return 2;
    if(lead <= 0xefU) return 3;
    return 4;
}

std::size_t bounded_utf8_prefix(std::string_view text, std::size_t maximum) {
    std::size_t length = 0;
    while(length < text.size()) {
        const auto width = utf8_code_point_width(text, length);
        if(length + width > maximum) break;
        length += width;
    }
    return length;
}

std::vector<TextRange> split_utf8_ranges(
    std::string_view text,
    std::size_t maximum
) {
    std::vector<TextRange> ranges;
    for(std::size_t offset = 0; offset < text.size();) {
        const auto length = bounded_utf8_prefix(text.substr(offset), maximum);
        if(length == 0) {
            throw std::runtime_error("UTF-8 chunk limit is too small for a code point");
        }
        ranges.push_back(TextRange{
            static_cast<std::uint64_t>(offset),
            static_cast<std::uint64_t>(length)
        });
        offset += length;
    }
    return ranges;
}

std::uint64_t content_hint(std::string_view body) {
    std::uint64_t value = 1469598103934665603ULL;
    for(const auto byte : body) {
        value ^= static_cast<unsigned char>(byte);
        value *= 1099511628211ULL;
    }
    return value;
}

ResourceManifest make_manifest(
    const ObservedResource& resource,
    std::uint64_t generation,
    const ResourceBodyDigest& digest,
    const std::vector<KnowledgeUnitId>& raw_units,
    ResourceImportPublicationProfile profile,
    std::string_view table_prefix
) {
    const auto document = document_id(resource);
    const auto chunk = chunk_id(resource);
    ResourceRevision revision;
    revision.resource_id = resource.resource_id;
    revision.generation = generation;
    revision.content_hash = content_hint(resource.utf8_body);
    revision.pipeline_config_hash = resource.pipeline_config_hash;
    revision.body_digest = digest;
    revision.text_origin = resource.text_origin;
    revision.derived_text_provenance = resource.derived_text_provenance;
    ResourceManifest manifest;
    manifest.revision = std::move(revision);
    manifest.records = {
        DerivedRecordRef{DerivedRecordKind::Document, ChunkId{}, document.value(), 0},
        DerivedRecordRef{DerivedRecordKind::Chunk, chunk, std::string{}, 0}
    };
    for(std::size_t index = 0; index < raw_units.size(); ++index) {
        manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom,
            ChunkId{},
            "knowledge_unit:" + std::to_string(raw_units[index].value()),
            static_cast<std::uint32_t>(index)
        });
        if(profile == ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections) {
            manifest.records.push_back(DerivedRecordRef{
                DerivedRecordKind::Custom,
                ChunkId{},
                "original_projection:" + std::to_string(raw_units[index].value()),
                static_cast<std::uint32_t>(index)
            });
        }
    }
    manifest.schema = ResourceManifestSchema{
        publication_schema_id(profile, table_prefix),
        1
    };
    return manifest;
}

bool same_provenance(
    SourceTextOrigin left_origin,
    const std::optional<DerivedTextProvenance>& left,
    SourceTextOrigin right_origin,
    const std::optional<DerivedTextProvenance>& right
) {
    if(left_origin != right_origin || left.has_value() != right.has_value()) {
        return false;
    }
    if(!left) return true;
    return left->source_media_type == right->source_media_type &&
        left->extractor_id == right->extractor_id &&
        left->extractor_version == right->extractor_version;
}

bool same_import_request(
    const ObservedResource& left,
    const ObservedResource& right
) {
    return left.scope_id == right.scope_id &&
        left.resource_id == right.resource_id &&
        left.source_uri == right.source_uri &&
        left.utf8_body == right.utf8_body &&
        left.pipeline_config_hash == right.pipeline_config_hash &&
        same_provenance(
            left.text_origin,
            left.derived_text_provenance,
            right.text_origin,
            right.derived_text_provenance
        );
}

bool manifest_has_raw_bindings(const ResourceManifest& manifest) {
    for(const auto& record : manifest.records) {
        if(record.kind == DerivedRecordKind::Custom &&
           record.key.rfind("knowledge_unit:", 0) == 0) {
            return true;
        }
    }
    return false;
}

} // namespace

class MdbxResourceImporter::Impl final {
public:
    explicit Impl(MdbxResourceImporterOptions options)
        : m_context(options.context ? std::move(options.context)
                                    : MdbxStorageContext::open(
                                          options.path,
                                          options.relative_to_exe,
                                          options.max_dbs))
        , m_lexical([&] {
            preflight_fencing_schema(m_context, options.table_prefix);
            MdbxDurableLexicalDocumentMemoryOptions lexical_options;
            lexical_options.table_prefix = options.table_prefix;
            lexical_options.context = m_context;
            return lexical_options;
        }())
        , m_manifests([&] {
            MdbxResourceManifestStorageOptions manifest_options;
            manifest_options.table_prefix = options.table_prefix;
            manifest_options.context = m_context;
            manifest_options.max_dbs = options.max_dbs;
            manifest_options.publication_guard_table_name =
                table_name(options.table_prefix, "resource_import_intents");
            manifest_options.publication_token_guard_table_name =
                table_name(options.table_prefix, "resource_import_fencing_tokens");
            return manifest_options;
        }())
        , m_intent_table_name(table_name(options.table_prefix, "resource_import_intents"))
        , m_token_table_name(
              table_name(options.table_prefix, "resource_import_fencing_tokens")
          )
        , m_intents(m_context->connection(), m_intent_table_name)
        , m_tokens(
              m_context->connection(),
              table_name(options.table_prefix, "resource_import_fencing_tokens")
          )
        , m_bindings(m_context->connection(), table_name(options.table_prefix, "resource_import_bindings"))
        , m_epochs(m_context->connection(), table_name(options.table_prefix, "resource_import_epochs"))
        , m_lease_ms(options.ownership_lease_ms)
        , m_publication_profile(options.publication_profile)
        , m_table_prefix(sanitize_table_prefix(options.table_prefix))
        , m_max_raw_body_bytes(options.max_raw_body_bytes)
        , m_max_raw_chunks(options.max_raw_chunks)
        , m_raw_chunk_batch_size(options.raw_chunk_batch_size)
        , m_clock(options.clock_ms ? std::move(options.clock_ms) : system_clock_ms)
        , m_fault_injector(std::move(options.fault_injector))
        , m_diagnostics(std::move(options.diagnostics)) {
        if(!is_valid_publication_profile(m_publication_profile)) {
            throw std::invalid_argument("resource importer has an unknown publication profile");
        }
        if(m_lease_ms == 0) {
            throw std::invalid_argument("resource importer ownership lease must be positive");
        }
        if(m_publication_profile != ResourceImportPublicationProfile::LexicalOnly) {
            if(m_max_raw_body_bytes == 0 || m_max_raw_chunks == 0 ||
               m_max_raw_chunks > (std::numeric_limits<std::uint32_t>::max)() ||
               m_raw_chunk_batch_size == 0 ||
               m_raw_chunk_batch_size > kMdbxKnowledgeUnitMaxBatchSize) {
                throw std::invalid_argument(
                    "raw publication admission and batch limits are invalid"
                );
            }
            MdbxKnowledgeUnitStoreOptions store_options;
            store_options.table_prefix = options.table_prefix;
            store_options.max_dbs = options.max_dbs;
            store_options.context = m_context;
            store_options.publication_token_guard_table_name =
                table_name(options.table_prefix, "resource_import_fencing_tokens");
            m_knowledge_unit_store = std::make_shared<MdbxKnowledgeUnitStore>(
                std::move(store_options)
            );
            if(m_publication_profile ==
               ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections) {
                MdbxProjectionStoreOptions projection_options;
                projection_options.table_prefix = options.table_prefix;
                projection_options.max_dbs = options.max_dbs;
                projection_options.context = m_context;
                m_projection_store = std::make_shared<MdbxProjectionStore>(
                    std::move(projection_options)
                );
            }
        }
        m_owner_id = make_owner_id();
        recover_pending();
    }

    ResourceImportResult import(ObservedResource resource) {
        validate_resource(resource);
        validate_admission(resource);
        std::lock_guard<std::mutex> lock(m_mutex);
        ensure_manifest_compatibility(resource, m_publication_profile);
        auto claim = claim_import(resource);
        ensure_profile_compatibility(
            claim.operation.publication_profile,
            m_publication_profile
        );
        if(!same_import_request(claim.operation.resource, resource)) {
            if(claim.operation.owner_id == m_owner_id ||
               claim.operation.lease_expires_at_ms > now_ms()) {
                throw std::runtime_error("a different resource import is already pending");
            }
            const auto recovered = takeover_pending(
                resource.resource_id.value(),
                claim.payload,
                std::move(claim.operation)
            );
            static_cast<void>(complete(
                recovered.operation,
                false,
                recovered.payload,
                recovered.token
            ));
            claim = claim_import(resource);
            if(!same_import_request(claim.operation.resource, resource)) {
                throw std::runtime_error(
                    "resource import changed while recovering an expired intent"
                );
            }
        }
        if(claim.operation.owner_id != m_owner_id) {
            if(claim.operation.lease_expires_at_ms > now_ms()) {
                throw std::runtime_error("resource import is owned by another live executor");
            }
            claim = takeover_pending(
                resource.resource_id.value(),
                claim.payload,
                std::move(claim.operation)
            );
            return complete(claim.operation, true, claim.payload, claim.token);
        }
        if(claim.created) {
            inject(ResourceImporterFaultPoint::AfterIntent);
        }
        return complete(claim.operation, true, claim.payload, claim.token);
    }

    std::optional<ResourceManifest> find_manifest(const ResourceId& id) const {
        return m_manifests.find_manifest(id);
    }

    std::vector<DurableLexicalDocumentHit> search(
        const ScopeId& scope, std::string_view query, std::size_t limit
    ) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        std::vector<DurableLexicalDocumentHit> visible;
        if(limit == 0) {
            throw std::invalid_argument("search limit must not be zero");
        }
        if(limit > kMdbxResourceImporterMaxSearchLimit) {
            throw std::invalid_argument("search limit exceeds the importer candidate bound");
        }
        std::size_t candidate_limit = limit;
        for(;;) {
            auto lexical_hits = m_lexical.search(scope, query, candidate_limit);
            visible.clear();
            visible.reserve(std::min(limit, lexical_hits.size()));
            for(auto& hit : lexical_hits) {
                if(!m_intents.find(hit.resource_id.value())) {
                    visible.push_back(std::move(hit));
                    if(visible.size() == limit) break;
                }
            }
            if(visible.size() >= limit ||
               lexical_hits.size() < candidate_limit ||
               candidate_limit >= kMdbxResourceImporterMaxSearchLimit) {
                break;
            }
            candidate_limit = std::min(
                kMdbxResourceImporterMaxSearchLimit,
                candidate_limit > kMdbxResourceImporterMaxSearchLimit / 2
                    ? kMdbxResourceImporterMaxSearchLimit
                    : candidate_limit * 2
            );
        }
        return visible;
    }

private:
    void inject(ResourceImporterFaultPoint point) const {
        if(m_fault_injector) m_fault_injector(point);
    }

    void validate_admission(const ObservedResource& resource) const {
        if(m_publication_profile == ResourceImportPublicationProfile::LexicalOnly) {
            return;
        }
        if(resource.utf8_body.size() > m_max_raw_body_bytes) {
            throw std::invalid_argument("resource body exceeds the raw publication byte limit");
        }
        const auto ranges = split_utf8_ranges(
            resource.utf8_body,
            kMdbxResourceImporterRawChunkBytes
        );
        if(ranges.size() > m_max_raw_chunks) {
            throw std::invalid_argument("resource exceeds the raw publication chunk limit");
        }
    }

    static void ensure_profile_compatibility(
        ResourceImportPublicationProfile required,
        ResourceImportPublicationProfile configured
    ) {
        if(required != configured) {
            throw std::runtime_error(
                "resource import publication profile mismatch; explicit migration is required"
            );
        }
    }

    ResourceImportResult complete(
        const PendingImport& pending,
        bool inject_faults,
        const std::string& expected_payload,
        const std::string& expected_token
    ) {
        ensure_profile_compatibility(
            pending.publication_profile,
            m_publication_profile
        );
        // Admission is part of the execution contract, not merely an
        // admission-time hint. A recovery executor with tighter limits must
        // fail closed before rewriting lexical data or creating raw units.
        validate_admission(pending.resource);
        const auto expected_manifest = ensure_manifest_compatibility(
            pending.resource,
            pending.publication_profile
        );
        assert_ownership(pending.resource.resource_id.value(), expected_token);
        if(inject_faults) inject(ResourceImporterFaultPoint::BeforeLexicalPublication);
        const auto lexical_started = std::chrono::steady_clock::now();
        const auto generation = m_lexical.upsert_text_guarded(
            pending.resource.scope_id,
            pending.resource.resource_id,
            pending.resource.source_uri,
            pending.resource.utf8_body,
            DurableLexicalPublicationGuard{
                m_intent_table_name,
                pending.resource.resource_id.value(),
                expected_payload,
                m_token_table_name,
                expected_token
            }
        );
        record_duration(
            m_diagnostics ? &m_diagnostics->lexical_ns : nullptr,
            lexical_started
        );
        if(inject_faults) inject(ResourceImporterFaultPoint::AfterLexicalPublication);
        if(inject_faults) inject(ResourceImporterFaultPoint::BeforeManifestPublication);
        const auto digest = body_digest(pending.resource.utf8_body);
        std::vector<KnowledgeUnitId> raw_units;
        if(m_knowledge_unit_store) {
            const auto ranges = split_utf8_ranges(
                pending.resource.utf8_body,
                kMdbxResourceImporterRawChunkBytes
            );
            raw_units.reserve(ranges.size());
            for(std::size_t batch_begin = 0; batch_begin < ranges.size();
                batch_begin += m_raw_chunk_batch_size) {
                const auto batch_end = std::min(
                    ranges.size(), batch_begin + m_raw_chunk_batch_size
                );
                // Fencing is checked before and after every durable batch.
                // A lease takeover may leave an idempotent staging batch, but
                // the stale executor must never continue toward publication.
                assert_ownership(
                    pending.resource.resource_id.value(),
                    expected_token
                );
                std::vector<KnowledgeUnitCreateRequest> requests;
                requests.reserve(batch_end - batch_begin);
                for(std::size_t index = batch_begin; index < batch_end; ++index) {
                    const auto range = ranges[index];
                    const auto chunk = pending.resource.utf8_body.substr(
                        static_cast<std::size_t>(range.offset),
                        static_cast<std::size_t>(range.length)
                    );
                    const auto preview_length = bounded_utf8_prefix(
                        chunk,
                        kMaxRawChunkPreviewBytes
                    );
                    if(preview_length == 0) {
                        throw std::runtime_error("raw Chunk preview is empty");
                    }
                    SourceRefSummary source;
                    source.resource_id = pending.resource.resource_id;
                    source.reference_mode = SourceReferenceMode::RevisionBoundQuote;
                    source.resource_revision = ResourceRevisionRef{
                        pending.resource.resource_id,
                        generation,
                        digest
                    };
                    source.uri = pending.resource.source_uri;
                    source.excerpt = range;
                    source.preview.assign(chunk.data(), preview_length);
                    source.quote_hash = compute_quote_hash(source.preview);
                    source.confidence = 1.0;
                    source.text_origin = pending.resource.text_origin;

                    KnowledgeUnitCreateRequest request;
                    request.kind = KnowledgeUnitKind::Chunk;
                    request.scope_id = pending.resource.scope_id;
                    request.primary_text = std::string{chunk};
                    request.display_text = request.primary_text;
                    request.sources = {std::move(source)};
                    requests.push_back(std::move(request));
                }
                const auto raw_started = std::chrono::steady_clock::now();
                const auto envelopes = m_knowledge_unit_store->create_or_get_units_guarded(
                    std::move(requests),
                    MdbxKnowledgeUnitPublicationGuard{
                        m_token_table_name,
                        pending.resource.resource_id.value(),
                        expected_token
                    }
                );
                record_duration(
                    m_diagnostics ? &m_diagnostics->raw_unit_store_ns : nullptr,
                    raw_started
                );
                if(m_diagnostics) ++m_diagnostics->raw_store_calls;
                if(envelopes.size() != batch_end - batch_begin) {
                    throw std::runtime_error("raw publication returned an incomplete batch");
                }
                for(const auto& envelope : envelopes) {
                    if(envelope.lifecycle_state != KnowledgeUnitLifecycleState::Active) {
                        throw std::runtime_error(
                            "raw publication matched a non-active historical Chunk"
                        );
                    }
                    raw_units.push_back(envelope.id);
                    if(m_projection_store) {
                        // Projections are durable staging rows. The manifest
                        // below is the sole publication boundary, so an
                        // interrupted run can leave rows that recovery
                        // replays idempotently without making them visible.
                        // Projection revision follows the persisted envelope
                        // revision. Mutable envelope updates therefore create
                        // a new immutable projection key instead of trying to
                        // overwrite an older projection with a new freshness
                        // token.
                        m_projection_store->put(make_original_projection(
                            envelope, envelope.revision
                        ));
                    }
                }
                assert_ownership(pending.resource.resource_id.value(), expected_token);
                if(inject_faults) {
                    // A fault after this point models interruption after the
                    // whole durable batch, not after an individual envelope.
                    inject(ResourceImporterFaultPoint::AfterRawUnitPublication);
                }
            }
        }
        const auto manifest = make_manifest(
            pending.resource,
            generation,
            digest,
            raw_units,
            pending.publication_profile,
            m_table_prefix
        );
        ensure_manifest_compatibility(
            pending.resource,
            manifest.revision,
            digest,
            pending.publication_profile
        );
        assert_ownership(pending.resource.resource_id.value(), expected_token);
        const auto manifest_started = std::chrono::steady_clock::now();
        m_manifests.upsert_manifest_if_unchanged_guarded(
            manifest,
            expected_manifest,
            MdbxManifestPublicationGuard{
                m_intent_table_name,
                pending.resource.resource_id.value(),
                expected_payload,
                m_token_table_name,
                expected_token
            }
        );
        record_duration(
            m_diagnostics ? &m_diagnostics->manifest_ns : nullptr,
            manifest_started
        );
        if(inject_faults) inject(ResourceImporterFaultPoint::AfterManifestPublication);
        release_expected(pending.resource.resource_id.value(), expected_token);
        return ResourceImportResult{
            pending.resource.resource_id,
            manifest.revision,
            document_id(pending.resource),
            chunk_id(pending.resource),
            std::move(raw_units)
        };
    }

    void recover_pending() {
        std::vector<std::pair<std::string, std::string>> pending;
        m_intents.load(pending);
        for(const auto& item : pending) {
            const auto operation = deserialize_intent(item.second);
            if(item.first != operation.resource.resource_id.value()) {
                throw std::runtime_error("resource import intent key does not match its payload");
            }
            if(operation.lease_expires_at_ms > now_ms()) {
                continue;
            }
            ensure_profile_compatibility(
                operation.publication_profile,
                m_publication_profile
            );
            ensure_manifest_compatibility(
                operation.resource,
                operation.publication_profile
            );
            const auto takeover = takeover_pending(item.first, item.second, operation);
            complete(
                takeover.operation,
                false,
                takeover.payload,
                takeover.token
            );
        }
    }

    ClaimedImport claim_import(const ObservedResource& resource) {
        const auto key = resource.resource_id.value();
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto existing_payload = m_intents.find(key, txn);
        if(existing_payload) {
            auto existing = deserialize_intent(*existing_payload);
            const auto stored_token = m_tokens.find(key, txn);
            if(!stored_token || *stored_token != fencing_token(*existing_payload)) {
                txn.rollback();
                throw std::runtime_error(
                    "resource import fencing token is missing or does not match intent"
                );
            }
            if(existing.owner_id == m_owner_id &&
               same_import_request(existing.resource, resource)) {
                existing.lease_expires_at_ms = lease_deadline(now_ms(), m_lease_ms);
                auto renewed_payload = serialize_intent(existing);
                const auto renewed_token = fencing_token(renewed_payload);
                m_intents.insert_or_assign(key, renewed_payload, txn);
                m_tokens.insert_or_assign(key, renewed_token, txn);
                txn.commit();
                return {
                    std::move(existing),
                    std::move(renewed_payload),
                    std::move(renewed_token),
                    false
                };
            }
            const auto token = m_tokens.find(key, txn);
            if(!token || *token != fencing_token(*existing_payload)) {
                txn.rollback();
                throw std::runtime_error(
                    "resource import fencing token is missing or does not match intent"
                );
            }
            txn.commit();
            return {std::move(existing), *existing_payload, *token, false};
        }

        const auto binding = m_bindings.find(key, txn);
        if(binding && *binding != resource.scope_id.value()) {
            txn.rollback();
            throw std::invalid_argument("ResourceId is already bound to a different scope");
        }
        const auto epoch = next_epoch(key, txn);
        PendingImport claimed{
            resource,
            m_publication_profile,
            m_owner_id,
            epoch,
            lease_deadline(now_ms(), m_lease_ms)
        };
        auto payload = serialize_intent(claimed);
        if(!binding) {
            if(!m_bindings.insert(key, resource.scope_id.value(), txn)) {
                txn.rollback();
                throw std::runtime_error("resource scope binding could not be created");
            }
        }
        if(!m_intents.insert(key, payload, txn)) {
            txn.rollback();
            throw std::runtime_error("resource import intent was concurrently created");
        }
        const auto token = fencing_token(payload);
        m_tokens.insert_or_assign(key, token, txn);
        txn.commit();
        return {std::move(claimed), std::move(payload), std::move(token), true};
    }

    ClaimedImport takeover_pending(
        const std::string& key,
        const std::string& expected_payload,
        PendingImport operation
    ) {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto existing = m_intents.find(key, txn);
        if(!existing || *existing != expected_payload) {
            txn.rollback();
            throw std::runtime_error("resource import intent changed during recovery");
        }
        const auto stored_token = m_tokens.find(key, txn);
        if(!stored_token || *stored_token != fencing_token(*existing)) {
            txn.rollback();
            throw std::runtime_error(
                "resource import fencing token is missing or does not match intent"
            );
        }
        if(operation.lease_expires_at_ms > now_ms()) {
            txn.rollback();
            throw std::runtime_error("live resource import intent cannot be taken over");
        }
        const auto binding = m_bindings.find(key, txn);
        if(!binding || *binding != operation.resource.scope_id.value()) {
            txn.rollback();
            throw std::runtime_error("resource import scope binding is missing or inconsistent");
        }
        const auto stored_epoch = m_epochs.find(key, txn);
        if(!stored_epoch ||
           parse_decimal_u64(*stored_epoch, "resource import fencing epoch") <
               operation.fencing_epoch) {
            txn.rollback();
            throw std::runtime_error("resource import fencing epoch regressed");
        }
        operation.owner_id = m_owner_id;
        operation.fencing_epoch = next_epoch(key, txn);
        operation.lease_expires_at_ms = lease_deadline(now_ms(), m_lease_ms);
        const auto replacement = serialize_intent(operation);
        const auto token = fencing_token(replacement);
        m_intents.insert_or_assign(key, replacement, txn);
        m_tokens.insert_or_assign(key, token, txn);
        txn.commit();
        return {std::move(operation), replacement, token, false};
    }

    std::uint64_t next_epoch(
        const std::string& key,
        const mdbxc::Transaction& txn
    ) {
        const auto stored = m_epochs.find(key, txn);
        const auto previous = stored
            ? parse_decimal_u64(*stored, "resource import fencing epoch")
            : 0;
        if(previous == (std::numeric_limits<std::uint64_t>::max)()) {
            throw std::runtime_error("resource import fencing epoch overflow");
        }
        const auto next = previous + 1;
        m_epochs.insert_or_assign(key, std::to_string(next), txn);
        return next;
    }

    std::uint64_t now_ms() const {
        return m_clock();
    }

    void assert_ownership(const std::string& key, const std::string& payload) const {
        std::optional<std::string> current;
        if(!m_diagnostics) {
            auto txn = m_context->connection()->transaction(
                mdbxc::TransactionMode::READ_ONLY
            );
            current = m_tokens.find(key, txn);
            txn.commit();
            if(!current || *current != payload) {
                throw std::runtime_error("resource import executor was fenced out");
            }
            return;
        }
        const auto total_started = std::chrono::steady_clock::now();
        const auto lookup_started = std::chrono::steady_clock::now();
        auto txn = m_context->connection()->transaction(
            mdbxc::TransactionMode::READ_ONLY
        );
        current = m_tokens.find(key, txn);
        txn.commit();
        const auto lookup_finished = std::chrono::steady_clock::now();
        const auto compare_started = lookup_finished;
        const bool matches = current && *current == payload;
        const auto compare_finished = std::chrono::steady_clock::now();
        ++m_diagnostics->ownership_lookup_attempts;
        ++m_diagnostics->ownership_checks;
        if(current) {
            ++m_diagnostics->ownership_payload_materializations;
            m_diagnostics->ownership_payload_bytes += current->size();
        }
        record_duration(
            &m_diagnostics->ownership_lookup_ns,
            lookup_started,
            lookup_finished
        );
        record_duration(
            &m_diagnostics->ownership_compare_ns,
            compare_started,
            compare_finished
        );
        record_duration(
            &m_diagnostics->ownership_total_ns,
            total_started,
            compare_finished
        );
        if(!matches) {
            throw std::runtime_error("resource import executor was fenced out");
        }
    }

    static void record_duration(
        std::atomic<std::uint64_t>* destination,
        std::chrono::steady_clock::time_point started
    ) {
        record_duration(destination, started, std::chrono::steady_clock::now());
    }

    static void record_duration(
        std::atomic<std::uint64_t>* destination,
        std::chrono::steady_clock::time_point started,
        std::chrono::steady_clock::time_point finished
    ) {
        if(destination) {
            *destination += static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    finished - started
                ).count()
            );
        }
    }

    void release_expected(const std::string& key, const std::string& token) {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto existing = m_intents.find(key, txn);
        if(!existing) {
            txn.rollback();
            throw std::runtime_error("resource import intent disappeared before release");
        }
        const auto existing_token = m_tokens.find(key, txn);
        if(!existing_token || *existing_token != token ||
           *existing_token != fencing_token(*existing)) {
            txn.rollback();
            throw std::runtime_error("resource import fencing token changed during publication");
        }
        if(!m_intents.erase(key, txn)) {
            txn.rollback();
            throw std::runtime_error("resource import intent could not be released");
        }
        if(!m_tokens.erase(key, txn)) {
            txn.rollback();
            throw std::runtime_error("resource import fencing token could not be released");
        }
        txn.commit();
    }

    std::optional<ResourceManifest> ensure_manifest_compatibility(
        const ObservedResource& resource,
        ResourceImportPublicationProfile profile
    ) const {
        const auto existing = m_manifests.find_manifest(resource.resource_id);
        if(!existing) return std::nullopt;
        if(existing->payload_version != ResourceManifestPayloadVersion::V6) {
            throw std::runtime_error(
                "legacy resource manifest lacks verified text-origin provenance; "
                "export and explicitly migrate it before importing"
            );
        }
        if(profile == ResourceImportPublicationProfile::LexicalOnly &&
           manifest_has_raw_bindings(*existing)) {
            throw std::runtime_error(
                "raw publication manifest cannot be downgraded to lexical-only"
            );
        }
        const auto expected_schema = publication_schema_id(profile, m_table_prefix);
        if(existing->schema.schema_id != expected_schema ||
           existing->schema.schema_version != 1) {
            throw std::runtime_error(
                "resource manifest belongs to a foreign schema and cannot be overwritten"
            );
        }
        const auto digest = body_digest(resource.utf8_body);
        if(existing->revision.body_digest &&
           existing->revision.body_digest->bytes == digest.bytes &&
           (existing->revision.pipeline_config_hash != resource.pipeline_config_hash ||
            !same_provenance(
                existing->revision.text_origin,
                existing->revision.derived_text_provenance,
                resource.text_origin,
                resource.derived_text_provenance
            ))) {
            throw std::runtime_error(
                "resource revision metadata changed before canonical publication: " +
                std::to_string(existing->revision.pipeline_config_hash) + " -> " +
                std::to_string(resource.pipeline_config_hash)
            );
        }
        return existing;
    }

    void ensure_manifest_compatibility(
        const ObservedResource& resource,
        const ResourceRevision& revision,
        const ResourceBodyDigest& digest,
        ResourceImportPublicationProfile profile
    ) const {
        const auto existing = m_manifests.find_manifest(resource.resource_id);
        if(!existing) return;
        if(existing->payload_version != ResourceManifestPayloadVersion::V6) {
            throw std::runtime_error(
                "legacy resource manifest lacks verified text-origin provenance; "
                "export and explicitly migrate it before importing"
            );
        }
        if(profile == ResourceImportPublicationProfile::LexicalOnly &&
           manifest_has_raw_bindings(*existing)) {
            throw std::runtime_error(
                "raw publication manifest cannot be downgraded to lexical-only"
            );
        }
        const auto expected_schema = publication_schema_id(profile, m_table_prefix);
        if(existing->schema.schema_id != expected_schema ||
           existing->schema.schema_version != 1) {
            throw std::runtime_error(
                "resource manifest belongs to a foreign schema and cannot be overwritten"
            );
        }
        if(existing->revision.generation == revision.generation &&
           (existing->revision.pipeline_config_hash != revision.pipeline_config_hash ||
            !same_provenance(
                existing->revision.text_origin,
                existing->revision.derived_text_provenance,
                revision.text_origin,
                revision.derived_text_provenance
            ) ||
            !existing->revision.body_digest ||
            existing->revision.body_digest->bytes != digest.bytes)) {
            throw std::runtime_error(
                "resource revision metadata changed during canonical publication"
            );
        }
    }

    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxDurableLexicalDocumentMemory m_lexical;
    MdbxResourceManifestStorage m_manifests;
    std::shared_ptr<MdbxKnowledgeUnitStore> m_knowledge_unit_store;
    std::shared_ptr<MdbxProjectionStore> m_projection_store;
    std::string m_intent_table_name;
    std::string m_token_table_name;
    mdbxc::KeyValueTable<std::string, std::string> m_intents;
    mdbxc::KeyValueTable<std::string, std::string> m_tokens;
    mdbxc::KeyValueTable<std::string, std::string> m_bindings;
    mdbxc::KeyValueTable<std::string, std::string> m_epochs;
    std::uint64_t m_lease_ms;
    ResourceImportPublicationProfile m_publication_profile;
    std::string m_table_prefix;
    std::size_t m_max_raw_body_bytes;
    std::size_t m_max_raw_chunks;
    std::size_t m_raw_chunk_batch_size;
    std::function<std::uint64_t()> m_clock;
    std::function<void(ResourceImporterFaultPoint)> m_fault_injector;
    std::shared_ptr<MdbxResourceImporterDiagnostics> m_diagnostics;
    std::string m_owner_id;
    mutable std::mutex m_mutex;
};

void MdbxResourceImporter::migrate_legacy_fencing_state(
    const std::shared_ptr<MdbxStorageContext>& context,
    std::string_view table_prefix
) {
    if(!context) {
        throw std::invalid_argument("legacy fencing migration requires a storage context");
    }
    mdbxc::KeyValueTable<std::string, std::string> marker(
        context->connection(),
        table_name(std::string{table_prefix}, "resource_import_fencing_schema")
    );
    mdbxc::KeyValueTable<std::string, std::string> intents(
        context->connection(),
        table_name(std::string{table_prefix}, "resource_import_intents")
    );
    mdbxc::KeyValueTable<std::string, std::string> tokens(
        context->connection(),
        table_name(std::string{table_prefix}, kFencingTokenTableSuffix)
    );
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto stored_marker = marker.find(std::string{kFencingSchemaKey}, txn);
    if(stored_marker && *stored_marker != kFencingSchemaVersion) {
        txn.rollback();
        throw std::runtime_error("unsupported resource import fencing schema marker");
    }
    std::vector<std::pair<std::string, std::string>> existing_intents;
    std::vector<std::pair<std::string, std::string>> existing_tokens;
    intents.load(existing_intents, txn);
    tokens.load(existing_tokens, txn);
    for(const auto& item : existing_intents) {
        // Parsing is intentional: only legacy v3/v4 intent envelopes are
        // eligible for this narrow administrative migration.
        static_cast<void>(deserialize_intent(item.second));
        const auto expected = fencing_token(item.second);
        const auto existing = tokens.find(item.first, txn);
        if(existing && *existing != expected) {
            txn.rollback();
            throw std::runtime_error(
                "resource import fencing token is missing or does not match intent"
            );
        }
        if(!existing) tokens.insert_or_assign(item.first, expected, txn);
    }
    for(const auto& item : existing_tokens) {
        if(!intents.find(item.first, txn)) {
            txn.rollback();
            throw std::runtime_error("resource import fencing token is orphaned");
        }
    }
    if(!stored_marker) {
        marker.insert_or_assign(
            std::string{kFencingSchemaKey},
            std::string{kFencingSchemaVersion},
            txn
        );
    }
    txn.commit();
}

MdbxResourceImporter::MdbxResourceImporter(MdbxResourceImporterOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxResourceImporter::~MdbxResourceImporter() = default;

ResourceImportResult MdbxResourceImporter::import(ObservedResource resource) {
    return m_impl->import(std::move(resource));
}

std::optional<ResourceManifest> MdbxResourceImporter::find_manifest(
    const ResourceId& resource_id
) const {
    return m_impl->find_manifest(resource_id);
}

std::vector<DurableLexicalDocumentHit> MdbxResourceImporter::search(
    const ScopeId& scope_id,
    std::string_view query,
    std::size_t limit
) const {
    return m_impl->search(scope_id, query, limit);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
