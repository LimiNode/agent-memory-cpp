#include "MdbxDerivedPublicationStore.hpp"
#include "MdbxResourceFrontierSnapshot.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/domain/Sha256.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <unordered_set>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kPayloadVersion = "agent_memory.derived_publication.v1";
constexpr std::string_view kKeyPrefix = "derived_publication:";
constexpr std::uint8_t kActiveMarker = 1;
constexpr std::uint64_t kMaxInputs = 1'000'000;
constexpr std::size_t kMaxScopeBytes = 256;
constexpr std::size_t kMaxPublicationIdBytes = 256;
constexpr std::size_t kMaxBodyBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaxManifestBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaxFrontierBytes = 1U * 1024U * 1024U;
constexpr std::size_t kMaxSourceDigestBytes = 4U * 1024U;
constexpr std::size_t kMaxMarkerBytes = 256;
constexpr std::size_t kMaxSerializedBytes = 32U * 1024U * 1024U;

std::string table_name(const std::string& prefix) {
    std::string value = prefix.empty() ? "agent_memory" : prefix;
    for(char& character : value) {
        if(!std::isalnum(static_cast<unsigned char>(character)) && character != '_')
            character = '_';
    }
    return value + "_unit_projections";
}

void append_u8(std::string& out, std::uint8_t value) { out.push_back(static_cast<char>(value)); }

void append_u64(std::string& out, std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8)
        out.push_back(static_cast<char>((value >> shift) & 0xffU));
}

void append_digest(std::string& out, const std::array<std::uint8_t, 32>& digest) {
    out.append(reinterpret_cast<const char*>(digest.data()), digest.size());
}

void append_string(std::string& out, std::string_view value) {
    out += std::to_string(value.size());
    out.push_back(':');
    out.append(value.data(), value.size());
}

std::uint8_t read_u8(std::string_view payload, std::size_t& position) {
    if(position >= payload.size()) throw std::runtime_error("derived publication payload ended before marker");
    return static_cast<std::uint8_t>(payload[position++]);
}

std::uint64_t read_u64(std::string_view payload, std::size_t& position) {
    if(position > payload.size() || payload.size() - position < 8)
        throw std::runtime_error("derived publication payload ended before an integer");
    std::uint64_t value = 0;
    for(int index = 0; index < 8; ++index)
        value = (value << 8U) | static_cast<std::uint8_t>(payload[position++]);
    return value;
}

std::array<std::uint8_t, 32> read_digest(std::string_view payload, std::size_t& position) {
    if(position > payload.size() || payload.size() - position < 32)
        throw std::runtime_error("derived publication payload ended before digest");
    std::array<std::uint8_t, 32> value{};
    std::memcpy(value.data(), payload.data() + position, value.size());
    position += value.size();
    return value;
}

std::string read_string(std::string_view payload, std::size_t& position,
                        std::size_t max_size, const char* field_name) {
    if(position >= payload.size()) throw std::runtime_error("derived publication payload ended");
    std::size_t size = 0;
    bool digit = false;
    while(position < payload.size() && payload[position] != ':') {
        const auto c = static_cast<unsigned char>(payload[position++]);
        if(c < '0' || c > '9') throw std::runtime_error("derived publication string size is invalid");
        digit = true;
        const auto value = static_cast<std::size_t>(c - '0');
        if(size > (std::numeric_limits<std::size_t>::max() - value) / 10U)
            throw std::runtime_error("derived publication string size overflows");
        size = size * 10U + value;
    }
    if(!digit || position >= payload.size()) throw std::runtime_error("derived publication string size missing");
    ++position;
    if(size > payload.size() - position)
        throw std::runtime_error("derived publication string is truncated");
    if(size > max_size)
        throw std::runtime_error(std::string("derived publication ") + field_name + " exceeds its byte budget");
    std::string value{payload.data() + position, size};
    position += size;
    return value;
}

std::string base_key(const std::string& publication_id) {
    std::string key{kKeyPrefix};
    append_string(key, publication_id);
    return key;
}

std::string field_key(const std::string& publication_id, char field) {
    auto key = base_key(publication_id);
    key.push_back(field);
    return key;
}

void validate_input(const DerivedPublicationInput& input) {
    if(input.unit_id.empty() || input.unit_revision == 0 || input.source_generation == 0 || input.source_digest.empty())
        throw std::invalid_argument("derived publication input is incomplete");
}

