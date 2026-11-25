#include "MdbxConversationDurableInternal.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <agent_memory/ingestion/ConversationSourceImporterInternal.hpp>
#include <mdbx_containers/common.hpp>

#include <algorithm>
#include <cstring>
#include <limits>
#include <random>
#include <type_traits>
#include <utility>

namespace agent_memory::detail::conversation_durable {
namespace {

constexpr std::uint64_t kMiB = 1024U * 1024U;
constexpr auto kMax = std::numeric_limits<std::uint64_t>::max();
constexpr std::size_t kConversationValueOverheadBytes = 4 + 8 + 8 + kValueOverheadBytes;
constexpr std::size_t kRevisionValueOverheadBytes = 8 + 1 + 8 + 4 + 128 + 4 + kValueOverheadBytes;
ByteView view(const ByteBuffer& value) { return {value.data(), value.size()}; }
ByteView view(const MDBX_val& value) { return {static_cast<const std::uint8_t*>(value.iov_base), value.iov_len}; }
MDBX_val val(ByteView value) { return {const_cast<std::uint8_t*>(value.data), value.size}; }
void checked(int code) {
    if(code != MDBX_SUCCESS) throw mdbxc::MdbxException(mdbx_strerror(code), code);
}
[[noreturn]] void corrupt() { throw CodecError(CodecCode::Corruption); }
[[noreturn]] void unsupported() { throw CodecError(CodecCode::UnsupportedSchema); }
[[noreturn]] void resource_limit() { throw CodecError(CodecCode::ResourceLimit); }
template<typename Function>
auto stored(Function function) {
    try { return function(); }
    catch(const CodecError& error) {
        if(error.code() == CodecCode::ResourceLimit || error.code() == CodecCode::InvalidValue) corrupt();
        if(error.code() == CodecCode::InvalidLimits) unsupported();
        throw;
    }
}
std::uint64_t sum(std::uint64_t a, std::uint64_t b) {
    if(b > kMax - a) resource_limit();
    return a + b;
}
bool fits(std::uint64_t current, std::uint64_t amount, std::uint64_t quota) {
    return current <= quota && amount <= quota - current;
}
bool same_quotas(const Quotas& a, const Quotas& b) {
    return a.conversations == b.conversations && a.entries == b.entries &&
        a.revisions_per_entry == b.revisions_per_entry && a.canonical_bytes == b.canonical_bytes &&
        a.mapping_rows == b.mapping_rows && a.mapping_bytes == b.mapping_bytes;
}
bool valid_access(const ConversationStoreAccess& access) {
    return !access.scope.empty() && is_valid_conversation_namespace(access.policy_binding);
}
bool valid_limits(const ConversationReadLimits& limits) {
    return limits.max_items <= 65536 && limits.max_output_bytes <= 256U * kMiB &&
        limits.max_visited_records <= 262144 && limits.max_decoded_bytes <= 256U * kMiB;
}
ConversationWriteResult refused(ConversationWriteStatus status) { return {status, std::nullopt, 0}; }
ConversationReadResult refused(ConversationReadStatus status) {
    ConversationReadResult result;
    result.status = status;
    return result;
}

// Counts physical rows/bytes before any copies. Persistent preparation charges
// are conservative; bounded bucket scratch is reused one row at a time.
struct Work final {
    const DurableOptions& options;
    std::uint64_t rows = 0, bytes = 0, live = 0;
    explicit Work(const DurableOptions& input) : options(input) {
        // Profile, fixed keys, row-vector storage and small result identities.
        prepare(16384);
    }
    void visit(ByteView key, ByteView value, std::uint64_t max_value_bytes) {
        // An already-fetched impossible stored length is corruption, regardless
        // of a stricter local budget. No decode or copy is needed to establish it.
        if(value.size > max_value_bytes) corrupt();
        const auto size = sum(key.size, value.size);
        if(rows >= options.max_read_rows || !fits(bytes, size, options.max_read_raw_bytes)) resource_limit();
        ++rows;
        bytes += size;
    }
    void prepare(std::uint64_t amount) {
        if(!fits(live, amount, options.max_live_bytes)) resource_limit();
        live += amount;
    }
    void scratch(std::uint64_t amount) const {
        if(!fits(live, amount, options.max_live_bytes)) resource_limit();
    }
};

struct WorkPhase final {
    Work& work;
    std::uint64_t retained;
    explicit WorkPhase(Work& input) : work(input), retained(input.live) {}
    ~WorkPhase() { work.live = retained; }
};

std::uint64_t object_reserve(const ConversationCodecLimits& limits) {
    const auto unit = std::max({sizeof(ConversationLink), sizeof(ArtifactBindingId),
        sizeof(ConversationMetadataExtension), sizeof(ExternalIdentityComponent)});
    // Each nested AMXR frame has an independent bounded tuple allocation.
    return sum(limits.max_objects * unit,
        8U * kMaxExternalIdentityComponents * sizeof(ExternalIdentityComponent));
}

std::optional<ByteView> get(MDBX_txn* txn, MDBX_dbi dbi, const ByteBuffer& key, Work& work,
    std::uint64_t max_value_bytes) {
    auto k = val(view(key));
    MDBX_val value{};
    const auto code = mdbx_get(txn, dbi, &k, &value);
    if(code == MDBX_NOTFOUND) return std::nullopt;
    checked(code);
    work.visit(view(key), view(value), max_value_bytes);
    return view(value);
}
void put(MDBX_txn* txn, MDBX_dbi dbi, const ByteBuffer& key, const ByteBuffer& value, bool immutable) {
    auto k = val(view(key)), v = val(view(value));
    const auto code = mdbx_put(txn, dbi, &k, &v, immutable ? MDBX_NOOVERWRITE : MDBX_UPSERT);
    if(immutable && code == MDBX_KEYEXIST) corrupt();
    checked(code);
}
class Cursor final {
public:
    Cursor(MDBX_txn* txn, MDBX_dbi dbi) { checked(mdbx_cursor_open(txn, dbi, &m_cursor)); }
    ~Cursor() { mdbx_cursor_close(m_cursor); }
    Cursor(const Cursor&) = delete;
    Cursor& operator=(const Cursor&) = delete;
    MDBX_cursor* handle() const { return m_cursor; }
private:
    MDBX_cursor* m_cursor = nullptr;
};

void validate_environment(const DurableOptions& options, MDBX_txn* txn, bool writing) {
    auto* env = options.context->connection()->env_handle();
    if(!env || !txn || mdbx_txn_env(txn) != env) unsupported();
    unsigned flags = 0;
    checked(mdbx_env_get_flags(env, &flags));
    if(flags & (MDBX_NOMETASYNC | MDBX_SAFE_NOSYNC | MDBX_UTTERLY_NOSYNC | MDBX_WRITEMAP)) unsupported();
    const auto txn_flags = mdbx_txn_flags(txn);
    if(txn_flags == MDBX_TXN_INVALID || (txn_flags & MDBX_TXN_BLOCKED) ||
        (writing && (txn_flags & (MDBX_TXN_RDONLY | MDBX_TXN_NOMETASYNC | MDBX_TXN_NOSYNC)))) unsupported();
    if(mdbx_env_get_maxkeysize_ex(env, MDBX_DB_DEFAULTS) < 46) unsupported();
    MDBX_envinfo info{};
    checked(mdbx_env_info_ex(env, txn, &info, sizeof(info)));
    if(info.mi_geo.upper != options.map_policy.upper_bytes || info.mi_geo.grow != options.map_policy.growth_bytes ||
        info.mi_geo.lower > info.mi_geo.current || info.mi_geo.current > info.mi_geo.upper) unsupported();
}

// The pinned API's explicit comparator overload checks already-open comparator
// identity as well as persistent flags. A sample comparison would not establish it.
int open_table(MDBX_txn* txn, const std::string& name, MDBX_db_flags_t flags, MDBX_dbi& dbi) {
    return mdbx_dbi_open_ex(txn, name.c_str(), flags, &dbi,
        mdbx_get_keycmp(MDBX_DB_DEFAULTS), mdbx_get_datacmp(MDBX_DB_DEFAULTS));
}
void table_flags(MDBX_txn* txn, MDBX_dbi dbi) {
    unsigned flags = 0;
    checked(mdbx_dbi_flags(txn, dbi, &flags));
    if(flags != 0) unsupported();
}
void capacity(const DurableOptions& options, MDBX_txn* txn, std::uint64_t missing) {
    MDBX_dbi maximum = 0, main = 0;
    checked(mdbx_env_get_maxdbs(options.context->connection()->env_handle(), &maximum));
    checked(mdbx_dbi_open(txn, nullptr, MDBX_DB_DEFAULTS, &main));
    MDBX_stat stats{};
    checked(mdbx_dbi_stat(txn, main, &stats, sizeof(stats)));
    // Main-table entries conservatively include any non-DBI host rows as well.
    const auto occupied = sum(stats.ms_entries, missing);
    const auto declared = sum(options.other_profile_peak_dbis, 3);
    if(occupied > declared || sum(declared, 16) > maximum) unsupported();
}
void validate_options(const DurableOptions& options) {
    if(!options.context || !options.context->connection() || !is_valid_conversation_namespace(options.allocation_namespace) ||
        options.table_prefix.empty() || options.table_prefix.size() > 64 ||
        options.table_prefix.front() < 'a' || options.table_prefix.front() > 'z' ||
        !options.map_policy.upper_bytes || options.map_policy.growth_bytes > options.map_policy.upper_bytes ||
        options.max_live_bytes > 512 * kMiB || options.max_read_raw_bytes > 256 * kMiB ||
        options.max_read_rows > 262144 || options.other_profile_peak_dbis > kMax - 19) {
        throw std::invalid_argument("Invalid durable conversation configuration");
    }
    for(char c : options.table_prefix) {
        if(!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'))
            throw std::invalid_argument("Invalid durable conversation prefix");
    }
    const auto& q = options.quotas;
    for(auto quota : {q.conversations, q.entries, q.revisions_per_entry, q.canonical_bytes, q.mapping_rows, q.mapping_bytes}) {
        if(quota == kMax) throw std::invalid_argument("Durable quotas reserve UINT64_MAX");
    }
    static_cast<void>(encode_descriptor(options.descriptor));
}

} // namespace

DurableOptions::DurableOptions(std::shared_ptr<MdbxStorageContext> input_context, std::string input_namespace,
    Quotas input_quotas, MapPolicy input_map)
    : context(std::move(input_context)), allocation_namespace(std::move(input_namespace)), quotas(input_quotas),
      map_policy(input_map) {}

struct MdbxConversationDurable::Impl final {
    DurableOptions options;
    QualificationHooks hooks;
    std::array<std::string, 3> names;
    std::array<MDBX_dbi, 3> dbis{};
    Profile identity;
    ByteBuffer descriptor;

