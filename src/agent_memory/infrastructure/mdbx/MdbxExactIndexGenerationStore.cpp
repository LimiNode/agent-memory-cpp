#include "MdbxExactIndexGenerationStore.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxProjectionStore.hpp"
#include "MdbxResourceManifestStorage.hpp"
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <set>
#include <utility>

namespace agent_memory {
namespace {
using Failure = IndexGenerationFailure;
[[noreturn]] void fail(Failure code, const char* message) { throw IndexGenerationError(code, message); }

// This small fixed-width codec is private to schema v1. Every variable field
// is length framed, every decode is bounded, and floats retain IEEE FP32 bits.
void number(std::string& out, std::uint64_t value) {
    for(unsigned i = 0; i < 8; ++i) out.push_back(static_cast<char>((value >> (i * 8U)) & 255U));
}
void field(std::string& out, const std::string& value) { number(out, value.size()); out += value; }
template<std::size_t N> std::string bytes(const std::array<std::uint8_t, N>& value) {
    return std::string(reinterpret_cast<const char*>(value.data()), value.size());
}
std::string digest(const std::string& value) { return bytes(sha256(value)); }
class Reader final {
public:
    explicit Reader(const std::string& value) : m_value(value) {}
    std::uint64_t number() {
        if(m_value.size() - m_position < 8) fail(Failure::Corrupt, "truncated generation integer");
        std::uint64_t result = 0;
        for(unsigned i = 0; i < 8; ++i)
            result |= std::uint64_t(static_cast<unsigned char>(m_value[m_position++])) << (8U * i);
        return result;
    }
    std::string field(std::size_t maximum = 65536) {
        const auto size = number();
        if(size > maximum || size > m_value.size() - m_position)
            fail(Failure::Corrupt, "invalid generation field length");
        const auto result = m_value.substr(m_position, static_cast<std::size_t>(size));
        m_position += static_cast<std::size_t>(size);
        return result;
    }
    void end() { if(m_position != m_value.size()) fail(Failure::Corrupt, "trailing generation bytes"); }
    float fp32() {
        if(m_value.size() - m_position < 4) fail(Failure::Corrupt, "truncated generation FP32");
        std::uint32_t bits = 0;
        for(unsigned i = 0; i < 4; ++i)
            bits |= std::uint32_t(static_cast<unsigned char>(m_value[m_position++])) << (8U * i);
        float result = 0; std::memcpy(&result, &bits, 4); return result;
    }
private:
    const std::string& m_value;
    std::size_t m_position = 0;
};
void validate_descriptor(const ExactIndexSpaceDescriptor& d) {
    if(d.schema_version != 1 || d.dimensions == 0 || d.dimensions > 4096 || d.scalar_bits != 32 ||
       (d.metric != "dot" && d.metric != "negative_squared_l2") || d.normalization != "none" ||
       d.codec != "fp32-le-v1" || d.projection_kind != ProjectionKind::Original)
        fail(Failure::Incompatible, "unsupported exact generation descriptor");
    if(d.model_id.empty() || d.recipe_id.empty() || d.scope_id.empty() ||
       d.model_id.size() > 256 || d.recipe_id.size() > 256 || d.scope_id.value().size() > 256)
        fail(Failure::InvalidInput, "missing or oversized space identity");
}
std::string encode_descriptor(const ExactIndexSpaceDescriptor& d) {
    validate_descriptor(d);
    std::string out;
    number(out, d.schema_version); field(out, d.model_id); field(out, d.recipe_id);
    number(out, d.dimensions); number(out, d.scalar_bits); field(out, d.metric);
    field(out, d.normalization); field(out, d.codec); field(out, d.scope_id.value());
    number(out, static_cast<std::uint64_t>(d.projection_kind));
    return out;
}
ExactIndexSpaceDescriptor decode_descriptor(const std::string& value) {
    Reader r(value);
    ExactIndexSpaceDescriptor d;
    const auto version = r.number();
    if(version != 1) fail(Failure::Incompatible, "unknown exact generation schema");
    d.model_id = r.field(256); d.recipe_id = r.field(256);
    const auto dimensions = r.number(), bits = r.number();
    if(dimensions > 4096 || bits != 32) fail(Failure::Incompatible, "unsupported vector layout");
    d.dimensions = static_cast<std::uint32_t>(dimensions);
    d.metric = r.field(64); d.normalization = r.field(64); d.codec = r.field(64);
    d.scope_id = ScopeId{r.field(256)};
    if(r.number() != static_cast<std::uint64_t>(ProjectionKind::Original))
        fail(Failure::Incompatible, "unsupported projection family");
    r.end(); validate_descriptor(d); return d;
}
void validate_vector(const std::vector<float>& values, const ExactIndexSpaceDescriptor& d) {
    if(values.empty()) fail(Failure::RebuildUnavailable, "exact retained vector input is unavailable");
    if(values.size() != d.dimensions) fail(Failure::Incompatible, "vector dimension mismatch");
    for(float value : values) if(!std::isfinite(value)) fail(Failure::InvalidInput, "nonfinite exact vector");
}
struct Row final {
    ExactIndexVectorInput input;
    std::string binding;
};
std::string encode_row(const Row& row) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559, "IEEE FP32 required");
    std::string out;
    number(out, row.input.unit_id.value()); number(out, row.input.unit_revision);
    field(out, row.binding); number(out, row.input.values.size());
    for(float value : row.input.values) {
        std::uint32_t bits = 0; std::memcpy(&bits, &value, 4);
        for(unsigned i = 0; i < 4; ++i) out.push_back(static_cast<char>((bits >> (i * 8U)) & 255U));
    }
    return out;
}
Row decode_row(const std::string& value, const ExactIndexSpaceDescriptor& d) {
    Reader r(value); Row row;
    row.input.unit_id = KnowledgeUnitId{r.number()}; row.input.unit_revision = r.number();
    row.binding = r.field();
    const auto count = r.number();
    // Validate count before allocation; all remaining bytes must be FP32 values.
    if(count != d.dimensions || value.size() < count * 4)
        fail(Failure::Corrupt, "invalid persisted vector dimension");
    for(std::uint64_t i = 0; i < count; ++i) row.input.values.push_back(r.fp32());
    r.end();
    if(row.input.unit_id.empty() || row.input.unit_revision == 0 || row.binding.empty())
        fail(Failure::Corrupt, "incomplete canonical candidate identity");
    validate_vector(row.input.values, d); return row;
}