void validate_request(const DerivedPublicationRequest& request, bool require_body = true) {
    if(request.scope_id.empty() || request.publication_id.empty() || request.output_revision == 0 ||
       request.generation == 0 || (require_body && request.body.empty()) || !is_valid_projection_kind(request.kind) ||
       request.kind == ProjectionKind::Original || request.inputs.empty() ||
       request.inputs.size() > kMaxInputs || request.dependency_frontier.empty() ||
       request.scope_id.value().size() > kMaxScopeBytes ||
       request.publication_id.size() > kMaxPublicationIdBytes ||
       request.dependency_frontier.size() > kMaxFrontierBytes ||
       request.body.size() > kMaxBodyBytes)
        throw std::invalid_argument("invalid derived publication request");
    for(const auto& input : request.inputs) {
        validate_input(input);
        if(input.source_digest.size() > kMaxSourceDigestBytes)
            throw std::invalid_argument("derived publication source digest exceeds its byte budget");
    }
}

bool add_size(std::size_t& total, std::size_t amount) {
    if(amount > std::numeric_limits<std::size_t>::max() - total) return false;
    total += amount;
    return true;
}

bool add_encoded_string_size(std::size_t& total, std::string_view value) {
    const auto length_digits = std::to_string(value.size()).size();
    return add_size(total, length_digits) && add_size(total, 1U) && add_size(total, value.size());
}

std::size_t estimate_manifest_size(const DerivedPublicationRequest& request) {
    std::size_t total = 0;
    if(!add_encoded_string_size(total, kPayloadVersion) ||
       !add_encoded_string_size(total, request.scope_id.value()) ||
       !add_encoded_string_size(total, request.publication_id) ||
       !add_size(total, 8U * 3U) || // kind, output revision and generation
       !add_encoded_string_size(total, request.dependency_frontier) ||
       !add_size(total, 32U) || // body digest
       !add_size(total, 8U)) // input count
        throw std::invalid_argument("derived publication manifest size overflows");
    for(const auto& input : request.inputs) {
        if(!add_size(total, 8U * 3U) || !add_encoded_string_size(total, input.source_digest))
            throw std::invalid_argument("derived publication manifest size overflows");
    }
    if(total > kMaxManifestBytes)
        throw std::invalid_argument("derived publication manifest exceeds its byte budget");
    return total;
}

std::string serialize_manifest(const DerivedPublicationRequest& request,
                               const std::array<std::uint8_t, 32>& body_digest) {
    const auto estimated_size = estimate_manifest_size(request);
    std::string payload;
    payload.reserve(estimated_size);
    append_string(payload, kPayloadVersion);
    append_string(payload, request.scope_id.value());
    append_string(payload, request.publication_id);
    append_u64(payload, static_cast<std::uint16_t>(request.kind));
    append_u64(payload, request.output_revision);
    append_u64(payload, request.generation);
    append_string(payload, request.dependency_frontier);
    append_digest(payload, body_digest);
    append_u64(payload, request.inputs.size());
    for(const auto& input : request.inputs) {
        append_u64(payload, input.unit_id.value());
        append_u64(payload, input.unit_revision);
        append_u64(payload, input.source_generation);
        append_string(payload, input.source_digest);
    }
    if(payload.size() != estimated_size || payload.size() > kMaxManifestBytes)
        throw std::invalid_argument("derived publication manifest exceeds its byte budget");
    return payload;
}

DerivedPublicationRequest deserialize_manifest(std::string_view payload,
                                               std::array<std::uint8_t, 32>& body_digest) {
    std::size_t position = 0;
    if(payload.size() > kMaxManifestBytes)
        throw std::runtime_error("derived publication manifest exceeds its byte budget");
    if(read_string(payload, position, kPayloadVersion.size(), "payload version") != kPayloadVersion)
        throw std::runtime_error("unsupported derived publication payload version");
    DerivedPublicationRequest request;
    request.scope_id = ScopeId{read_string(payload, position, kMaxScopeBytes, "scope")};
    request.publication_id = read_string(payload, position, kMaxPublicationIdBytes, "publication id");
    const auto raw_kind = read_u64(payload, position);
    if(raw_kind > std::numeric_limits<std::uint16_t>::max())
        throw std::runtime_error("derived publication kind is out of range");
    request.kind = static_cast<ProjectionKind>(static_cast<std::uint16_t>(raw_kind));
    request.output_revision = read_u64(payload, position);
    request.generation = read_u64(payload, position);
    request.dependency_frontier = read_string(payload, position, kMaxFrontierBytes, "dependency frontier");
    body_digest = read_digest(payload, position);
    const auto count = read_u64(payload, position);
    if(count == 0 || count > kMaxInputs) throw std::runtime_error("derived publication input list is invalid");
    request.inputs.reserve(static_cast<std::size_t>(count));
    for(std::uint64_t i = 0; i < count; ++i) {
        DerivedPublicationInput input;
        input.unit_id = KnowledgeUnitId{read_u64(payload, position)};
        input.unit_revision = read_u64(payload, position);
        input.source_generation = read_u64(payload, position);
        input.source_digest = read_string(payload, position, kMaxSourceDigestBytes, "source digest");
        validate_input(input);
        request.inputs.push_back(std::move(input));
    }
    if(position != payload.size()) throw std::runtime_error("derived publication manifest has trailing bytes");
    validate_request(request, false);
    return request;
}