    Impl(DurableOptions input, QualificationHooks input_hooks) : options(std::move(input)), hooks(input_hooks) {
        validate_options(options);
        names = {options.table_prefix + "_conversation_meta_v1", options.table_prefix + "_conversation_events_v1",
            options.table_prefix + "_conversation_sources_v1"};
        descriptor = encode_descriptor(options.descriptor);
        const auto writable = options.allow_initialize;
        auto txn = options.context->connection()->transaction(writable ? mdbxc::TransactionMode::WRITABLE :
            mdbxc::TransactionMode::READ_ONLY);
        validate_environment(options, txn.handle(), writable);
        std::size_t missing = 0;
        for(std::size_t i = 0; i < dbis.size(); ++i) {
            const auto code = open_table(txn.handle(), names[i], MDBX_DB_DEFAULTS, dbis[i]);
            if(code == MDBX_NOTFOUND) ++missing;
            else { checked(code); table_flags(txn.handle(), dbis[i]); }
        }
        capacity(options, txn.handle(), missing);
        if(missing) {
            if(missing != 3 || !writable) unsupported();
            identity.allocation_namespace = options.allocation_namespace;
            identity.quotas = options.quotas;
            identity.descriptor = options.descriptor;
            std::random_device random;
            for(auto& byte : identity.incarnation) byte = static_cast<std::uint8_t>(random());
            if(std::all_of(identity.incarnation.begin(), identity.incarnation.end(),
                [](std::uint8_t byte) { return byte == 0; })) identity.incarnation[0] = 1;
            const auto body = encode_profile(identity);
            const auto key = encode_profile_key();
            const auto value = wrap_value(identity, TableKind::Metadata, view(key), view(body));
            // No wrapper-owned per-table transaction: all DBIs and marker commit together.
            for(std::size_t i = 0; i < dbis.size(); ++i) {
                checked(open_table(txn.handle(), names[i], MDBX_CREATE, dbis[i]));
                table_flags(txn.handle(), dbis[i]);
            }
            put(txn.handle(), dbis[0], key, value, true);
        } else {
            // Schema bootstrap is a separate, fixed-size verification operation;
            // local per-read limits may legitimately be zero after open.
            auto startup = options;
            startup.max_read_raw_bytes = 65536;
            startup.max_read_rows = 1;
            startup.max_live_bytes = 65536;
            Work work{startup};
            const auto encoded = get(txn.handle(), dbis[0], encode_profile_key(), work, kMaxProfileValueBytes);
            if(!encoded) unsupported();
            identity = stored([&encoded] { return decode_profile_value(*encoded); });
            compatible(identity);
        }
        if(missing) step(WriteStep::BeforeCommit);
        txn.commit();
    }

