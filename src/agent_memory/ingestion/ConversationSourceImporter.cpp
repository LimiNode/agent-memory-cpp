#include "ConversationSourceImporter.hpp"

#include "ConversationSourceImporterInternal.hpp"

#include <limits>
#include <map>
#include <mutex>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::size_t kMappingByteCap = 256U * 1024U * 1024U;
constexpr std::size_t kMappingCountCap = 65536;

ConversationWriteResult refusal(ConversationWriteStatus status) {
    return {status, std::nullopt, 0};
}

bool valid_access(const ConversationStoreAccess& access) {
    return !access.scope.empty() && is_valid_conversation_namespace(access.policy_binding);
}

} // namespace

IConversationImportSink::~IConversationImportSink() = default;

struct InMemoryConversationImportSink::Impl final {
    struct Mapping final {
        ConversationSourceObservation witness;
        EntryRef original_ref;
        std::uint64_t generation = 0;
    };

    struct Conversation final {
        ScopeId scope;
        std::uint64_t next_entry = 1;
        std::map<detail::SourceKey, Mapping> mappings;
    };

    explicit Impl(ConversationImportOptions input) : options(std::move(input)), store(options.store) {}

    ConversationImportOptions options;
    InMemoryConversationStore store;
    std::mutex mutex;
    std::map<ConversationRef, Conversation> conversations;
    std::size_t mapping_count = 0;
    std::size_t mapping_bytes = 0;

    ConversationLinkTarget resolve_target(const ConversationSourceCallTarget& input, const Conversation& conversation) const {
        if(const auto* ref = std::get_if<EntryRef>(&input)) return *ref;
        if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&input))
            return copy_unresolved_conversation_target(*unresolved);
        const auto& source_target = std::get<ConversationSourceTarget>(input);
        const auto found = conversation.mappings.find(detail::source_key(source_target.source));
        if(found != conversation.mappings.end()) return found->second.original_ref;
        return detail::unresolved_source_target(source_target);
    }
};

InMemoryConversationImportSink::InMemoryConversationImportSink(ConversationImportOptions options) {
    if(options.max_observation_bytes > detail::kConversationImportObservationByteCap ||
        options.max_mappings > kMappingCountCap ||
        options.max_mapping_bytes > kMappingByteCap) throw std::invalid_argument("Invalid conversation import options");
    m_impl = std::make_unique<Impl>(std::move(options));
}

InMemoryConversationImportSink::~InMemoryConversationImportSink() = default;

ConversationWriteResult InMemoryConversationImportSink::create_conversation(
    const ConversationStoreAccess& access, const ConversationRef& ref) {
    if(!valid_access(access)) return refusal(ConversationWriteStatus::Denied);
    if(!is_valid_conversation_ref(ref) || ref.allocation_namespace != m_impl->options.store.allocation_namespace) {
        return refusal(ConversationWriteStatus::InvalidRequest);
    }
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto existing = m_impl->conversations.find(ref);
    if(existing != m_impl->conversations.end()) {
        if(existing->second.scope.value() != access.scope.value()) return refusal(ConversationWriteStatus::NotFound);
        return m_impl->store.create_conversation(access, ref);
    }
    if(m_impl->conversations.size() >= m_impl->options.store.max_conversations ||
        access.scope.value().size() > m_impl->options.store.codec_limits.max_frame_bytes ||
        access.scope.value().size() > m_impl->options.store.codec_limits.max_owned_bytes ||
        access.scope.value().size() > m_impl->options.store.max_storage_bytes) {
        return refusal(ConversationWriteStatus::ResourceLimit);
    }
    // Bookkeeping must exist before the canonical create can become visible.
    const auto inserted = m_impl->conversations.emplace(ref, Impl::Conversation{access.scope, 1, {}});
    try {
        auto result = m_impl->store.create_conversation(access, ref);
        if(result.status != ConversationWriteStatus::Applied) m_impl->conversations.erase(inserted.first);
        return result;
    } catch(...) {
        m_impl->conversations.erase(inserted.first);
        throw;
    }
}