std::string serialize_marker(const std::string& manifest, const std::string& body) {
    const auto manifest_digest = sha256(manifest);
    const auto body_digest = sha256(body);
    std::string marker;
    append_string(marker, kPayloadVersion);
    append_u8(marker, kActiveMarker);
    append_digest(marker, manifest_digest);
    append_digest(marker, body_digest);
    return marker;
}

std::string digest_token(const ResourceBodyDigest& digest) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string value{"sha256:"};
    value.reserve(71);
    for(const auto byte : digest.bytes) {
        value.push_back(kHex[(byte >> 4U) & 0x0fU]);
        value.push_back(kHex[byte & 0x0fU]);
    }
    return value;
}

bool same_root_revision(const ResourceRevision& left, const ResourceRevision& right) {
    if(left.resource_id != right.resource_id || left.generation != right.generation
       || left.body_digest.has_value() != right.body_digest.has_value()) {
        return false;
    }
    if(!left.body_digest) return true;
    return left.body_digest->algorithm == right.body_digest->algorithm
        && left.body_digest->bytes == right.body_digest->bytes;
}

bool same_root_revision(const ResourceRevisionRef& left, const ResourceRevision& right) {
    return left.resource_id == right.resource_id
        && left.generation == right.generation
        && right.body_digest
        && left.body_digest.algorithm == right.body_digest->algorithm
        && left.body_digest.bytes == right.body_digest->bytes;
}

void validate_marker(std::string_view payload, std::string_view manifest, std::string_view body) {
    if(payload.size() > kMaxMarkerBytes || manifest.size() > kMaxManifestBytes || body.size() > kMaxBodyBytes)
        throw std::runtime_error("derived publication payload exceeds its byte budget");
    std::size_t position = 0;
    if(read_string(payload, position, kPayloadVersion.size(), "payload version") != kPayloadVersion ||
       read_u8(payload, position) != kActiveMarker)
        throw std::runtime_error("derived publication marker is not active");
    if(read_digest(payload, position) != sha256(manifest) || read_digest(payload, position) != sha256(body) ||
       position != payload.size())
        throw std::runtime_error("derived publication marker integrity mismatch");
}

bool same_inputs(const std::vector<DerivedPublicationInput>& left,
                 const std::vector<DerivedPublicationInput>& right) {
    if(left.size() != right.size()) return false;
    return std::equal(left.begin(), left.end(), right.begin(), [](const auto& a, const auto& b) {
        return a.unit_id == b.unit_id && a.unit_revision == b.unit_revision &&
               a.source_generation == b.source_generation && a.source_digest == b.source_digest;
    });
}

} // namespace

class MdbxDerivedPublicationStore::Impl final {
public:
    explicit Impl(MdbxDerivedPublicationStoreOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(std::move(options.path), options.relative_to_exe, options.max_dbs)),
          table(context->connection(), table_name(options.table_prefix)),
          table_prefix(std::move(options.table_prefix)),
          fault_injector(std::move(options.fault_injector)) {}

    std::shared_ptr<MdbxStorageContext> context;
    mutable mdbxc::KeyValueTable<std::string, std::string> table;
    std::string table_prefix;
    std::function<void(MdbxDerivedPublicationFaultPoint)> fault_injector;