    void compatible(const Profile& profile) const {
        if(profile.allocation_namespace != options.allocation_namespace ||
            encode_descriptor(profile.descriptor) != descriptor || !same_quotas(profile.quotas, options.quotas) ||
            profile.quota_policy_version != 1) unsupported();
        if(profile.conversation_count > profile.quotas.conversations || profile.entry_count > profile.quotas.entries ||
            profile.mapping_count > profile.quotas.mapping_rows || profile.canonical_charge > profile.quotas.canonical_bytes ||
            profile.mapping_charge > profile.quotas.mapping_bytes || profile.revision_count < profile.entry_count ||
            profile.mapping_count != profile.entry_count || profile.generation < profile.conversation_count ||
            profile.entry_count > profile.generation - profile.conversation_count) corrupt();
    }
    Profile load(MDBX_txn* txn, Work& work, bool writing) const {
        validate_environment(options, txn, writing);
        capacity(options, txn, 0);
        for(std::size_t i = 0; i < dbis.size(); ++i) {
            MDBX_dbi actual = 0;
            checked(open_table(txn, names[i], MDBX_DB_DEFAULTS, actual));
            if(actual != dbis[i]) unsupported();
            table_flags(txn, actual);
        }
        const auto raw = get(txn, dbis[0], encode_profile_key(), work, kMaxProfileValueBytes);
        if(!raw) corrupt();
        auto result = stored([&raw] { return decode_profile_value(*raw); });
        compatible(result);
        if(result.incarnation != identity.incarnation) unsupported();
        return result;
    }
    std::optional<ConversationRow> conversation(MDBX_txn* txn, const Profile& profile,
        const ConversationRef& ref, const ConversationStoreAccess& access, Work& work) const {
        const auto key = encode_conversation_key(ref.id);
        const auto raw = get(txn, dbis[0], key, work,
            profile.descriptor.codec.max_frame_bytes + kConversationValueOverheadBytes);
        if(!raw) return std::nullopt;
        work.prepare(raw->size);
        auto result = stored([&profile, &key, &raw] {
            return decode_conversation_row(unwrap_value(profile, TableKind::Metadata, view(key), *raw));
        });
        if(!result.created_generation || result.created_generation > profile.generation) corrupt();
        if(result.scope.value() != access.scope.value()) return std::nullopt;
        return result;
    }
    ByteBuffer value(const Profile& profile, TableKind kind, const ByteBuffer& key, const ByteBuffer& body) const {
        return wrap_value(profile, kind, view(key), view(body));
    }
    void step(WriteStep point) const { if(hooks.before_commit_step) hooks.before_commit_step(point); }
    template<std::size_t Count>
    void transaction_size(const std::array<std::pair<ByteBuffer, ByteBuffer>, Count>& rows) const {
        if(rows.size() > 16) resource_limit();
        std::uint64_t charge = 0;
        for(const auto& row : rows) charge = sum(charge, sum(row.first.size(), row.second.size()));
        if(charge > options.descriptor.max_transaction_bytes) resource_limit();
    }
    Digest digest(ByteView key) const { return hooks.force_digest_collision ? Digest{} : source_digest(key); }

