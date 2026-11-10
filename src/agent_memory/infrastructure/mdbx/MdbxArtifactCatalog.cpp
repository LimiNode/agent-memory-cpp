#include "MdbxArtifactCatalog.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <limits>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>

#include <agent_memory/domain/Sha256.hpp>

namespace agent_memory {
namespace {

constexpr std::string_view kSchemaKey = "schema";
constexpr std::string_view kSchemaVersion = "agent_memory.artifact_catalog.v2";
constexpr std::string_view kArtifactVersion = "agent_memory.artifact.v1";
constexpr std::string_view kBindingVersion = "agent_memory.artifact_binding.v1";
constexpr std::string_view kIntentVersion = "agent_memory.artifact_intent.v1";
constexpr std::string_view kReceiptVersion = "agent_memory.artifact_receipt.v1";

class Writer final {
public:
    void string(std::string_view value) {
        u64(static_cast<std::uint64_t>(value.size()));
        m_value.append(value.data(), value.size());
    }
    void bytes(const std::uint8_t* data, std::size_t size) {
        m_value.append(reinterpret_cast<const char*>(data), size);
    }
    void u8(std::uint8_t value) { m_value.push_back(static_cast<char>(value)); }
    void u64(std::uint64_t value) {
        for(unsigned shift = 0; shift < 64U; shift += 8U)
            m_value.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
    [[nodiscard]] std::string finish() && { return std::move(m_value); }
private:
    std::string m_value;
};

class Reader final {
public:
    explicit Reader(const std::string& value) : m_value(value) {}
    std::string string() {
        const auto size = u64();
        if(size > static_cast<std::uint64_t>(m_value.size() - m_offset) ||
           size > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
            throw std::runtime_error("artifact catalog payload string is truncated");
        std::string result(m_value.data() + m_offset, static_cast<std::size_t>(size));
        m_offset += static_cast<std::size_t>(size);
        return result;
    }
    std::array<std::uint8_t, 32> digest() {
        if(remaining() < 32U) throw std::runtime_error("artifact digest is truncated");
        std::array<std::uint8_t, 32> result{};
        for(auto& byte : result) byte = static_cast<std::uint8_t>(m_value[m_offset++]);
        return result;
    }
    std::uint8_t u8() {
        if(remaining() < 1U) throw std::runtime_error("artifact payload is truncated");
        return static_cast<std::uint8_t>(m_value[m_offset++]);
    }
    std::uint64_t u64() {
        if(remaining() < 8U) throw std::runtime_error("artifact payload integer is truncated");
        std::uint64_t result = 0;
        for(unsigned shift = 0; shift < 64U; shift += 8U)
            result |= static_cast<std::uint64_t>(
                static_cast<unsigned char>(m_value[m_offset++])) << shift;
        return result;
    }
    void end() const {
        if(m_offset != m_value.size()) throw std::runtime_error("artifact payload has trailing bytes");
    }
private:
    [[nodiscard]] std::size_t remaining() const noexcept { return m_value.size() - m_offset; }
    const std::string& m_value;
    std::size_t m_offset = 0;
};

void version(Writer& writer, std::string_view value) { writer.string(value); }
void expect_version(Reader& reader, std::string_view expected) {
    if(reader.string() != expected) throw std::runtime_error("unsupported artifact catalog payload version");
}

std::string safe_part(std::string value) {
    if(value.empty()) value = "agent_memory";
    for(char& character : value) {
        if(!(std::isalnum(static_cast<unsigned char>(character)) || character == '_'))
            character = '_';
    }
    return value;
}

std::string table_name(const std::string& prefix, std::string_view suffix) {
    return prefix + "_artifact_" + std::string{suffix};
}

std::string revision_prefix(const SourceRevisionId& source_revision_id) {
    Writer writer;
    writer.u64(static_cast<std::uint64_t>(source_revision_id.value().size()));
    writer.bytes(reinterpret_cast<const std::uint8_t*>(source_revision_id.value().data()),
                 source_revision_id.value().size());
    return std::move(writer).finish();
}

std::string revision_key(
    const SourceRevisionId& source_revision_id,
    const ArtifactBindingId& binding_id) {
    auto key = revision_prefix(source_revision_id);
    Writer writer;
    writer.u64(static_cast<std::uint64_t>(binding_id.value().size()));
    writer.bytes(reinterpret_cast<const std::uint8_t*>(binding_id.value().data()),
                 binding_id.value().size());
    key += std::move(writer).finish();
    return key;
}

bool known_retention(ArtifactRetentionClass value) noexcept {
    return value == ArtifactRetentionClass::ReferenceOnly ||
           value == ArtifactRetentionClass::SourceOriginal ||
           value == ArtifactRetentionClass::DerivedDurable ||
           value == ArtifactRetentionClass::RebuildableCache;
}

ArtifactCatalogResult failure(ArtifactCatalogStatus status, std::string diagnostic) {
    ArtifactCatalogResult result;
    result.status = status;
    result.diagnostic = std::move(diagnostic);
    return result;
}

ArtifactCatalogReadResult read_failure(ArtifactCatalogStatus status, std::string diagnostic) {
    ArtifactCatalogReadResult result;
    result.status = status;
    result.diagnostic = std::move(diagnostic);
    return result;
}

ArtifactCatalogStatus map_raw_status(RawBlobStatus status) noexcept {
    switch(status) {
    case RawBlobStatus::NotFound:
        return ArtifactCatalogStatus::NotFound;
    case RawBlobStatus::Corrupt:
        return ArtifactCatalogStatus::Corrupt;
    case RawBlobStatus::InvalidArgument:
        return ArtifactCatalogStatus::InvalidArgument;
    case RawBlobStatus::VerificationBudgetExceeded:
        return ArtifactCatalogStatus::VerificationBudgetExceeded;
    case RawBlobStatus::IoError:
        return ArtifactCatalogStatus::IoError;
    case RawBlobStatus::Ok:
        return ArtifactCatalogStatus::Ok;
    }
    return ArtifactCatalogStatus::IoError;
}

ArtifactCatalogStatus probe_status(
    RawBlobStatus status,
    const std::optional<RawBlobMetadata>& metadata,
    std::uint64_t expected_size) noexcept {
    if(status != RawBlobStatus::Ok) return map_raw_status(status);
    if(!metadata || metadata->size_bytes != expected_size)
        return ArtifactCatalogStatus::Corrupt;
    return ArtifactCatalogStatus::Ok;
}

void encode_digest(Writer& writer, const BlobDigest& digest) {
    writer.u8(static_cast<std::uint8_t>(digest.algorithm));
    writer.bytes(digest.value.data(), digest.value.size());
}

BlobDigest decode_digest(Reader& reader) {
    BlobDigest digest;
    digest.algorithm = static_cast<BlobDigestAlgorithm>(reader.u8());
    digest.value = reader.digest();
    return digest;
}

std::string encode_artifact(const Artifact& artifact) {
    Writer writer;
    version(writer, kArtifactVersion);
    writer.string(artifact.id.value());
    encode_digest(writer, artifact.digest);
    writer.u64(artifact.size_bytes);
    return std::move(writer).finish();
}

Artifact decode_artifact(const std::string& payload) {
    Reader reader(payload);
    expect_version(reader, kArtifactVersion);
    Artifact artifact{ArtifactId(reader.string()), decode_digest(reader), reader.u64()};
    reader.end();
    if(!is_valid_artifact(artifact)) throw std::runtime_error("invalid artifact catalog identity");
    return artifact;
}

std::string encode_binding(const ArtifactBinding& binding) {
    Writer writer;
    version(writer, kBindingVersion);
    writer.string(binding.id.value());
    writer.string(binding.source_revision_id.value());
    writer.string(binding.artifact_id.value());
    writer.string(binding.role);
    writer.u8(static_cast<std::uint8_t>(binding.retention));
    writer.u8(binding.media_type ? 1U : 0U);
    if(binding.media_type) writer.string(*binding.media_type);
    writer.u8(binding.original_name ? 1U : 0U);
    if(binding.original_name) writer.string(*binding.original_name);
    writer.u64(binding.bound_at_ms);
    return std::move(writer).finish();
}

ArtifactBinding decode_binding(const std::string& payload) {
    Reader reader(payload);
    expect_version(reader, kBindingVersion);
    ArtifactBinding binding;
    binding.id = ArtifactBindingId(reader.string());
    binding.source_revision_id = SourceRevisionId(reader.string());
    binding.artifact_id = ArtifactId(reader.string());
    binding.role = reader.string();
    binding.retention = static_cast<ArtifactRetentionClass>(reader.u8());
    if(reader.u8() != 0U) binding.media_type = reader.string();
    if(reader.u8() != 0U) binding.original_name = reader.string();
    binding.bound_at_ms = reader.u64();
    reader.end();
    if(!is_valid_artifact_binding(binding) || !known_retention(binding.retention))
        throw std::runtime_error("invalid artifact binding identity");
    return binding;
}

struct Intent final {
    ArtifactCatalogIngestRequest request;
    Artifact artifact;
    std::uint8_t phase = 0;
};

std::string encode_intent(const Intent& intent) {
    Writer writer;
    version(writer, kIntentVersion);
    writer.string(intent.request.source_revision_id.value());
    writer.string(intent.request.binding_id.value());
    writer.string(intent.request.role);
    writer.u8(static_cast<std::uint8_t>(intent.request.retention));
    writer.u8(intent.request.media_type ? 1U : 0U);
    if(intent.request.media_type) writer.string(*intent.request.media_type);
    writer.u8(intent.request.original_name ? 1U : 0U);
    if(intent.request.original_name) writer.string(*intent.request.original_name);
    writer.u64(intent.request.bound_at_ms);
    writer.string(intent.artifact.id.value());
    encode_digest(writer, intent.artifact.digest);
    writer.u64(intent.artifact.size_bytes);
    writer.u8(intent.phase);
    return std::move(writer).finish();
}

Intent decode_intent(const std::string& payload) {
    Reader reader(payload);
    expect_version(reader, kIntentVersion);
    Intent intent;
    intent.request.source_revision_id = SourceRevisionId(reader.string());
    intent.request.binding_id = ArtifactBindingId(reader.string());
    intent.request.role = reader.string();
    intent.request.retention = static_cast<ArtifactRetentionClass>(reader.u8());
    if(reader.u8() != 0U) intent.request.media_type = reader.string();
    if(reader.u8() != 0U) intent.request.original_name = reader.string();
    intent.request.bound_at_ms = reader.u64();
    intent.artifact.id = ArtifactId(reader.string());
    intent.artifact.digest = decode_digest(reader);
    intent.artifact.size_bytes = reader.u64();
    intent.phase = reader.u8();
    reader.end();
    if(!is_valid_artifact(intent.artifact) ||
       intent.request.source_revision_id.empty() ||
       intent.request.binding_id.empty() || intent.request.role.empty() ||
       !known_retention(intent.request.retention))
        throw std::runtime_error("invalid artifact ingest intent");
    return intent;
}

std::string encode_receipt(const ArtifactCatalogRecord& record) {
    Writer writer;
    version(writer, kReceiptVersion);
    writer.string(record.binding.id.value());
    writer.string(record.artifact.id.value());
    writer.u8(static_cast<std::uint8_t>(record.body_state));
    writer.u64(record.binding.bound_at_ms);
    return std::move(writer).finish();
}

void validate_record(const Artifact& artifact, const ArtifactBinding& binding) {
    if(!is_valid_artifact(artifact) || !is_valid_artifact_binding(binding) ||
       binding.artifact_id != artifact.id)
        throw std::runtime_error("artifact catalog record identity mismatch");
}

} // namespace

class MdbxArtifactCatalog::Impl final {
public:
    explicit Impl(MdbxArtifactCatalogOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(std::move(options.path),
                                                             options.relative_to_exe,
                                                             options.max_dbs)),
          prefix(safe_part(std::move(options.table_prefix))),
          marker(context->connection(), table_name(prefix, "schema")),
          artifacts(context->connection(), table_name(prefix, "artifacts")),
          bindings(context->connection(), table_name(prefix, "bindings")),
          revisions(context->connection(), table_name(prefix, "revisions")),
          intents(context->connection(), table_name(prefix, "intents")),
          receipts(context->connection(), table_name(prefix, "receipts")),
          cas(std::move(options.raw_cas_root), options.raw_cas_options),
          max_full_body_bytes(options.max_full_body_bytes),
          fault_injector(std::move(options.fault_injector)) {
        if(cas.root().empty()) throw std::invalid_argument("artifact catalog requires a Raw CAS root");
        initialize_schema();
        recover();
    }