    void publish_in_transaction(
        const DerivedPublicationRequest& request,
        const std::string& manifest,
        const std::string& marker,
        mdbxc::Transaction& txn,
        const bool inject_faults
    ) {
        const auto body_key = field_key(request.publication_id, 'b');
        const auto manifest_key = field_key(request.publication_id, 'm');
        const auto marker_key = field_key(request.publication_id, 'a');
        const auto existing_body = table.find(body_key, txn);
        const auto existing_manifest = table.find(manifest_key, txn);
        const auto existing_marker = table.find(marker_key, txn);
        if(existing_body || existing_manifest || existing_marker) {
            if(!existing_body || !existing_manifest || !existing_marker
               || *existing_body != request.body
               || *existing_manifest != manifest || *existing_marker != marker) {
                throw std::invalid_argument(
                    "derived publication identity already has different content");
            }
            return;
        }
        table.insert_or_assign(body_key, request.body, txn);
        if(inject_faults && fault_injector) {
            fault_injector(MdbxDerivedPublicationFaultPoint::AfterBodyWrite);
        }
        table.insert_or_assign(manifest_key, manifest, txn);
        if(inject_faults && fault_injector) {
            fault_injector(MdbxDerivedPublicationFaultPoint::AfterManifestWrite);
        }
        table.insert_or_assign(marker_key, marker, txn);
    }
};

