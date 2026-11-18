#include "MdbxPromptAssemblyProfileStorage.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <cctype>
#include <limits>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kPayloadVersion =
    "agent_memory.prompt_assembly_profile.v1";
constexpr std::size_t kMaxCollectionItems = 1U << 20U;

class Writer final {
public:
    void string(std::string_view value) {
        u64(static_cast<std::uint64_t>(value.size()));
        m_bytes.append(value.data(), value.size());
    }

    void u8(std::uint8_t value) {
        m_bytes.push_back(static_cast<char>(value));
    }

    void u64(std::uint64_t value) {
        for(unsigned shift = 0U; shift < 64U; shift += 8U) {
            m_bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
        }
    }

    [[nodiscard]] std::string finish() && { return std::move(m_bytes); }

private:
    std::string m_bytes;
};

class Reader final {
public:
    explicit Reader(std::string_view bytes)
        : m_bytes(bytes) {}

    [[nodiscard]] std::string string() {
        const auto size = u64();
        if(size > m_bytes.size() - m_offset ||
           size > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max())) {
            throw std::runtime_error("prompt profile payload string is truncated");
        }
        std::string result{m_bytes.data() + m_offset, static_cast<std::size_t>(size)};
        m_offset += static_cast<std::size_t>(size);
        return result;
    }

    [[nodiscard]] std::uint8_t u8() {
        if(m_offset >= m_bytes.size()) {
            throw std::runtime_error("prompt profile payload is truncated");
        }
        return static_cast<std::uint8_t>(m_bytes[m_offset++]);
    }

    [[nodiscard]] std::uint64_t u64() {
        if(m_bytes.size() - m_offset < 8U) {
            throw std::runtime_error("prompt profile payload integer is truncated");
        }
        std::uint64_t result = 0;
        for(unsigned shift = 0U; shift < 64U; shift += 8U) {
            result |= static_cast<std::uint64_t>(
                static_cast<unsigned char>(m_bytes[m_offset++])) << shift;
        }
        return result;
    }

    [[nodiscard]] std::size_t count() {
        const auto value = u64();
        if(value > kMaxCollectionItems ||
           value > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max())) {
            throw std::runtime_error("prompt profile collection count is too large");
        }
        return static_cast<std::size_t>(value);
    }

    void optional_string(std::optional<std::string>& target) {
        const auto present = u8();
        if(present > 1U) {
            throw std::runtime_error("prompt profile optional marker is invalid");
        }
        if(present != 0U) target = string();
    }

    void end() const {
        if(m_offset != m_bytes.size()) {
            throw std::runtime_error("prompt profile payload has trailing bytes");
        }
    }

private:
    std::string_view m_bytes;
    std::size_t m_offset = 0U;
};

std::string sanitize_table_part(std::string value) {
    if(value.empty()) value = "agent_memory";
    for(char& character : value) {
        if(!(std::isalnum(static_cast<unsigned char>(character)) || character == '_')) {
            character = '_';
        }
    }
    return value;
}

std::string table_name(const std::string& prefix, std::string_view suffix) {
    return prefix + "_" + std::string{suffix};
}

std::uint64_t encode_variant_priority(std::int32_t priority) noexcept {
    const auto shifted = static_cast<std::int64_t>(priority) -
        static_cast<std::int64_t>(std::numeric_limits<std::int32_t>::min());
    return static_cast<std::uint64_t>(shifted);
}

std::int32_t decode_variant_priority(std::uint64_t encoded) {
    if(encoded > static_cast<std::uint64_t>(std::numeric_limits<std::uint32_t>::max())) {
        throw std::runtime_error("prompt profile variant priority is out of range");
    }
    const auto shifted = static_cast<std::int64_t>(encoded) +
        static_cast<std::int64_t>(std::numeric_limits<std::int32_t>::min());
    return static_cast<std::int32_t>(shifted);
}

void validate_profile(const PromptAssemblyProfile& profile) {
    if(profile.profile_id.empty() || profile.revision == 0U ||
       profile.renderer_revision.empty() || profile.limits.max_expansions == 0U ||
       profile.limits.max_result_bytes == 0U || profile.limits.max_template_bytes == 0U) {
        throw std::invalid_argument("prompt assembly profile identity or limits are invalid");
    }

    std::set<ContentBlockId> ids;
    for(const auto& selection : profile.selections) {
        if(selection.block_id.empty() || !ids.insert(selection.block_id).second) {
            throw std::invalid_argument("prompt assembly profile has duplicate block selection");
        }
        if(selection.required && !selection.enabled) {
            throw std::invalid_argument("required prompt block cannot be disabled");
        }
        if(selection.variant_group.empty() != selection.variant_value.empty()) {
            throw std::invalid_argument("prompt profile variant fields must be paired");
        }
    }
    for(const auto& variant : profile.variants) {
        if(variant.first.empty() || variant.second.empty()) {
            throw std::invalid_argument("prompt profile variants must have non-empty names and values");
        }
    }
}