ConversationWriteResult InMemoryConversationImportSink::import_observation(
    const ConversationStoreAccess& access, const ConversationSourceObservation& observation) {
    if(!valid_access(access) || access.scope.value() != observation.scope.value()) {
        return refusal(ConversationWriteStatus::Denied);
    }
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto found = m_impl->conversations.find(observation.conversation);
    if(found == m_impl->conversations.end() || found->second.scope.value() != access.scope.value()) {
        return refusal(ConversationWriteStatus::NotFound);
    }
    auto& conversation = found->second;
    const auto preflight = detail::preflight_observation(observation,
        m_impl->options.store.codec_limits.fields, m_impl->options.max_observation_bytes);
    if(preflight.status != ConversationWriteStatus::Applied) return refusal(preflight.status);
    const auto source = detail::source_status(observation.source, observation.conversation, observation.scope,
        m_impl->options.store.codec_limits.fields);
    if(source != ConversationWriteStatus::Applied) return refusal(source);
    const auto target = detail::target_status(observation, m_impl->options.store.codec_limits.fields);
    if(target != ConversationWriteStatus::Applied) return refusal(target);
    const auto classification = detail::observation_status(observation);
    if(classification != ConversationWriteStatus::Applied) return refusal(classification);
    auto key = detail::source_key(observation.source);
    const auto existing = conversation.mappings.find(key);
    if(existing != conversation.mappings.end()) {
        if(!detail::same_witness(observation, existing->second.witness)) return refusal(ConversationWriteStatus::Conflict);
        // Do not renormalize a previously unresolved target against today's map.
        return {ConversationWriteStatus::NoChange, existing->second.original_ref, existing->second.generation};
    }
    if(m_impl->mapping_count >= m_impl->options.max_mappings ||
        conversation.next_entry == std::numeric_limits<std::uint64_t>::max()) {
        return refusal(ConversationWriteStatus::ResourceLimit);
    }
    // All components have bounded preflight sizes, so this sum cannot overflow size_t.
    const auto charge = preflight.bytes + key.charge() + 32 + observation.conversation.allocation_namespace.size();
    if(charge > m_impl->options.max_mapping_bytes - m_impl->mapping_bytes) {
        return refusal(ConversationWriteStatus::ResourceLimit);
    }
    const EntryRef ref{observation.conversation, EntryId{conversation.next_entry}, EventRevision{std::uint64_t{1}}};
    std::optional<ConversationLinkTarget> resolved_target;
    if(const auto* result = std::get_if<ConversationSourceToolResult>(&observation.payload))
        resolved_target = m_impl->resolve_target(result->call, conversation);
    auto envelope = detail::normalize_observation(observation, ref, std::move(resolved_target));
    // Result and witness allocations precede canonical append. Nothing after Applied allocates.
    ConversationWriteResult result{ConversationWriteStatus::Applied, ref, 0};
    Impl::Mapping row{detail::copy_observation(observation), ref, 0};
    const auto inserted = conversation.mappings.emplace(std::move(key), std::move(row));
    static_assert(std::is_nothrow_move_constructible_v<ConversationWriteResult>);
    try {
        auto appended = m_impl->store.append(access, envelope);
        if(appended.status != ConversationWriteStatus::Applied) {
            conversation.mappings.erase(inserted.first);
            return appended;
        }
        inserted.first->second.generation = appended.commit_generation;
        result.commit_generation = appended.commit_generation;
        ++conversation.next_entry;
        ++m_impl->mapping_count;
        m_impl->mapping_bytes += charge;
        return result;
    } catch(...) {
        conversation.mappings.erase(inserted.first);
        throw;
    }
}

ConversationWriteResult InMemoryConversationImportSink::correct(const ConversationStoreAccess& access,
    const ConversationEnvelope& envelope, EventRevision expected, const std::string& reason_code) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    return m_impl->store.correct(access, envelope, expected, reason_code);
}

const IConversationStore& InMemoryConversationImportSink::history() const noexcept {
    return m_impl->store;
}

} // namespace agent_memory