    void inject(ArtifactCatalogFaultPoint point) const {
        if(fault_injector) fault_injector(point);
    }

    static bool same_intent(
        const Intent& stored,
        const ArtifactCatalogIngestRequest& request,
        const Artifact& artifact) noexcept {
        return stored.artifact.id == artifact.id &&
            stored.artifact.digest.value == artifact.digest.value &&
            stored.artifact.size_bytes == artifact.size_bytes &&
            stored.request.source_revision_id == request.source_revision_id &&
            stored.request.binding_id == request.binding_id &&
            stored.request.role == request.role &&
            stored.request.retention == request.retention &&
            stored.request.media_type == request.media_type &&
            stored.request.original_name == request.original_name &&
            stored.request.bound_at_ms == request.bound_at_ms;
    }

    static ArtifactCatalogRecord record_from(const Artifact& artifact,
                                              const ArtifactBinding& binding,
                                              bool receipt) {
        validate_record(artifact, binding);
        ArtifactCatalogRecord result;
        result.artifact = artifact;
        result.binding = binding;
        result.body_state = BindingBodyState::Materialized;
        result.receipt_finalized = receipt;
        return result;
    }

    std::optional<ArtifactCatalogRecord> load_binding(
        const ArtifactBindingId& id,
        const mdbxc::Transaction& txn) const {
        const auto binding_payload = bindings.find(id.value(), txn);
        if(!binding_payload) return std::nullopt;
        const auto binding = decode_binding(*binding_payload);
        const auto artifact_payload = artifacts.find(binding.artifact_id.value(), txn);
        if(!artifact_payload) throw std::runtime_error("artifact binding references missing artifact");
        const auto artifact = decode_artifact(*artifact_payload);
        const auto revision_binding = revisions.find(
            revision_key(binding.source_revision_id, binding.id), txn);
        if(!revision_binding || *revision_binding != binding.id.value())
            throw std::runtime_error("artifact source revision index mismatch");
        const auto receipt = receipts.find(binding.id.value(), txn);
        if(receipt) {
            Reader reader(*receipt);
            expect_version(reader, kReceiptVersion);
            if(reader.string() != binding.id.value() || reader.string() != artifact.id.value())
                throw std::runtime_error("artifact receipt identity mismatch");
            const auto state = static_cast<BindingBodyState>(reader.u8());
            static_cast<void>(reader.u64());
            reader.end();
            if(state != BindingBodyState::Materialized)
                throw std::runtime_error("artifact receipt body state mismatch");
        }
        return record_from(artifact, binding, receipt.has_value());
    }