// Canonical values come from existing authorities. The binding records exact
// resource/source/projection identity, not a caller's assertion of freshness.
std::string binding_for(const KnowledgeUnitEnvelope& unit, const SearchProjection& projection) {
    const auto& source = unit.sources.front();
    const auto& revision = *source.resource_revision;
    std::string out;
    field(out, "exact-original-binding-v1"); number(out, unit.id.value()); number(out, unit.revision);
    field(out, unit.scope_id.value()); field(out, bytes(unit.content_hash.bytes));
    field(out, source.resource_id.value()); field(out, revision.resource_id.value());
    number(out, revision.generation); number(out, static_cast<std::uint64_t>(revision.body_digest.algorithm));
    field(out, bytes(revision.body_digest.bytes)); field(out, source.uri);
    number(out, source.excerpt.offset); number(out, source.excerpt.length);
    field(out, bytes(source.quote_hash)); field(out, source.preview);
    number(out, static_cast<std::uint64_t>(source.reference_mode));
    number(out, static_cast<std::uint64_t>(source.text_origin));
    number(out, source.observed_at_ms.has_value()); number(out, source.observed_at_ms.value_or(0));
    std::uint64_t confidence = 0; static_assert(sizeof(double) == 8, "64-bit double required");
    std::memcpy(&confidence, &source.confidence, 8); number(out, confidence);
    number(out, projection.version.unit_revision_at_build); number(out, projection.version.projection_revision);
    number(out, projection.version.derivation_generation); field(out, projection.version.derivation_fingerprint);
    field(out, digest(projection.body));
    return out;
}
} // namespace

struct ExactIndexGenerationData final {
    std::string generation;
    ExactIndexSpaceDescriptor descriptor;
    std::string frontier;
    std::vector<Row> rows;
    std::weak_ptr<mdbxc::Connection> owner_connection;
    std::string owner_index;
    std::string owner_prefix;
};

class MdbxExactIndexGenerationStore::Impl final {
public:
    std::shared_ptr<MdbxStorageContext> context;
    std::string prefix, index;
    MdbxKnowledgeUnitStore units;
    MdbxProjectionStore projections;
    MdbxResourceManifestStorage manifests;
    mdbxc::KeyValueTable<std::string, std::string> table;
    std::function<void()> before_commit;