MdbxDerivedPublicationStore::MdbxDerivedPublicationStore(MdbxDerivedPublicationStoreOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxDerivedPublicationStore::~MdbxDerivedPublicationStore() = default;

void MdbxDerivedPublicationStore::publish(DerivedPublicationRequest request) {
    validate_request(request);
    const auto body_digest = sha256(request.body);
    const auto manifest = serialize_manifest(request, body_digest);
    const auto marker = serialize_marker(manifest, request.body);
    if(marker.size() > kMaxMarkerBytes || manifest.size() > kMaxManifestBytes ||
       request.body.size() > kMaxBodyBytes || manifest.size() > kMaxSerializedBytes - request.body.size() ||
       marker.size() > kMaxSerializedBytes - request.body.size() - manifest.size())
        throw std::invalid_argument("derived publication serialized payload exceeds its byte budget");
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    m_impl->publish_in_transaction(request, manifest, marker, txn, false);
    txn.commit();
}

MdbxFencedDerivedPublicationResult MdbxDerivedPublicationStore::publish_fenced(
    const MdbxResourceFrontierSnapshot& frontier,
    MdbxFencedDerivedPublicationRequest request
) {
    if(frontier.scope_id.empty() || frontier.entries.empty()
       || frontier.entries.size() > kMdbxResourceFrontierMaxInputs
       || request.dependency_frontier.empty()) {
        return {MdbxFencedDerivedPublicationStatus::InvalidInput, std::nullopt};
    }
    if(!frontier.authentic_for(m_impl->context, m_impl->table_prefix)) {
        return {MdbxFencedDerivedPublicationStatus::InvalidInput, std::nullopt};
    }

    DerivedPublicationRequest publication;
    publication.scope_id = frontier.scope_id;
    publication.publication_id = std::move(request.publication_id);
    publication.kind = request.kind;
    publication.output_revision = request.output_revision;
    publication.body = std::move(request.body);
    publication.generation = request.generation;
    publication.inputs.reserve(frontier.entries.size());
    std::unordered_set<std::uint64_t> unit_ids;
    unit_ids.reserve(frontier.entries.size());
    for(const auto& entry : frontier.entries) {
        if(entry.unit.id.empty()
           || !unit_ids.insert(entry.unit.id.value()).second
           || entry.unit.scope_id != frontier.scope_id
           || entry.unit.lifecycle_state != KnowledgeUnitLifecycleState::Active
           || entry.unit.revision == 0
           || entry.root.revision.resource_id.empty()
           || entry.root.revision.generation == 0
           || !entry.root.revision.body_digest
           || entry.root.state != ResourceManifestState::Active) {
            return {MdbxFencedDerivedPublicationStatus::InvalidInput, std::nullopt};
        }
        publication.inputs.push_back(DerivedPublicationInput{
            entry.unit.id,
            entry.unit.revision,
            entry.root.revision.generation,
            digest_token(*entry.root.revision.body_digest)
        });
    }
    publication.dependency_frontier = frontier.identity_token();
    publication.dependency_frontier.push_back('|');
    publication.dependency_frontier += request.dependency_frontier;

    try {
        validate_request(publication);
        const auto body_digest = sha256(publication.body);
        const auto serialized_manifest = serialize_manifest(publication, body_digest);
        const auto marker = serialize_marker(serialized_manifest, publication.body);
        if(marker.size() > kMaxMarkerBytes
           || serialized_manifest.size() > kMaxManifestBytes
           || publication.body.size() > kMaxBodyBytes) {
            return {MdbxFencedDerivedPublicationStatus::InvalidInput, std::nullopt};
        }

        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = m_impl->context;
        manifest_options.table_prefix = m_impl->table_prefix;
        MdbxResourceManifestStorage manifests(std::move(manifest_options));
        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = m_impl->context;
        unit_options.table_prefix = m_impl->table_prefix;
        MdbxKnowledgeUnitStore units(std::move(unit_options));
        auto txn = m_impl->context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        for(const auto& entry : frontier.entries) {
            const auto current_unit = units.find_in_transaction(entry.unit.id, txn);
            const auto current_root = manifests.find_manifest_in_transaction(
                entry.root.revision.resource_id, txn);
            if(!current_unit || !current_root
               || current_unit->scope_id != frontier.scope_id
               || current_unit->lifecycle_state != KnowledgeUnitLifecycleState::Active
               || current_unit->revision != entry.unit.revision
               || current_unit->sources.size() != 1
               || !current_unit->sources.front().resource_revision
               || !same_root_revision(
                   *current_unit->sources.front().resource_revision, entry.root.revision)
               || current_root->state != ResourceManifestState::Active
               || !same_root_revision(current_root->revision, entry.root.revision)) {
                return {MdbxFencedDerivedPublicationStatus::FenceRejected, std::nullopt};
            }
        }
        m_impl->publish_in_transaction(publication, serialized_manifest, marker, txn, true);
        txn.commit();
        return {MdbxFencedDerivedPublicationStatus::Published,
                DerivedPublication{std::move(publication)}};
    } catch(const std::invalid_argument&) {
        return {MdbxFencedDerivedPublicationStatus::InvalidInput, std::nullopt};
    } catch(const std::exception&) {
        return {MdbxFencedDerivedPublicationStatus::Unavailable, std::nullopt};
    }
}

DerivedPublicationReadResult MdbxDerivedPublicationStore::read(
    const ScopeId& access_scope,
    const std::string& publication_id,
    const std::vector<DerivedPublicationInput>& current_inputs,
    const std::string& current_dependency_frontier
) const {
    if(access_scope.empty() || publication_id.empty() || access_scope.value().size() > kMaxScopeBytes ||
       publication_id.size() > kMaxPublicationIdBytes || current_dependency_frontier.size() > kMaxFrontierBytes ||
       current_inputs.size() > kMaxInputs)
        throw std::invalid_argument("invalid derived publication read identity or byte budget");
    for(const auto& input : current_inputs) {
        validate_input(input);
        if(input.source_digest.size() > kMaxSourceDigestBytes)
            throw std::invalid_argument("derived publication source digest exceeds its byte budget");
    }
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto body = m_impl->table.find(field_key(publication_id, 'b'), txn);
    const auto manifest = m_impl->table.find(field_key(publication_id, 'm'), txn);
    const auto marker = m_impl->table.find(field_key(publication_id, 'a'), txn);
    if(!body && !manifest && !marker) {
        txn.commit();
        return {DerivedPublicationReadStatus::NotFound, std::nullopt};
    }
    if(!body || !manifest || !marker) {
        txn.commit();
        return {DerivedPublicationReadStatus::Unavailable, std::nullopt};
    }
    try {
        if(body->size() > kMaxBodyBytes || manifest->size() > kMaxManifestBytes ||
           marker->size() > kMaxMarkerBytes || body->size() > kMaxSerializedBytes - manifest->size() ||
           marker->size() > kMaxSerializedBytes - body->size() - manifest->size())
            throw std::runtime_error("derived publication serialized payload exceeds its byte budget");
        validate_marker(*marker, *manifest, *body);
        std::array<std::uint8_t, 32> body_digest{};
        auto request = deserialize_manifest(*manifest, body_digest);
        request.body = *body;
        validate_request(request);
        if(body_digest != sha256(*body) || request.publication_id != publication_id) throw std::runtime_error("derived publication body identity mismatch");
        txn.commit();
        if(request.scope_id != access_scope) return {DerivedPublicationReadStatus::ScopeMismatch, std::nullopt};
        if(request.dependency_frontier != current_dependency_frontier || !same_inputs(request.inputs, current_inputs))
            return {DerivedPublicationReadStatus::StoredMismatch, std::nullopt};
        return {DerivedPublicationReadStatus::StoredMatch, DerivedPublication{request}};
    } catch(const std::exception&) {
        txn.commit();
        return {DerivedPublicationReadStatus::Unavailable, std::nullopt};
    }
}

} // namespace agent_memory

#endif