    struct Bucket final {
        std::optional<SourceRow> match;
        std::uint64_t rows = 0, bytes = 0;
    };
    Bucket bucket(MDBX_txn* txn, const Profile& profile, const ConversationRef& ref,
        const ByteBuffer& full_key, Work& work) const {
        const auto hashed = digest(view(full_key));
        const auto start = encode_source_key({ref.id, hashed, 0});
        Cursor cursor(txn, dbis[2]);
        auto key = val(view(start));
        MDBX_val raw{};
        Bucket result;
        auto code = mdbx_cursor_get(cursor.handle(), &key, &raw, MDBX_SET_RANGE);
        while(code == MDBX_SUCCESS && key.iov_len >= 42 && std::memcmp(key.iov_base, start.data(), 42) == 0) {
            if(result.rows >= profile.descriptor.bucket_rows) corrupt();
            work.visit(view(key), view(raw), profile.descriptor.bucket_bytes - result.bytes);
            const auto physical = decode_source_key(view(key));
            if(physical.slot != result.rows) corrupt();
            ++result.rows;
            result.bytes += raw.iov_len;
            work.scratch(sum(4U * raw.iov_len,
                sum(object_reserve(profile.descriptor.codec),
                    4096U * sizeof(ArtifactBindingId))));
            auto row = stored([&profile, &key, &raw] {
                return decode_source_row(unwrap_value(profile, TableKind::Sources, view(key), view(raw)));
            });
            stored([&row] { validate_logical_source_key(view(row.key)); });
            auto original = stored([&row] { return decode_witness(view(row.witness)); });
            if(row.ref.conversation != ref || row.ref.revision.value() != 1 || !row.generation ||
                row.generation > profile.generation || original.conversation != ref ||
                encode_logical_source_key(original.source) != row.key || digest(view(row.key)) != hashed) corrupt();
            if(row.key == full_key) {
                if(result.match) corrupt();
                work.prepare(sum(sizeof(SourceRow), sum(row.key.size(), row.witness.size())));
                result.match.emplace(std::move(row));
            }
            code = mdbx_cursor_get(cursor.handle(), &key, &raw, MDBX_NEXT);
        }
        if(code != MDBX_SUCCESS && code != MDBX_NOTFOUND) checked(code);
        return result;
    }