    static std::shared_ptr<MdbxStorageContext> checked(std::shared_ptr<MdbxStorageContext> value) {
        if(!value) fail(Failure::InvalidInput, "exact generation requires shared MDBX context");
        return value;
    }
    static std::string checked_prefix(std::string value) {
        if(value.empty() || value.size() > 64) fail(Failure::InvalidInput, "invalid table prefix");
        for(char c : value) if(!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                                (c >= '0' && c <= '9') || c == '_'))
            fail(Failure::InvalidInput, "table prefix must be ASCII alphanumeric or underscore");
        return value;
    }
    Impl(std::shared_ptr<MdbxStorageContext> c, std::string name, std::string p)
        : context(checked(std::move(c))), prefix(checked_prefix(std::move(p))), index(std::move(name)),
          units(unit_options()), projections(projection_options()), manifests(manifest_options()),
          table(context->connection(), prefix + "_exact_index_generations") {
        if(index.empty() || index.size() > 128) fail(Failure::InvalidInput, "invalid index name");
    }
    MdbxKnowledgeUnitStoreOptions unit_options() {
        MdbxKnowledgeUnitStoreOptions o; o.context = context; o.table_prefix = prefix; return o;
    }
    MdbxProjectionStoreOptions projection_options() {
        MdbxProjectionStoreOptions o; o.context = context; o.table_prefix = prefix; return o;
    }
    MdbxResourceManifestStorageOptions manifest_options() {
        MdbxResourceManifestStorageOptions o; o.context = context; o.table_prefix = prefix; return o;
    }
    std::string key(const std::string& generation, char kind, std::uint64_t ordinal = 0) const {
        if(generation.size() > 128) fail(Failure::InvalidInput, "generation identity too long");
        std::string out; field(out, index); field(out, generation); out.push_back(kind); number(out, ordinal);
        return out;
    }
    std::string required(const std::string& k, const mdbxc::Transaction& txn) const {
        const auto value = table.find(k, txn);
        if(!value) fail(Failure::Incomplete, "missing generation row");
        return *value;
    }
    std::pair<std::size_t, std::size_t> counts(const std::string& generation,
                                             const mdbxc::Transaction& txn) const {
        const auto payload = required(key(generation, 'p'), txn); Reader r(payload);
        const auto expected = r.number(), actual = r.number(); r.end();
        if(expected == 0 || expected > 1024 || actual > expected) fail(Failure::Corrupt, "invalid build counts");
        return {static_cast<std::size_t>(expected), static_cast<std::size_t>(actual)};
    }
    std::optional<std::pair<KnowledgeUnitEnvelope, std::string>> current(
        KnowledgeUnitId id, std::uint64_t revision, const ExactIndexSpaceDescriptor& d,
        const mdbxc::Transaction& txn) const {
        auto unit = units.find_in_transaction(id, txn, 1024U * 1024U);
        if(!unit || unit->id != id || unit->revision != revision || unit->scope_id != d.scope_id ||
           unit->lifecycle_state != KnowledgeUnitLifecycleState::Active || unit->sources.size() != 1 ||
           !is_valid_source_ref_summary(unit->sources.front()) ||
           unit->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
           !unit->sources.front().resource_revision) return std::nullopt;
        ProjectionAwareLexicalRetrieverOptions options;
        options.expected_manifest_schema_id = "agent_memory.m0_resource_importer.raw_chunks_original@" + prefix;
        std::vector<SearchProjection> original;
        const OriginalProjectionListReader read_projections = [&](const ScopeId& scope, const KnowledgeUnitId& uid,
                                                                 ProjectionKind kind) {
            original = projections.list_in_transaction(scope, uid, txn, kind); return original;
        };
        const OriginalManifestReader read_manifest = [&](const ResourceId& resource) {
            return manifests.find_manifest_in_transaction(resource, txn, 1024U * 1024U);
        };
        if(!has_current_original_projection_with_readers(*unit, read_projections, read_manifest, options))
            return std::nullopt;
        for(const auto& projection : original) {
            if(projection.version.unit_revision_at_build == revision &&
               projection.version.projection_revision == revision &&
               projection.version.derivation_generation == 1 &&
               projection.version.derivation_fingerprint == "m0.original.primary_text.v1" &&
               projection.body == unit->primary_text && is_valid_search_projection(projection))
                return std::make_pair(*unit, binding_for(*unit, projection));
        }
        return std::nullopt;
    }
    std::shared_ptr<ExactIndexGenerationData> load(const std::string& generation,
                                                 const mdbxc::Transaction& txn, bool check_current,
                                                 bool require_complete) const {
        auto data = std::make_shared<ExactIndexGenerationData>(); data->generation = generation;
        data->owner_connection = context->connection();
        data->owner_index = index;
        data->owner_prefix = prefix;
        const auto encoded = required(key(generation, 'd'), txn);
        data->descriptor = decode_descriptor(encoded);
        const auto count = counts(generation, txn);
        if(count.first != count.second) fail(Failure::Incomplete, "generation build is incomplete");
        Sha256Accumulator rows_hash, frontier;
        rows_hash.update(encoded);
        std::set<KnowledgeUnitId> seen;
        for(std::size_t i = 0; i < count.first; ++i) {
            const auto payload = required(key(generation, 'r', i), txn);
            auto row = decode_row(payload, data->descriptor);
            if(!seen.insert(row.input.unit_id).second) fail(Failure::Corrupt, "duplicate candidate identity");
            rows_hash.update(payload); frontier.update(row.binding);
            if(check_current) {
                const auto evidence = current(row.input.unit_id, row.input.unit_revision, data->descriptor, txn);
                if(!evidence || evidence->second != row.binding) fail(Failure::StaleBinding, "stale canonical binding");
            }
            data->rows.push_back(std::move(row));
        }
        data->frontier = bytes(frontier.finish());
        std::string completion; number(completion, 1); number(completion, count.first);
        field(completion, bytes(rows_hash.finish())); field(completion, data->frontier);
        if(require_complete && required(key(generation, 'c'), txn) != completion)
            fail(Failure::Corrupt, "generation completion checksum mismatch");
        return data;
    }
};