void write_optional(Writer& writer, const std::optional<std::string>& value) {
    writer.u8(value ? 1U : 0U);
    if(value) writer.string(*value);
}

std::string serialize_profile(const PromptAssemblyProfile& profile) {
    validate_profile(profile);
    Writer writer;
    writer.string(kPayloadVersion);
    writer.string(profile.profile_id);
    writer.u64(profile.revision);
    writer.string(profile.renderer_revision);
    writer.u8(profile.default_enabled ? 1U : 0U);
    writer.u64(static_cast<std::uint64_t>(profile.limits.max_template_bytes));
    writer.u64(static_cast<std::uint64_t>(profile.limits.max_expansions));
    writer.u64(static_cast<std::uint64_t>(profile.limits.max_result_bytes));
    writer.u64(static_cast<std::uint64_t>(profile.selections.size()));
    for(const auto& selection : profile.selections) {
        writer.string(selection.block_id.value());
        writer.u8(selection.enabled ? 1U : 0U);
        writer.u8(selection.required ? 1U : 0U);
        writer.u8(selection.control_instruction ? 1U : 0U);
        writer.u8(selection.allow_templates ? 1U : 0U);
        write_optional(writer, selection.locale);
        write_optional(writer, selection.style);
        write_optional(writer, selection.consumer_profile);
        writer.string(selection.variant_group);
        writer.string(selection.variant_value);
        writer.u64(encode_variant_priority(selection.variant_priority));
    }
    writer.u64(static_cast<std::uint64_t>(profile.variants.size()));
    for(const auto& variant : profile.variants) {
        writer.string(variant.first);
        writer.string(variant.second);
    }
    return std::move(writer).finish();
}

bool read_bool(Reader& reader, const char* field) {
    const auto value = reader.u8();
    if(value > 1U) throw std::runtime_error(std::string("invalid prompt profile boolean: ") + field);
    return value != 0U;
}

PromptAssemblyProfile deserialize_profile(std::string_view bytes) {
    Reader reader{bytes};
    if(reader.string() != kPayloadVersion) {
        throw std::runtime_error("unsupported prompt assembly profile payload version");
    }

    PromptAssemblyProfile profile;
    profile.profile_id = reader.string();
    profile.revision = reader.u64();
    profile.renderer_revision = reader.string();
    profile.default_enabled = read_bool(reader, "default_enabled");
    const auto template_bytes = reader.u64();
    const auto expansions = reader.u64();
    const auto result_bytes = reader.u64();
    if(template_bytes > std::numeric_limits<std::size_t>::max() ||
       expansions > std::numeric_limits<std::size_t>::max() ||
       result_bytes > std::numeric_limits<std::size_t>::max()) {
        throw std::runtime_error("prompt profile limit exceeds size_t");
    }
    profile.limits.max_template_bytes = static_cast<std::size_t>(template_bytes);
    profile.limits.max_expansions = static_cast<std::size_t>(expansions);
    profile.limits.max_result_bytes = static_cast<std::size_t>(result_bytes);

    const auto selection_count = reader.count();
    profile.selections.reserve(selection_count);
    for(std::size_t index = 0U; index < selection_count; ++index) {
        PromptBlockSelection selection;
        selection.block_id = ContentBlockId{reader.string()};
        selection.enabled = read_bool(reader, "enabled");
        selection.required = read_bool(reader, "required");
        selection.control_instruction = read_bool(reader, "control_instruction");
        selection.allow_templates = read_bool(reader, "allow_templates");
        reader.optional_string(selection.locale);
        reader.optional_string(selection.style);
        reader.optional_string(selection.consumer_profile);
        selection.variant_group = reader.string();
        selection.variant_value = reader.string();
        selection.variant_priority = decode_variant_priority(reader.u64());
        profile.selections.push_back(std::move(selection));
    }

    const auto variant_count = reader.count();
    for(std::size_t index = 0U; index < variant_count; ++index) {
        auto key = reader.string();
        auto value = reader.string();
        if(!profile.variants.emplace(std::move(key), std::move(value)).second) {
            throw std::runtime_error("prompt profile contains duplicate variant group");
        }
    }
    reader.end();
    validate_profile(profile);
    return profile;
}

std::string profile_key(const std::string& profile_id, std::uint64_t revision) {
    Writer writer;
    writer.string(profile_id);
    writer.u64(revision);
    return std::move(writer).finish();
}