    std::optional<RevisionRow> revision(MDBX_txn* txn, const Profile& profile, const EntryRef& ref,
        const ScopeId& scope, Work& work) const {
        const auto key = encode_revision_key({ref.conversation.id, ref.entry, ref.revision});
        const auto raw = get(txn, dbis[1], key, work,
            profile.descriptor.codec.max_frame_bytes + kRevisionValueOverheadBytes);
        if(!raw) return std::nullopt;
        work.prepare(sum(sum(raw->size, sum(raw->size, raw->size)), object_reserve(profile.descriptor.codec)));
        auto row = stored([&profile, &key, &raw] {
            return decode_revision_row(unwrap_value(profile, TableKind::Events, view(key), *raw));
        });
        if(!row.generation || row.generation > profile.generation || row.receipt || ref.revision.value() != 1) corrupt();
        static_cast<void>(scope); // Envelope identity is checked only when decode work is permitted.
        const auto generation_key = encode_generation_key(row.generation);
        const auto generation_raw = get(txn, dbis[1], generation_key, work, kMaxGenerationValueBytes);
        if(!generation_raw) corrupt();
        const auto generation = stored([&profile, &generation_key, &generation_raw] {
            return decode_generation_row(unwrap_value(profile, TableKind::Events, view(generation_key), *generation_raw));
        });
        if(generation.kind != GenerationKind::Import || generation.conversation != ref.conversation ||
            !generation.entry || generation.entry->first != ref.entry || generation.entry->second != ref.revision) corrupt();
        return row;
    }
    ConversationEnvelope decode(const Profile& profile, const RevisionRow& row,
        const EntryRef& ref, const ScopeId& scope) const {
        try {
            auto envelope = deserialize_conversation_envelope(view(row.frame), {profile.descriptor.codec, true});
            if(envelope.metadata.ref != ref || envelope.metadata.scope.value() != scope.value()) corrupt();
            return envelope;
        } catch(const ConversationCodecError&) { corrupt(); }
    }
};

MdbxConversationDurable::MdbxConversationDurable(DurableOptions options, QualificationHooks hooks)
    : m_impl(std::make_unique<Impl>(std::move(options), hooks)) {}
MdbxConversationDurable::~MdbxConversationDurable() = default;

ConversationWriteResult MdbxConversationDurable::create_conversation(
    const ConversationStoreAccess& access, const ConversationRef& ref) {
    if(!valid_access(access)) return refused(ConversationWriteStatus::Denied);
    if(!is_valid_conversation_ref(ref) || ref.allocation_namespace != m_impl->options.allocation_namespace)
        return refused(ConversationWriteStatus::InvalidRequest);
    try {
        Work work{m_impl->options};
        auto txn = m_impl->options.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        auto profile = m_impl->load(txn.handle(), work, true);
        const auto key = encode_conversation_key(ref.id);
        const auto raw = get(txn.handle(), m_impl->dbis[0], key, work,
            profile.descriptor.codec.max_frame_bytes + kConversationValueOverheadBytes);
        if(raw) {
            const auto row = m_impl->conversation(txn.handle(), profile, ref, access, work);
            if(!row) return refused(ConversationWriteStatus::NotFound);
            return {ConversationWriteStatus::NoChange, std::nullopt, profile.generation};
        }
        const auto size = access.scope.value().size();
        if(profile.generation == kMax || !fits(profile.conversation_count, 1, profile.quotas.conversations) ||
            size > profile.descriptor.codec.max_frame_bytes || size > profile.descriptor.codec.max_owned_bytes ||
            !fits(profile.canonical_charge, size, profile.quotas.canonical_bytes)) return refused(ConversationWriteStatus::ResourceLimit);
        work.prepare(sum(4096, sum(size, sum(size, size))));
        ++profile.generation;
        ++profile.conversation_count;
        profile.canonical_charge += size;
        const auto body = encode_conversation_row({access.scope, profile.generation, 1});
        const auto generation_key = encode_generation_key(profile.generation);
        const auto generation_body = encode_generation_row({GenerationKind::Create, ref, std::nullopt});
        const auto profile_key = encode_profile_key();
        const auto profile_body = encode_profile(profile);
        const std::array<std::pair<ByteBuffer, ByteBuffer>, 3> rows{{
            {key, m_impl->value(profile, TableKind::Metadata, key, body)},
            {generation_key, m_impl->value(profile, TableKind::Events, generation_key, generation_body)},
            {profile_key, m_impl->value(profile, TableKind::Metadata, profile_key, profile_body)}}};
        m_impl->transaction_size(rows);
        ConversationWriteResult result{ConversationWriteStatus::Applied, std::nullopt, profile.generation};
        m_impl->step(WriteStep::BeforeWrites);
        put(txn.handle(), m_impl->dbis[0], rows[0].first, rows[0].second, true);
        put(txn.handle(), m_impl->dbis[1], rows[1].first, rows[1].second, true);
        m_impl->step(WriteStep::AfterGeneration);
        put(txn.handle(), m_impl->dbis[0], rows[2].first, rows[2].second, false);
        m_impl->step(WriteStep::BeforeCommit);
        txn.commit();
        return result;
    } catch(const CodecError& error) {
        if(error.code() == CodecCode::ResourceLimit) return refused(ConversationWriteStatus::ResourceLimit);
        throw;
    }
}

ConversationWriteResult MdbxConversationDurable::import_observation(
    const ConversationStoreAccess& access, const ConversationSourceObservation& input) {
    if(!valid_access(access) || access.scope.value() != input.scope.value()) return refused(ConversationWriteStatus::Denied);
    if(!is_valid_conversation_ref(input.conversation) ||
        input.conversation.allocation_namespace != m_impl->options.allocation_namespace)
        return refused(ConversationWriteStatus::NotFound);
    try {
        Work work{m_impl->options};
        auto txn = m_impl->options.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        auto profile = m_impl->load(txn.handle(), work, true);
        auto conversation = m_impl->conversation(txn.handle(), profile, input.conversation, access, work);
        if(!conversation) return refused(ConversationWriteStatus::NotFound);
        work.scratch(kMaxExternalObjectRefBytes);
        const auto preflight = detail::preflight_observation(input, profile.descriptor.codec.fields,
            static_cast<std::size_t>(profile.descriptor.max_observation_bytes));
        if(preflight.status != ConversationWriteStatus::Applied) return refused(preflight.status);
        // Validation probes are temporary, not future writer buffers. Replay
        // must not reserve normalisation/encoding storage it will never use.
        work.scratch(sum(3U * preflight.bytes, 2U * object_reserve(profile.descriptor.codec)));
        const auto source_status = detail::source_status(input.source, input.conversation, input.scope,
            profile.descriptor.codec.fields);
        if(source_status != ConversationWriteStatus::Applied) return refused(source_status);
        const auto target_status = detail::target_status(input, profile.descriptor.codec.fields);
        if(target_status != ConversationWriteStatus::Applied) return refused(target_status);
        const auto observation_status = detail::observation_status(input);
        if(observation_status != ConversationWriteStatus::Applied) return refused(observation_status);
        work.prepare(sum(preflight.bytes, 4096)); // Retained logical key, before allocation.
        auto full_key = encode_logical_source_key(input.source);
        auto bucket = m_impl->bucket(txn.handle(), profile, input.conversation, full_key, work);
        if(bucket.match) {
            {
                work.scratch(sum(2U * bucket.match->witness.size(), sum(object_reserve(profile.descriptor.codec),
                    4096U * sizeof(ArtifactBindingId))));
                const auto original = stored([&bucket] { return decode_witness(view(bucket.match->witness)); });
                if(!detail::same_witness(input, original)) return refused(ConversationWriteStatus::Conflict);
                if(original.scope.value() != access.scope.value()) corrupt();
            }
            const auto record = m_impl->revision(txn.handle(), profile, bucket.match->ref, access.scope, work);
            if(!record || record->generation != bucket.match->generation) corrupt();
            static_cast<void>(m_impl->decode(profile, *record, bucket.match->ref, access.scope));
            return {ConversationWriteStatus::NoChange, bucket.match->ref, bucket.match->generation};
        }
        if(profile.generation == kMax || profile.revision_count == kMax || conversation->next_entry == kMax ||
            !profile.quotas.revisions_per_entry ||
            !fits(profile.entry_count, 1, profile.quotas.entries) || !fits(profile.mapping_count, 1, profile.quotas.mapping_rows))
            return refused(ConversationWriteStatus::ResourceLimit);
        std::optional<ConversationLinkTarget> resolved;
        // The selected target survives the lookup phase; bucket and decoded
        // target-revision scratch do not survive into writer preparation.
        work.prepare(sum(preflight.bytes, object_reserve(profile.descriptor.codec)));
        {
        WorkPhase lookup(work);
        if(const auto* result = std::get_if<ConversationSourceToolResult>(&input.payload)) {
            if(const auto* exact = std::get_if<EntryRef>(&result->call)) resolved = *exact;
            else if(const auto* target = std::get_if<ConversationSourceTarget>(&result->call)) {
                const auto key = encode_logical_source_key(target->source);
                const auto found = m_impl->bucket(txn.handle(), profile, input.conversation, key, work);
                if(found.match) resolved = found.match->ref;
                else resolved = detail::unresolved_source_target(*target);
            } else resolved = copy_unresolved_conversation_target(std::get<UnresolvedConversationTarget>(result->call));
            if(const auto* exact = std::get_if<EntryRef>(&*resolved)) {
                if(!is_valid_entry_ref(*exact)) return refused(ConversationWriteStatus::InvalidRequest);
                const auto target = m_impl->revision(txn.handle(), profile, *exact, access.scope, work);
                if(!target) return refused(ConversationWriteStatus::InvalidRequest);
                const auto envelope = m_impl->decode(profile, *target, *exact, access.scope);
                const auto* call = std::get_if<ConversationToolCallObserved>(&envelope.payload);
                if(!call || call->call_id != result->call_id) return refused(ConversationWriteStatus::InvalidRequest);
            }
        }
        }
        // Twelve logical-input copies cover normalisation, AMCE validation/
        // encode scratch, witness, physical bodies and wrapped values. Four
        // independent object slabs cover non-wire container storage. This is a
        // conservative buffer charge, not allocator overhead or an RSS bound.
        work.prepare(sum(12U * sum(preflight.bytes, 4096), 4U * object_reserve(profile.descriptor.codec)));
        const EntryRef ref{input.conversation, EntryId{conversation->next_entry}, EventRevision{std::uint64_t{1}}};
        auto envelope = detail::normalize_observation(input, ref, std::move(resolved));
        auto frame = serialize_conversation_envelope(envelope, profile.descriptor.codec);
        auto witness = encode_witness(input);
        const auto logical = detail::source_key(input.source);
        const auto mapping_charge = sum(sum(preflight.bytes, logical.charge()), sum(32, input.conversation.allocation_namespace.size()));
        if(!fits(profile.canonical_charge, frame.size(), profile.quotas.canonical_bytes) ||
            !fits(profile.mapping_charge, mapping_charge, profile.quotas.mapping_bytes)) return refused(ConversationWriteStatus::ResourceLimit);
        ++profile.generation;
        ++profile.entry_count;
        ++profile.revision_count;
        ++profile.mapping_count;
        profile.canonical_charge += frame.size();
        profile.mapping_charge += mapping_charge;
        ++conversation->next_entry;
        const auto revision_key = encode_revision_key({ref.conversation.id, ref.entry, ref.revision});
        const auto revision_body = encode_revision_row({profile.generation, std::nullopt, std::move(frame)});
        const auto source_key = encode_source_key({ref.conversation.id, m_impl->digest(view(full_key)), static_cast<std::uint32_t>(bucket.rows)});
        const auto source_body = encode_source_row({std::move(full_key), std::move(witness), ref, profile.generation});
        auto source_value = m_impl->value(profile, TableKind::Sources, source_key, source_body);
        if(bucket.rows >= profile.descriptor.bucket_rows ||
            !fits(bucket.bytes, source_value.size(), profile.descriptor.bucket_bytes)) return refused(ConversationWriteStatus::ResourceLimit);
        const auto generation_key = encode_generation_key(profile.generation);
        const auto generation_body = encode_generation_row({GenerationKind::Import, ref.conversation, std::make_pair(ref.entry, ref.revision)});
        const auto conversation_key = encode_conversation_key(ref.conversation.id);
        const auto conversation_body = encode_conversation_row(*conversation);
        const auto profile_key = encode_profile_key();
        const auto profile_body = encode_profile(profile);
        // An array avoids initializer_list's hidden copy of every owning value.
        const std::array<std::pair<ByteBuffer, ByteBuffer>, 5> rows{{
            {revision_key, m_impl->value(profile, TableKind::Events, revision_key, revision_body)},
            {source_key, std::move(source_value)},
            {generation_key, m_impl->value(profile, TableKind::Events, generation_key, generation_body)},
            {conversation_key, m_impl->value(profile, TableKind::Metadata, conversation_key, conversation_body)},
            {profile_key, m_impl->value(profile, TableKind::Metadata, profile_key, profile_body)}}};
        m_impl->transaction_size(rows);
        ConversationWriteResult result{ConversationWriteStatus::Applied, ref, profile.generation};
        static_assert(std::is_nothrow_move_constructible_v<ConversationWriteResult>);
        m_impl->step(WriteStep::BeforeWrites);
        put(txn.handle(), m_impl->dbis[1], rows[0].first, rows[0].second, true);
        m_impl->step(WriteStep::AfterRevision);
        put(txn.handle(), m_impl->dbis[2], rows[1].first, rows[1].second, true);
        m_impl->step(WriteStep::AfterSource);
        put(txn.handle(), m_impl->dbis[1], rows[2].first, rows[2].second, true);
        m_impl->step(WriteStep::AfterGeneration);
        put(txn.handle(), m_impl->dbis[0], rows[3].first, rows[3].second, false);
        put(txn.handle(), m_impl->dbis[0], rows[4].first, rows[4].second, false);
        m_impl->step(WriteStep::BeforeCommit);
        txn.commit();
        return result;
    } catch(const ConversationCodecError& error) {
        return refused(error.code() == ConversationCodecCode::ResourceLimit ?
            ConversationWriteStatus::ResourceLimit : ConversationWriteStatus::InvalidRequest);
    } catch(const CodecError& error) {
        if(error.code() == CodecCode::ResourceLimit) return refused(ConversationWriteStatus::ResourceLimit);
        throw;
    }
}

ConversationReadResult MdbxConversationDurable::read_exact(const ConversationStoreAccess& access,
    const EntryRef& ref, const ConversationReadLimits& limits) const {
    if(!valid_access(access)) return refused(ConversationReadStatus::Denied);
    if(!is_valid_entry_ref(ref) || ref.conversation.allocation_namespace != m_impl->options.allocation_namespace ||
        !valid_limits(limits)) return refused(ConversationReadStatus::InvalidRequest);
    try {
        Work work{m_impl->options};
        auto txn = m_impl->options.context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto profile = m_impl->load(txn.handle(), work, false);
        if(!m_impl->conversation(txn.handle(), profile, ref.conversation, access, work)) return refused(ConversationReadStatus::NotFound);
        if(!limits.max_items || !limits.max_visited_records) return refused(ConversationReadStatus::BudgetExhausted);
        const auto row = m_impl->revision(txn.handle(), profile, ref, access.scope, work);
        if(!row) return refused(ConversationReadStatus::NotFound);
        auto result = refused(ConversationReadStatus::Complete);
        result.commit_generation = profile.generation;
        if(row->frame.size() > limits.max_output_bytes) { result.status = ConversationReadStatus::OversizedItem; return result; }
        if(row->frame.size() > limits.max_decoded_bytes) { result.status = ConversationReadStatus::BudgetExhausted; return result; }
        result.records.push_back({m_impl->decode(profile, *row, ref, access.scope),
            std::nullopt, row->generation});
        result.visited_records = 1;
        result.decoded_bytes = result.output_bytes = row->frame.size();
        return result;
    } catch(const CodecError& error) {
        if(error.code() == CodecCode::ResourceLimit) return refused(ConversationReadStatus::BudgetExhausted);
        throw;
    }
}

ConversationReadResult MdbxConversationDurable::read_history(const ConversationStoreAccess& access,
    const ConversationRef& ref, const ConversationReadLimits& limits, std::uint64_t seek_entry, std::uint64_t frontier) const {
    if(!valid_access(access)) return refused(ConversationReadStatus::Denied);
    if(!is_valid_conversation_ref(ref) || ref.allocation_namespace != m_impl->options.allocation_namespace ||
        !valid_limits(limits)) return refused(ConversationReadStatus::InvalidRequest);
    try {
        Work work{m_impl->options};
        auto txn = m_impl->options.context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto profile = m_impl->load(txn.handle(), work, false);
        if(!m_impl->conversation(txn.handle(), profile, ref, access, work)) return refused(ConversationReadStatus::NotFound);
        if(frontier > profile.generation) return refused(ConversationReadStatus::InvalidRequest);
        if(!frontier) frontier = profile.generation;
        auto result = refused(ConversationReadStatus::Complete);
        result.commit_generation = frontier;
        if(seek_entry == kMax) return result;
        const auto start = encode_revision_key({ref.id, EntryId{seek_entry + 1}, EventRevision{std::uint64_t{1}}});
        Cursor cursor(txn.handle(), m_impl->dbis[1]);
        auto key = val(view(start));
        MDBX_val value{};
        auto code = mdbx_cursor_get(cursor.handle(), &key, &value, MDBX_SET_RANGE);
        while(code == MDBX_SUCCESS && key.iov_len >= 10 && std::memcmp(key.iov_base, start.data(), 10) == 0) {
            work.visit(view(key), view(value),
                profile.descriptor.codec.max_frame_bytes + kRevisionValueOverheadBytes);
            const auto physical = decode_revision_key(view(key));
            if(result.records.size() >= limits.max_items) {
                result.status = result.records.empty() ? ConversationReadStatus::BudgetExhausted : ConversationReadStatus::Partial;
                return result;
            }
            if(result.visited_records >= limits.max_visited_records) { result.status = ConversationReadStatus::BudgetExhausted; return result; }
            // revision() charges/validates the exact row and generation index before copies.
            const EntryRef exact{ref, physical.entry, physical.revision};
            const auto row = m_impl->revision(txn.handle(), profile, exact, access.scope, work);
            if(!row) corrupt();
            if(row->generation > frontier) break;
            if(row->frame.size() > limits.max_output_bytes - result.output_bytes) {
                result.status = result.records.empty() ? ConversationReadStatus::OversizedItem : ConversationReadStatus::Partial;
                return result;
            }
            if(row->frame.size() > limits.max_decoded_bytes - result.decoded_bytes) {
                result.status = ConversationReadStatus::BudgetExhausted;
                return result;
            }
            result.records.push_back({m_impl->decode(profile, *row, exact, access.scope),
                std::nullopt, row->generation});
            ++result.visited_records;
            result.decoded_bytes += row->frame.size();
            result.output_bytes += row->frame.size();
            code = mdbx_cursor_get(cursor.handle(), &key, &value, MDBX_NEXT);
        }
        if(code != MDBX_SUCCESS && code != MDBX_NOTFOUND) checked(code);
        return result;
    } catch(const CodecError& error) {
        if(error.code() == CodecCode::ResourceLimit) return refused(ConversationReadStatus::BudgetExhausted);
        throw;
    }
}

} // namespace agent_memory::detail::conversation_durable
#endif