IndexGenerationError::IndexGenerationError(IndexGenerationFailure code, const std::string& message)
    : std::runtime_error(message), m_code(code) {}
ExactIndexGenerationSnapshot::ExactIndexGenerationSnapshot(std::shared_ptr<const ExactIndexGenerationData> data)
    : m_data(std::move(data)) {}
const std::string& ExactIndexGenerationSnapshot::generation_id() const { return m_data->generation; }
const ExactIndexSpaceDescriptor& ExactIndexGenerationSnapshot::descriptor() const { return m_data->descriptor; }
const std::string& ExactIndexGenerationSnapshot::source_frontier() const { return m_data->frontier; }
std::size_t ExactIndexGenerationSnapshot::size() const { return m_data->rows.size(); }
std::vector<ExactIndexVectorInput> ExactIndexGenerationSnapshot::retained_inputs() const {
    std::vector<ExactIndexVectorInput> result;
    result.reserve(m_data->rows.size());
    for(const auto& row : m_data->rows) result.push_back(row.input);
    return result;
}

MdbxExactIndexGenerationStore::MdbxExactIndexGenerationStore(std::shared_ptr<MdbxStorageContext> context,
    std::string index_name, std::string table_prefix)
    : m_impl(std::make_unique<Impl>(std::move(context), std::move(index_name), std::move(table_prefix))) {}
MdbxExactIndexGenerationStore::~MdbxExactIndexGenerationStore() = default;