    void publish_catalog(const Intent& intent, const mdbxc::Transaction& txn) {
        const auto existing = load_binding(intent.request.binding_id, txn);
        if(existing) {
            if(existing->artifact.id != intent.artifact.id ||
               existing->binding.source_revision_id != intent.request.source_revision_id ||
               existing->binding.role != intent.request.role ||
               existing->binding.retention != intent.request.retention ||
               existing->binding.media_type != intent.request.media_type ||
               existing->binding.original_name != intent.request.original_name ||
               existing->binding.bound_at_ms != intent.request.bound_at_ms)
                throw std::runtime_error("artifact binding identity conflict");
            intents.insert_or_assign(intent.request.binding_id.value(),
                                     encode_intent(Intent{intent.request, intent.artifact, 1U}), txn);
            return;
        }
        const auto existing_artifact = artifacts.find(intent.artifact.id.value(), txn);
        if(existing_artifact) {
            const auto stored = decode_artifact(*existing_artifact);
            if(stored.digest.value != intent.artifact.digest.value ||
               stored.size_bytes != intent.artifact.size_bytes)
                throw std::runtime_error("artifact digest identity collision");
        } else {
            artifacts.insert_or_assign(intent.artifact.id.value(), encode_artifact(intent.artifact), txn);
        }
        ArtifactBinding binding{
            intent.request.binding_id,
            intent.request.source_revision_id,
            intent.artifact.id,
            intent.request.role,
            intent.request.retention,
            intent.request.media_type,
            intent.request.original_name,
            intent.request.bound_at_ms};
        validate_record(intent.artifact, binding);
        bindings.insert_or_assign(binding.id.value(), encode_binding(binding), txn);
        revisions.insert_or_assign(
            revision_key(binding.source_revision_id, binding.id),
            binding.id.value(),
            txn);
        intents.insert_or_assign(binding.id.value(),
                                 encode_intent(Intent{intent.request, intent.artifact, 1U}), txn);
    }