std::string revision_value(std::uint64_t revision) {
    Writer writer;
    writer.u64(revision);
    return std::move(writer).finish();
}

std::uint64_t read_revision_value(std::string_view bytes) {
    Reader reader{bytes};
    const auto revision = reader.u64();
    reader.end();
    if(revision == 0U) throw std::runtime_error("prompt profile head has zero revision");
    return revision;
}

} // namespace

class MdbxPromptAssemblyProfileStorage::Impl final {
public:
    explicit Impl(MdbxPromptAssemblyProfileStorageOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(
                                      std::move(options.path),
                                      options.relative_to_exe,
                                      options.max_dbs)),
          prefix(sanitize_table_part(std::move(options.table_prefix))),
          history(context->connection(), table_name(prefix, "prompt_profiles")),
          heads(context->connection(), table_name(prefix, "prompt_profile_heads")) {}

    void put(PromptAssemblyProfile profile) {
        validate_profile(profile);
        const auto payload = serialize_profile(profile);
        const auto key = profile_key(profile.profile_id, profile.revision);
        std::lock_guard<std::mutex> lock(mutex);
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto existing = history.find(key, txn);
        if(existing) {
            if(*existing != payload) {
                throw std::runtime_error("prompt profile revision already has a different payload");
            }
            txn.commit();
            return;
        }

        std::uint64_t previous_revision = 0U;
        if(const auto head = heads.find(profile.profile_id, txn)) {
            previous_revision = read_revision_value(*head);
        }
        history.insert_or_assign(key, payload, txn);
        if(profile.revision > previous_revision) {
            heads.insert_or_assign(profile.profile_id, revision_value(profile.revision), txn);
        }
        txn.commit();
    }

    [[nodiscard]] std::optional<PromptAssemblyProfile> find(
        const std::string& profile_id, std::uint64_t revision) const {
        if(profile_id.empty() || revision == 0U) {
            throw std::invalid_argument("prompt profile lookup requires identity and revision");
        }
        std::lock_guard<std::mutex> lock(mutex);
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto payload = history.find(profile_key(profile_id, revision), txn);
        txn.commit();
        if(!payload) return std::nullopt;
        auto profile = deserialize_profile(*payload);
        if(profile.profile_id != profile_id || profile.revision != revision) {
            throw std::runtime_error("prompt profile key does not match payload identity");
        }
        return profile;
    }

    [[nodiscard]] std::optional<PromptAssemblyProfile> latest(
        const std::string& profile_id) const {
        if(profile_id.empty()) {
            throw std::invalid_argument("prompt profile lookup requires an identity");
        }
        std::lock_guard<std::mutex> lock(mutex);
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto head = heads.find(profile_id, txn);
        if(!head) {
            txn.commit();
            return std::nullopt;
        }
        const auto revision = read_revision_value(*head);
        const auto payload = history.find(profile_key(profile_id, revision), txn);
        txn.commit();
        if(!payload) {
            throw std::runtime_error("prompt profile head references a missing revision");
        }
        auto profile = deserialize_profile(*payload);
        if(profile.profile_id != profile_id || profile.revision != revision) {
            throw std::runtime_error("prompt profile head does not match payload identity");
        }
        return profile;
    }

    std::shared_ptr<MdbxStorageContext> context;
    std::string prefix;
    mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::string, std::string> history;
    mdbxc::KeyValueTable<std::string, std::string> heads;
};

MdbxPromptAssemblyProfileStorage::MdbxPromptAssemblyProfileStorage(
    MdbxPromptAssemblyProfileStorageOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxPromptAssemblyProfileStorage::~MdbxPromptAssemblyProfileStorage() = default;

MdbxPromptAssemblyProfileStorage::MdbxPromptAssemblyProfileStorage(
    MdbxPromptAssemblyProfileStorage&& other) noexcept = default;

MdbxPromptAssemblyProfileStorage& MdbxPromptAssemblyProfileStorage::operator=(
    MdbxPromptAssemblyProfileStorage&& other) noexcept = default;

void MdbxPromptAssemblyProfileStorage::put_profile(PromptAssemblyProfile profile) {
    m_impl->put(std::move(profile));
}

std::optional<PromptAssemblyProfile>
MdbxPromptAssemblyProfileStorage::find_profile(
    const std::string& profile_id, std::uint64_t revision) const {
    return m_impl->find(profile_id, revision);
}

std::optional<PromptAssemblyProfile>
MdbxPromptAssemblyProfileStorage::find_latest_profile(
    const std::string& profile_id) const {
    return m_impl->latest(profile_id);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