void MdbxExactIndexGenerationStore::begin(const std::string& generation, const ExactIndexSpaceDescriptor& d,
                                         std::size_t expected_count) {
    if(generation.empty() || expected_count == 0 || expected_count > 1024)
        fail(Failure::InvalidInput, "invalid generation identity or cardinality");
    const auto encoded = encode_descriptor(d);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto existing = m_impl->table.find(m_impl->key(generation, 'd'), txn);
    if(existing) {
        if(*existing != encoded || m_impl->counts(generation, txn).first != expected_count)
            fail(Failure::Conflict, "generation identity already has another descriptor/count");
    } else {
        m_impl->table.insert_or_assign(m_impl->key(generation, 'd'), encoded, txn);
        std::string progress; number(progress, expected_count); number(progress, 0);
        m_impl->table.insert_or_assign(m_impl->key(generation, 'p'), progress, txn);
    }
    txn.commit();
}
void MdbxExactIndexGenerationStore::append(const std::string& generation,
                                          const std::vector<ExactIndexVectorInput>& inputs) {
    if(inputs.empty() || inputs.size() > 32) fail(Failure::InvalidInput, "append batch must contain 1..32 vectors");
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    if(m_impl->table.find(m_impl->key(generation, 'c'), txn)) fail(Failure::Conflict, "completed generation is immutable");
    const auto d = decode_descriptor(m_impl->required(m_impl->key(generation, 'd'), txn));
    auto count = m_impl->counts(generation, txn);
    if(inputs.size() > count.first - count.second) fail(Failure::Conflict, "append exceeds declared count");
    std::set<KnowledgeUnitId> seen;
    for(std::size_t i = 0; i < count.second; ++i)
        seen.insert(decode_row(m_impl->required(m_impl->key(generation, 'r', i), txn), d).input.unit_id);
    for(const auto& input : inputs) {
        validate_vector(input.values, d);
        if(!seen.insert(input.unit_id).second) fail(Failure::Conflict, "duplicate unit in generation");
        const auto evidence = m_impl->current(input.unit_id, input.unit_revision, d, txn);
        if(!evidence) fail(Failure::StaleBinding, "candidate is not canonically admitted");
        m_impl->table.insert_or_assign(m_impl->key(generation, 'r', count.second++),
                                      encode_row(Row{input, evidence->second}), txn);
    }
    std::string progress; number(progress, count.first); number(progress, count.second);
    m_impl->table.insert_or_assign(m_impl->key(generation, 'p'), progress, txn); txn.commit();
}
void MdbxExactIndexGenerationStore::complete(const std::string& generation) {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const bool exists = m_impl->table.find(m_impl->key(generation, 'c'), txn).has_value();
    const auto data = m_impl->load(generation, txn, true, exists);
    if(!exists) {
        Sha256Accumulator hash; hash.update(encode_descriptor(data->descriptor));
        for(const auto& row : data->rows) hash.update(encode_row(row));
        std::string completion; number(completion, 1); number(completion, data->rows.size());
        field(completion, bytes(hash.finish())); field(completion, data->frontier);
        m_impl->table.insert_or_assign(m_impl->key(generation, 'c'), completion, txn);
    }
    txn.commit();
}
void MdbxExactIndexGenerationStore::activate(const std::string& generation, const ExactIndexSpaceDescriptor& expected) {
    const auto encoded = encode_descriptor(expected);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto data = m_impl->load(generation, txn, true, true);
    if(encode_descriptor(data->descriptor) != encoded) fail(Failure::Incompatible, "activation descriptor mismatch");
    m_impl->table.insert_or_assign(m_impl->key("", 'a'), generation, txn);
    if(m_impl->before_commit) m_impl->before_commit();
    txn.commit();
}
ExactIndexGenerationSnapshot MdbxExactIndexGenerationStore::pin(const ExactIndexSpaceDescriptor& expected) const {
    const auto encoded = encode_descriptor(expected);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto selected = m_impl->table.find(m_impl->key("", 'a'), txn);
    if(!selected) fail(Failure::NotFound, "no active exact generation");
    const auto data = m_impl->load(*selected, txn, false, true);
    if(encode_descriptor(data->descriptor) != encoded) fail(Failure::Incompatible, "selected descriptor mismatch");
    txn.commit(); return ExactIndexGenerationSnapshot{data};
}
std::vector<ExactIndexHit> MdbxExactIndexGenerationStore::query(const ExactIndexGenerationSnapshot& snapshot,
    const ScopeId& scope, const std::vector<float>& vector, std::size_t limit,
    const std::function<bool(const KnowledgeUnitEnvelope&)>& authorize) const {
    if(!authorize) fail(Failure::InvalidInput, "host authorization/hard filter is required");
    const auto& data = *snapshot.m_data;
    const auto owner_connection = data.owner_connection.lock();
    if(!owner_connection || owner_connection.get() != m_impl->context->connection().get() ||
       data.owner_index != m_impl->index || data.owner_prefix != m_impl->prefix)
        fail(Failure::Incompatible, "snapshot belongs to another exact index or storage context");
    if(scope != data.descriptor.scope_id) fail(Failure::Incompatible, "query scope mismatch");
    validate_vector(vector, data.descriptor);
    std::vector<ExactIndexHit> result;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    for(const auto& row : data.rows) {
        auto current = m_impl->current(row.input.unit_id, row.input.unit_revision, data.descriptor, txn);
        if(!current || current->second != row.binding || !authorize(current->first)) continue;
        double score = 0;
        for(std::size_t i = 0; i < vector.size(); ++i) {
            const double a = vector[i], b = row.input.values[i];
            score += data.descriptor.metric == "dot" ? a * b : -(a - b) * (a - b);
        }
        result.push_back(ExactIndexHit{std::move(current->first), score});
    }
    txn.commit();
    std::sort(result.begin(), result.end(), [](const ExactIndexHit& a, const ExactIndexHit& b) {
        return a.score != b.score ? a.score > b.score : a.unit.id < b.unit.id;
    });
    if(result.size() > limit) result.resize(limit);
    return result;
}
void MdbxExactIndexGenerationStore::set_before_activation_commit_hook_for_testing(std::function<void()> hook) {
    m_impl->before_commit = std::move(hook);
}
} // namespace agent_memory
#endif