    void finalize(const Intent& intent) {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto record = load_binding(intent.request.binding_id, txn);
        if(!record) throw std::runtime_error("cannot finalize missing artifact binding");
        receipts.insert_or_assign(intent.request.binding_id.value(), encode_receipt(*record), txn);
        intents.erase(intent.request.binding_id.value(), txn);
        txn.commit();
    }

    void initialize_schema() {
        auto read = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto stored = marker.find(std::string{kSchemaKey}, read);
        read.rollback();
        if(stored) {
            if(*stored != kSchemaVersion) throw std::runtime_error("unsupported artifact catalog schema");
            return;
        }
        auto write = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto again = marker.find(std::string{kSchemaKey}, write);
        if(again) {
            if(*again != kSchemaVersion) throw std::runtime_error("unsupported artifact catalog schema");
            write.commit();
            return;
        }
        marker.insert_or_assign(std::string{kSchemaKey}, std::string{kSchemaVersion}, write);
        write.commit();
    }

    void recover() {
        std::vector<std::pair<std::string, std::string>> pending;
        {
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            intents.load(pending, txn);
            txn.commit();
        }
        for(const auto& row : pending) {
            const auto intent = decode_intent(row.second);
            if(row.first != intent.request.binding_id.value())
                throw std::runtime_error("artifact intent key mismatch");
            auto read = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            const auto existing = load_binding(intent.request.binding_id, read);
            read.commit();
            const auto probe = cas.probe(intent.artifact.id);
            if(probe.status == RawBlobStatus::NotFound) {
                if(existing) {
                    throw std::runtime_error("published artifact binding lost its Raw CAS body");
                }
                continue;
            }
            if(probe.status != RawBlobStatus::Ok || !probe.metadata ||
               probe.metadata->size_bytes != intent.artifact.size_bytes) {
                throw std::runtime_error("artifact intent CAS body failed recovery validation");
            }
            if(!existing) {
                auto write = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
                publish_catalog(intent, write);
                write.commit();
            }
            finalize(intent);
        }
    }

    std::shared_ptr<MdbxStorageContext> context;
    std::string prefix;
    mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::string, std::string> marker;
    mdbxc::KeyValueTable<std::string, std::string> artifacts;
    mdbxc::KeyValueTable<std::string, std::string> bindings;
    mdbxc::KeyValueTable<std::string, std::string> revisions;
    mdbxc::KeyValueTable<std::string, std::string> intents;
    mdbxc::KeyValueTable<std::string, std::string> receipts;
    FileRawBlobStore cas;
    std::uint64_t max_full_body_bytes;
    ArtifactCatalogFaultInjector fault_injector;
};

MdbxArtifactCatalog::MdbxArtifactCatalog(MdbxArtifactCatalogOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxArtifactCatalog::~MdbxArtifactCatalog() = default;

ArtifactCatalogResult MdbxArtifactCatalog::ingest(
    const ArtifactCatalogIngestRequest& request) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    if(request.source_revision_id.empty() || request.binding_id.empty() || request.role.empty() ||
       !known_retention(request.retention))
        return failure(ArtifactCatalogStatus::InvalidArgument, "artifact ingest intent is incomplete");
    if(request.retention == ArtifactRetentionClass::ReferenceOnly)
        return failure(ArtifactCatalogStatus::InvalidArgument,
                       "Raw ingest requires a materialized retention class");

    BlobDigest digest;
    digest.value = sha256(request.body);
    const Artifact artifact{artifact_id_for_digest(digest), digest,
                            static_cast<std::uint64_t>(request.body.size())};
    Intent intent{request, artifact, 0U};
    std::optional<Intent> pending_intent;

    {
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto existing = m_impl->load_binding(request.binding_id, txn);
        if(const auto pending = m_impl->intents.find(request.binding_id.value(), txn))
            pending_intent = decode_intent(*pending);
        txn.commit();
        if(existing) {
            if(existing->artifact.id != artifact.id ||
               existing->binding.source_revision_id != request.source_revision_id ||
               existing->binding.role != request.role ||
               existing->binding.retention != request.retention ||
               existing->binding.media_type != request.media_type ||
               existing->binding.original_name != request.original_name ||
               existing->binding.bound_at_ms != request.bound_at_ms)
                return failure(ArtifactCatalogStatus::Conflict, "artifact binding already points elsewhere");
            const auto probe = m_impl->cas.probe(existing->artifact.id);
            const auto probe_result = probe_status(
                probe.status, probe.metadata, existing->artifact.size_bytes);
            if(probe_result != ArtifactCatalogStatus::Ok)
                return failure(probe_result,
                               "existing artifact binding has no verified Raw CAS body");
            auto result = *existing;
            if(!result.receipt_finalized) {
                if(!pending_intent || !Impl::same_intent(*pending_intent, request, artifact))
                    return failure(ArtifactCatalogStatus::Corrupt,
                                   "catalog binding has an unfinalized receipt without its intent");
                m_impl->finalize(*pending_intent);
                result.receipt_finalized = true;
            }
            return ArtifactCatalogResult{ArtifactCatalogStatus::Ok, std::move(result), {}};
        }
    }

    if(pending_intent) {
        if(!Impl::same_intent(*pending_intent, request, artifact))
            return failure(ArtifactCatalogStatus::Conflict,
                           "binding id already has a different pending ingest intent");
        intent = *pending_intent;
    }

    const bool created_intent = !pending_intent.has_value();
    if(created_intent) {
        m_impl->inject(ArtifactCatalogFaultPoint::BeforeIntent);
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        m_impl->intents.insert_or_assign(request.binding_id.value(), encode_intent(intent), txn);
        txn.commit();
        m_impl->inject(ArtifactCatalogFaultPoint::AfterIntent);
    }
    m_impl->inject(ArtifactCatalogFaultPoint::BeforeCasPublication);
    const auto stored = m_impl->cas.put(request.body);
    if(stored.status != RawBlobStatus::Ok || !stored.metadata)
        return failure(ArtifactCatalogStatus::IoError,
                       "Raw CAS publication failed: " + stored.diagnostic);
    if(stored.metadata->artifact_id != artifact.id || stored.metadata->size_bytes != artifact.size_bytes)
        return failure(ArtifactCatalogStatus::Corrupt, "Raw CAS publication identity mismatch");
    m_impl->inject(ArtifactCatalogFaultPoint::AfterCasPublication);
    m_impl->inject(ArtifactCatalogFaultPoint::BeforeCatalogCommit);
    {
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        m_impl->publish_catalog(intent, txn);
        txn.commit();
    }
    m_impl->inject(ArtifactCatalogFaultPoint::AfterCatalogCommit);
    m_impl->inject(ArtifactCatalogFaultPoint::BeforeReceiptFinalization);
    m_impl->finalize(intent);
    return ArtifactCatalogResult{ArtifactCatalogStatus::Ok,
                                 Impl::record_from(artifact,
                                                   ArtifactBinding{request.binding_id,
                                                                   request.source_revision_id,
                                                                   artifact.id,
                                                                   request.role,
                                                                   request.retention,
                                                                   request.media_type,
                                                                   request.original_name,
                                                                   request.bound_at_ms},
                                                   true), {}};
}

ArtifactCatalogResult MdbxArtifactCatalog::read_binding(
    const ArtifactBindingId& binding_id) const {
    if(binding_id.empty()) return failure(ArtifactCatalogStatus::InvalidArgument, "empty artifact binding id");
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    ArtifactCatalogRecord record;
    {
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        try {
            const auto loaded = m_impl->load_binding(binding_id, txn);
            txn.commit();
            if(!loaded) return failure(ArtifactCatalogStatus::NotFound, "artifact binding was not found");
            record = *loaded;
        } catch(...) {
            txn.rollback();
            throw;
        }
    }
    const auto probe = m_impl->cas.probe(record.artifact.id);
    const auto probe_result = probe_status(
        probe.status, probe.metadata, record.artifact.size_bytes);
    if(probe_result != ArtifactCatalogStatus::Ok)
        return failure(probe_result,
                       "catalog binding has no verified Raw CAS body");
    return ArtifactCatalogResult{ArtifactCatalogStatus::Ok, std::move(record), {}};
}

ArtifactCatalogResult MdbxArtifactCatalog::read_binding_metadata(
    const ArtifactBindingId& binding_id) const {
    if(binding_id.empty())
        return failure(ArtifactCatalogStatus::InvalidArgument,
                       "empty artifact binding id");
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(
        mdbxc::TransactionMode::READ_ONLY);
    try {
        const auto loaded = m_impl->load_binding(binding_id, txn);
        txn.commit();
        if(!loaded)
            return failure(ArtifactCatalogStatus::NotFound,
                           "artifact binding was not found");
        return ArtifactCatalogResult{
            ArtifactCatalogStatus::Ok, *loaded, {}};
    } catch(...) {
        txn.rollback();
        throw;
    }
}

ArtifactCatalogResult MdbxArtifactCatalog::read_binding_in_transaction(
    const ArtifactBindingId& binding_id,
    const mdbxc::Transaction& transaction
) const {
    if(binding_id.empty())
        return failure(ArtifactCatalogStatus::InvalidArgument,
                       "empty artifact binding id");
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    try {
        const auto record = m_impl->load_binding(binding_id, transaction);
        if(!record)
            return failure(ArtifactCatalogStatus::NotFound,
                           "artifact binding was not found");
        return ArtifactCatalogResult{ArtifactCatalogStatus::Ok, *record, {}};
    } catch(const std::exception& error) {
        return failure(ArtifactCatalogStatus::Corrupt, error.what());
    }
}

ArtifactCatalogCollectionResult MdbxArtifactCatalog::read_source_revision_bindings(
    const SourceRevisionId& source_revision_id) const {
    if(source_revision_id.empty())
        return ArtifactCatalogCollectionResult{
            ArtifactCatalogStatus::InvalidArgument, {}, "empty source revision id"};
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    ArtifactCatalogCollectionResult result;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto prefix = revision_prefix(source_revision_id);
    auto upper = prefix;
    upper.push_back(static_cast<char>(0xff));
    m_impl->revisions.for_each_range(
        prefix,
        upper,
        [&](const std::string& key, const std::string& binding_id) {
            if(key.size() <= prefix.size() || key.compare(0, prefix.size(), prefix) != 0)
                throw std::runtime_error("artifact revision index key is outside its prefix");
            const ArtifactBindingId id(binding_id);
            const auto record = m_impl->load_binding(id, txn);
            if(!record || record->binding.source_revision_id != source_revision_id)
                throw std::runtime_error("artifact revision index binding mismatch");
            result.records.push_back(*record);
            return true;
        },
        txn);
    txn.commit();
    for(const auto& record : result.records) {
        const auto probe = m_impl->cas.probe(record.artifact.id);
        const auto status = probe_status(
            probe.status, probe.metadata, record.artifact.size_bytes);
        if(status != ArtifactCatalogStatus::Ok) {
            result.records.clear();
            result.status = status;
            result.diagnostic = "source revision binding has no verified Raw CAS body";
            return result;
        }
    }
    result.status = result.records.empty() ? ArtifactCatalogStatus::NotFound
                                           : ArtifactCatalogStatus::Ok;
    if(result.records.empty()) result.diagnostic = "source revision was not found";
    return result;
}

ArtifactCatalogResult MdbxArtifactCatalog::read_source_revision(
    const SourceRevisionId& source_revision_id) const {
    const auto collection = read_source_revision_bindings(source_revision_id);
    if(collection.status != ArtifactCatalogStatus::Ok || collection.records.empty())
        return failure(collection.status, collection.diagnostic);
    if(collection.records.size() != 1U)
        return failure(ArtifactCatalogStatus::Conflict,
                       "source revision has multiple bindings; enumerate them explicitly");
    return ArtifactCatalogResult{
        ArtifactCatalogStatus::Ok, collection.records.front(), {}};
}

ArtifactCatalogReadResult MdbxArtifactCatalog::read_body(
    const ArtifactBindingId& binding_id) const {
    const auto record = read_binding(binding_id);
    if(record.status != ArtifactCatalogStatus::Ok || !record.record)
        return read_failure(record.status, record.diagnostic);
    const auto cas_limit = m_impl->cas.max_read_range_bytes();
    if(record.record->artifact.size_bytes > m_impl->max_full_body_bytes ||
       record.record->artifact.size_bytes > cas_limit)
        return read_failure(ArtifactCatalogStatus::InvalidArgument,
                            "full-body materialization exceeds the catalog or CAS range budget");
    if(record.record->artifact.size_bytes >
       static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
        return read_failure(ArtifactCatalogStatus::InvalidArgument,
                            "full-body materialization exceeds size_t");
    std::string bytes;
    bytes.reserve(static_cast<std::size_t>(record.record->artifact.size_bytes));
    const auto part = m_impl->cas.read_range(
        record.record->artifact.id, 0, record.record->artifact.size_bytes);
    if(part.status != RawBlobStatus::Ok)
        return read_failure(map_raw_status(part.status), part.diagnostic);
    bytes = part.bytes;
    return ArtifactCatalogReadResult{ArtifactCatalogStatus::Ok, std::move(bytes), {}};
}

ArtifactCatalogReadResult MdbxArtifactCatalog::read_body_range(
    const ArtifactBindingId& binding_id,
    std::uint64_t offset,
    std::uint64_t length) const {
    return read_body_range(
        binding_id, offset, length,
        (std::numeric_limits<std::uint64_t>::max)());
}

ArtifactCatalogReadResult MdbxArtifactCatalog::read_body_range(
    const ArtifactBindingId& binding_id,
    std::uint64_t offset,
    std::uint64_t length,
    std::uint64_t verification_budget_bytes) const {
    const auto record = read_binding_metadata(binding_id);
    if(record.status != ArtifactCatalogStatus::Ok || !record.record)
        return read_failure(record.status, record.diagnostic);
    if(offset > record.record->artifact.size_bytes ||
       length > record.record->artifact.size_bytes - offset)
        return read_failure(ArtifactCatalogStatus::InvalidArgument,
                            "artifact body range is outside the catalog body");
    if(length > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
        return read_failure(ArtifactCatalogStatus::InvalidArgument,
                            "artifact body range exceeds size_t");
    if(length > m_impl->cas.max_read_range_bytes())
        return read_failure(ArtifactCatalogStatus::InvalidArgument,
                            "artifact body range exceeds the configured CAS range budget");
    const auto part = m_impl->cas.read_range_bounded(
        record.record->artifact.id, offset, length, verification_budget_bytes);
    if(part.status == RawBlobStatus::InvalidArgument) {
        return read_failure(ArtifactCatalogStatus::Corrupt,
                            "Raw CAS body is shorter than its catalog metadata");
    }
    if(part.status != RawBlobStatus::Ok)
        return read_failure(map_raw_status(part.status), part.diagnostic);
    if(part.bytes.size() != static_cast<std::size_t>(length))
        return read_failure(ArtifactCatalogStatus::Corrupt,
                            "Raw CAS range returned an unexpected size");
    return ArtifactCatalogReadResult{ArtifactCatalogStatus::Ok, part.bytes, {}};
}

void MdbxArtifactCatalog::recover() {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    m_impl->recover();
}

std::vector<ArtifactId> MdbxArtifactCatalog::gc_candidates() const {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    std::vector<std::pair<std::string, std::string>> artifact_rows;
    std::vector<std::pair<std::string, std::string>> binding_rows;
    std::set<std::string> referenced;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    m_impl->artifacts.load(artifact_rows, txn);
    m_impl->bindings.load(binding_rows, txn);
    for(const auto& row : binding_rows) referenced.insert(decode_binding(row.second).artifact_id.value());
    txn.commit();
    std::vector<ArtifactId> result;
    for(const auto& row : artifact_rows) {
        const auto artifact = decode_artifact(row.second);
        if(referenced.find(artifact.id.value()) == referenced.end()) result.push_back(artifact.id);
    }
    return result;
}

const FileRawBlobStore& MdbxArtifactCatalog::raw_cas() const noexcept { return m_impl->cas; }

} // namespace agent_memory

#endif
