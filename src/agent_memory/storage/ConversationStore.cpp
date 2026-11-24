#include "ConversationStore.hpp"

#include <algorithm>
#include <iterator>
#include <limits>
#include <map>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {
constexpr std::size_t kByteCap = 256U * 1024U * 1024U;
constexpr std::size_t kItemCap = 65536;

bool valid_access(const ConversationStoreAccess& access) {
    return !access.scope.empty() && is_valid_conversation_namespace(access.policy_binding);
}

bool valid_limits(const ConversationReadLimits& limits) {
    return limits.max_items <= kItemCap && limits.max_output_bytes <= kByteCap &&
        limits.max_visited_records <= 262144 && limits.max_decoded_bytes <= kByteCap;
}

int family(const ConversationEnvelope& envelope) {
    if(const auto* value = std::get_if<ConversationPayload>(&envelope.payload)) {
        return static_cast<int>(value->index());
    }
    if(std::holds_alternative<ConversationToolCallObserved>(envelope.payload)) return 5;
    if(std::holds_alternative<ConversationToolResultObserved>(envelope.payload)) return 6;
    return -1;
}

const EntryRef* affected(const ConversationEnvelope& envelope) {
    const auto* payload = std::get_if<ConversationPayload>(&envelope.payload);
    if(!payload) return nullptr;
    if(const auto* value = std::get_if<ConversationEdited>(payload)) return &value->affected;
    if(const auto* value = std::get_if<ConversationTombstoned>(payload)) return &value->affected;
    if(const auto* value = std::get_if<ConversationRestored>(payload)) return &value->affected;
    return nullptr;
}

const ConversationContent* content(const ConversationEnvelope& envelope) {
    const auto* payload = std::get_if<ConversationPayload>(&envelope.payload);
    if(!payload) return nullptr;
    if(const auto* value = std::get_if<ConversationMessageCreated>(payload)) return &value->content;
    if(const auto* value = std::get_if<ConversationSnapshotObserved>(payload)) return &value->content;
    if(const auto* value = std::get_if<ConversationEdited>(payload)) return &value->replacement;
    return nullptr;
}

bool same_evidence(const ConversationImportEvidence& a, const ConversationImportEvidence& b) {
    return a.artifact == b.artifact && a.record_locator == b.record_locator;
}

bool same_target(const ConversationLinkTarget& a, const ConversationLinkTarget& b) {
    if(const auto* ref = std::get_if<EntryRef>(&a)) {
        const auto* other = std::get_if<EntryRef>(&b);
        return other && *ref == *other;
    }
    const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&a);
    const auto* other = std::get_if<UnresolvedConversationTarget>(&b);
    return unresolved && other && unresolved->external == other->external &&
        same_evidence(unresolved->evidence, other->evidence);
}

/// Compare immutable occurrence/evidence bindings directly, without synthetic re-encoding
/// that could exceed an otherwise valid caller profile at an exact frame-size boundary.
bool same_immutable_binding(const ConversationEnvelope& a, const ConversationEnvelope& b) {
    if(a.metadata.ref.conversation != b.metadata.ref.conversation || a.metadata.ref.entry != b.metadata.ref.entry ||
        a.metadata.scope.value() != b.metadata.scope.value() || family(a) != family(b) ||
        !a.metadata.source || !b.metadata.source) return false;
    const auto& source = *a.metadata.source;
    const auto& other = *b.metadata.source;
    if(source.identity != other.identity || source.object != other.object || source.event != other.event ||
        source.revision != other.revision || source.provider_revision_marker != other.provider_revision_marker ||
        !same_evidence(source.evidence, other.evidence)) return false;
    if(a.metadata.links.size() != b.metadata.links.size()) return false;
    for(std::size_t i = 0; i < a.metadata.links.size(); ++i) {
        if(a.metadata.links[i].kind != b.metadata.links[i].kind ||
            !same_target(a.metadata.links[i].target, b.metadata.links[i].target)) return false;
    }
    const auto* target = affected(a);
    if(target && *target != *affected(b)) return false;
    if(family(a) == 4) {
        return std::get<ConversationRestored>(std::get<ConversationPayload>(a.payload)).retained_state ==
            std::get<ConversationRestored>(std::get<ConversationPayload>(b.payload)).retained_state;
    }
    if(const auto* call = std::get_if<ConversationToolCallObserved>(&a.payload)) {
        const auto& old = std::get<ConversationToolCallObserved>(b.payload);
        return call->call_id == old.call_id && call->tool_name == old.tool_name && call->request == old.request;
    }
    if(const auto* result = std::get_if<ConversationToolResultObserved>(&a.payload)) {
        const auto& old = std::get<ConversationToolResultObserved>(b.payload);
        return result->call_id == old.call_id && same_target(result->call, old.call);
    }
    return true;
}

ConversationReadResult read_refusal(ConversationReadStatus status) {
    ConversationReadResult result;
    result.status = status;
    return result;
}

ConversationWriteResult write_refusal(ConversationWriteStatus status) {
    return {status, std::nullopt, 0};
}
} // namespace

namespace {
struct ReferenceContinuationState final : ConversationContinuation::BackendState {
    std::shared_ptr<const int> instance;
    ConversationRef conversation;
    ScopeId scope;
    std::string policy_binding;
    std::uint64_t generation = 0;
    std::uint64_t seek_entry = 0;
    std::uint64_t seek_revision = 0;
    bool is_current = false;
};
} // namespace

ConversationContinuation::BackendState::~BackendState() = default;

ConversationContinuation IConversationStore::make_continuation(
    std::shared_ptr<const ConversationContinuation::BackendState> state) {
    ConversationContinuation result;
    result.m_state = std::move(state);
    return result;
}

const ConversationContinuation::BackendState* IConversationStore::continuation_state(
    const ConversationContinuation& continuation) noexcept {
    return continuation.m_state.get();
}

struct InMemoryConversationStore::Impl final {
    struct Record final {
        ByteBuffer bytes;
        EntryRef ref;
        std::uint64_t generation = 0;
        std::optional<ConversationCorrectionReceipt> correction;
        std::optional<EntryRef> content_ref;
        std::size_t charge() const { return bytes.size() + (correction ? correction->reason_code.size() : 0); }
    };
    struct Entry final {
        std::vector<Record> revisions;
        std::optional<std::uint64_t> root;
    };
    struct Conversation final {
        ScopeId scope;
        std::map<std::uint64_t, Entry> entries;
    };
    explicit Impl(ConversationStoreOptions options) : options(std::move(options)) {}
    ConversationStoreOptions options;
    mutable std::mutex mutex;
    const std::shared_ptr<const int> instance = std::make_shared<const int>(0);
    std::map<ConversationRef, Conversation> conversations;
    std::uint64_t generation = 0;
    std::size_t entry_count = 0;
    std::size_t storage_bytes = 0;

    ConversationEnvelope decode(const Record& record) const {
        return deserialize_conversation_envelope({record.bytes.data(), record.bytes.size()},
            {options.codec_limits, true});
    }

    const Record* find_record(const EntryRef& ref, const ScopeId& scope) const {
        const auto conversation = conversations.find(ref.conversation);
        if(conversation == conversations.end() || conversation->second.scope.value() != scope.value()) return nullptr;
        const auto entry = conversation->second.entries.find(ref.entry.value());
        if(entry == conversation->second.entries.end() || ref.revision.empty() ||
            ref.revision.value() > entry->second.revisions.size()) return nullptr;
        return &entry->second.revisions[static_cast<std::size_t>(ref.revision.value() - 1)];
    }

    const Entry* find_entry(const EntryRef& ref, const ScopeId& scope) const {
        if(!find_record(ref, scope)) return nullptr;
        return &conversations.find(ref.conversation)->second.entries.find(ref.entry.value())->second;
    }

    bool targets_valid(const ConversationEnvelope& envelope) const {
        for(const auto& link : envelope.metadata.links) {
            if(const auto* target = std::get_if<EntryRef>(&link.target)) {
                if(!find_record(*target, envelope.metadata.scope)) return false;
            }
        }
        if(const auto* result = std::get_if<ConversationToolResultObserved>(&envelope.payload)) {
            if(const auto* target = std::get_if<EntryRef>(&result->call)) {
                const auto* record = find_record(*target, envelope.metadata.scope);
                if(!record) return false;
                const auto call_envelope = decode(*record);
                const auto* call = std::get_if<ConversationToolCallObserved>(&call_envelope.payload);
                if(!call || call->call_id != result->call_id) return false;
            }
        }
        return true;
    }

    bool bind_message(const ConversationEnvelope& envelope, Record& record,
        std::optional<std::uint64_t>& root) const {
        const auto type = family(envelope);
        if(type == 0 || type == 1) {
            root = envelope.metadata.ref.entry.value();
            record.content_ref = envelope.metadata.ref;
        } else if(type >= 2 && type <= 4) {
            const auto* target = affected(envelope);
            const auto* prior = find_entry(*target, envelope.metadata.scope);
            if(!prior || !prior->root) return false;
            root = prior->root;
            if(type == 2) record.content_ref = envelope.metadata.ref;
            if(type == 4) {
                const auto& restore = std::get<ConversationRestored>(std::get<ConversationPayload>(envelope.payload));
                const auto* retained_entry = find_entry(restore.retained_state, envelope.metadata.scope);
                const auto* retained = find_record(restore.retained_state, envelope.metadata.scope);
                if(!retained_entry || retained_entry->root != root || !retained->content_ref) return false;
                record.content_ref = retained->content_ref;
            }
        }
        return true;
    }

    ConversationWriteStatus encode(const ConversationEnvelope& envelope, ByteBuffer& bytes) const {
        try {
            bytes = serialize_conversation_envelope(envelope, options.codec_limits);
            if(family(envelope) < 0 || std::any_of(envelope.extensions.begin(), envelope.extensions.end(),
                [](const auto& value) { return value.is_critical; })) return ConversationWriteStatus::Unsupported;
            return ConversationWriteStatus::Applied;
        } catch(const ConversationCodecError& error) {
            return error.code() == ConversationCodecCode::ResourceLimit ?
                ConversationWriteStatus::ResourceLimit : ConversationWriteStatus::InvalidRequest;
        }
    }

    bool has_capacity(std::size_t charge) const {
        return generation != std::numeric_limits<std::uint64_t>::max() &&
            charge <= options.max_storage_bytes - storage_bytes;
    }

    ConversationContinuation cursor(const ConversationStoreAccess& access, const ConversationRef& ref,
        bool is_current, std::uint64_t frontier, std::uint64_t entry, std::uint64_t revision) const {
        auto state = std::make_shared<ReferenceContinuationState>();
        state->instance = instance;
        state->conversation = ref;
        state->scope = access.scope;
        state->policy_binding = access.policy_binding;
        state->generation = frontier;
        state->seek_entry = entry;
        state->seek_revision = revision;
        state->is_current = is_current;
        return make_continuation(std::move(state));
    }

    bool resume(const ConversationStoreAccess& access, const ConversationRef& ref, bool is_current,
        const std::optional<ConversationContinuation>& token, std::uint64_t& frontier,
        std::uint64_t& entry, std::uint64_t& revision) const {
        frontier = generation;
        entry = revision = 0;
        if(!token) return true;
        const auto* state = dynamic_cast<const ReferenceContinuationState*>(continuation_state(*token));
        if(!state || state->instance != instance || state->conversation != ref ||
            state->scope.value() != access.scope.value() || state->policy_binding != access.policy_binding ||
            state->is_current != is_current || state->generation > generation) return false;
        frontier = state->generation;
        entry = state->seek_entry;
        revision = state->seek_revision;
        return true;
    }

    bool consume(const Record& record, const ConversationReadLimits& limits, ConversationReadResult& result) const {
        if(result.visited_records >= limits.max_visited_records ||
            record.bytes.size() > limits.max_decoded_bytes - result.decoded_bytes) return false;
        ++result.visited_records;
        result.decoded_bytes += record.bytes.size();
        return true;
    }
};

IConversationStore::~IConversationStore() = default;

InMemoryConversationStore::InMemoryConversationStore(ConversationStoreOptions options) {
    if(!is_valid_conversation_namespace(options.allocation_namespace) || !options.max_conversations ||
        options.max_conversations > kItemCap || !options.max_entries || options.max_entries > kItemCap ||
        !options.max_revisions_per_entry || options.max_revisions_per_entry > 1024 ||
        !options.max_storage_bytes || options.max_storage_bytes > kByteCap) {
        throw std::invalid_argument("Invalid conversation store options");
    }
    (void)conversation_codec_limits_cover(options.codec_limits, options.codec_limits);
    m_impl = std::make_unique<Impl>(std::move(options));
}

InMemoryConversationStore::~InMemoryConversationStore() = default;

ConversationWriteResult InMemoryConversationStore::create_conversation(
    const ConversationStoreAccess& access, const ConversationRef& ref) {
    if(!valid_access(access)) return write_refusal(ConversationWriteStatus::Denied);
    if(!is_valid_conversation_ref(ref) || ref.allocation_namespace != m_impl->options.allocation_namespace)
        return write_refusal(ConversationWriteStatus::InvalidRequest);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto existing = m_impl->conversations.find(ref);
    if(existing != m_impl->conversations.end()) {
        if(existing->second.scope.value() != access.scope.value()) return write_refusal(ConversationWriteStatus::Denied);
        return {ConversationWriteStatus::NoChange, std::nullopt, m_impl->generation};
    }
    const auto scope_bytes = access.scope.value().size();
    if(m_impl->conversations.size() >= m_impl->options.max_conversations ||
        scope_bytes > m_impl->options.codec_limits.max_frame_bytes ||
        scope_bytes > m_impl->options.codec_limits.max_owned_bytes || !m_impl->has_capacity(scope_bytes))
        return write_refusal(ConversationWriteStatus::ResourceLimit);
    m_impl->conversations.emplace(ref, Impl::Conversation{access.scope, {}});
    m_impl->storage_bytes += scope_bytes;
    ++m_impl->generation;
    return {ConversationWriteStatus::Applied, std::nullopt, m_impl->generation};
}

ConversationWriteResult InMemoryConversationStore::append(
    const ConversationStoreAccess& access, const ConversationEnvelope& envelope) {
    if(!valid_access(access) || envelope.metadata.scope.value() != access.scope.value())
        return write_refusal(ConversationWriteStatus::Denied);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto conversation = m_impl->conversations.find(envelope.metadata.ref.conversation);
    if(conversation == m_impl->conversations.end() || conversation->second.scope.value() != access.scope.value())
        return write_refusal(ConversationWriteStatus::NotFound);
    ByteBuffer bytes;
    const auto encoded = m_impl->encode(envelope, bytes);
    if(encoded != ConversationWriteStatus::Applied) return write_refusal(encoded);
    const auto& ref = envelope.metadata.ref;
    if(ref.revision.value() != 1) return write_refusal(ConversationWriteStatus::InvalidRequest);
    const auto existing = conversation->second.entries.find(ref.entry.value());
    if(existing != conversation->second.entries.end()) {
        if(bytes != existing->second.revisions.front().bytes) return write_refusal(ConversationWriteStatus::Conflict);
        return {ConversationWriteStatus::NoChange, ref, m_impl->generation};
    }
    if(ref.entry.value() != conversation->second.entries.size() + 1)
        return write_refusal(ConversationWriteStatus::Conflict);
    if(m_impl->entry_count >= m_impl->options.max_entries || !m_impl->has_capacity(bytes.size()))
        return write_refusal(ConversationWriteStatus::ResourceLimit);
    if(!m_impl->targets_valid(envelope)) return write_refusal(ConversationWriteStatus::InvalidRequest);
    Impl::Record record{std::move(bytes), ref, m_impl->generation + 1, std::nullopt, std::nullopt};
    Impl::Entry entry;
    if(!m_impl->bind_message(envelope, record, entry.root)) return write_refusal(ConversationWriteStatus::InvalidRequest);
    const auto charge = record.charge();
    entry.revisions.push_back(std::move(record));
    ConversationWriteResult result{ConversationWriteStatus::Applied, ref, m_impl->generation + 1};
    conversation->second.entries.emplace(ref.entry.value(), std::move(entry));
    m_impl->storage_bytes += charge;
    ++m_impl->entry_count;
    ++m_impl->generation;
    return result;
}

ConversationWriteResult InMemoryConversationStore::correct(const ConversationStoreAccess& access,
    const ConversationEnvelope& envelope, EventRevision expected, const std::string& reason_code) {
    if(!valid_access(access) || envelope.metadata.scope.value() != access.scope.value())
        return write_refusal(ConversationWriteStatus::Denied);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto& ref = envelope.metadata.ref;
    const auto conversation = m_impl->conversations.find(ref.conversation);
    if(conversation == m_impl->conversations.end() || conversation->second.scope.value() != access.scope.value())
        return write_refusal(ConversationWriteStatus::NotFound);
    const auto found = conversation->second.entries.find(ref.entry.value());
    if(found == conversation->second.entries.end()) return write_refusal(ConversationWriteStatus::NotFound);
    auto& entry = found->second;
    if(entry.revisions.back().ref.revision != expected) return write_refusal(ConversationWriteStatus::Conflict);
    const auto* previous = &entry.revisions.back();
    if(expected.value() == std::numeric_limits<std::uint64_t>::max() || ref.revision.value() != expected.value() + 1 ||
        !is_valid_conversation_namespace(reason_code)) return write_refusal(ConversationWriteStatus::InvalidRequest);
    ByteBuffer bytes;
    const auto encoded = m_impl->encode(envelope, bytes);
    if(encoded != ConversationWriteStatus::Applied) return write_refusal(encoded);
    auto normalized = copy_conversation_envelope(envelope);
    normalized.metadata.ref.revision = expected;
    if(serialize_conversation_envelope(normalized, m_impl->options.codec_limits) == previous->bytes)
        return {ConversationWriteStatus::NoChange, previous->ref, m_impl->generation};
    const auto old_envelope = m_impl->decode(*previous);
    if(!envelope.metadata.source || !old_envelope.metadata.source ||
        !same_immutable_binding(envelope, old_envelope) ||
        !m_impl->targets_valid(envelope)) return write_refusal(ConversationWriteStatus::InvalidRequest);
    if(entry.revisions.size() >= m_impl->options.max_revisions_per_entry ||
        !m_impl->has_capacity(bytes.size() + reason_code.size())) return write_refusal(ConversationWriteStatus::ResourceLimit);
    Impl::Record record{std::move(bytes), ref, m_impl->generation + 1,
        ConversationCorrectionReceipt{expected, reason_code}, std::nullopt};
    auto root = entry.root;
    if(!m_impl->bind_message(envelope, record, root) || root != entry.root)
        return write_refusal(ConversationWriteStatus::InvalidRequest);
    const auto charge = record.charge();
    ConversationWriteResult result{ConversationWriteStatus::Applied, ref, m_impl->generation + 1};
    entry.revisions.push_back(std::move(record));
    m_impl->storage_bytes += charge;
    ++m_impl->generation;
    return result;
}

ConversationReadResult InMemoryConversationStore::read_exact(const ConversationStoreAccess& access,
    const EntryRef& ref, const ConversationReadLimits& limits) const {
    if(!valid_access(access)) return read_refusal(ConversationReadStatus::Denied);
    if(!is_valid_entry_ref(ref) || !valid_limits(limits)) return read_refusal(ConversationReadStatus::InvalidRequest);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto* record = m_impl->find_record(ref, access.scope);
    if(!record) return read_refusal(ConversationReadStatus::NotFound);
    ConversationReadResult result;
    result.commit_generation = m_impl->generation;
    if(!limits.max_items) { result.status = ConversationReadStatus::BudgetExhausted; return result; }
    if(record->charge() > limits.max_output_bytes) { result.status = ConversationReadStatus::OversizedItem; return result; }
    if(!m_impl->consume(*record, limits, result)) { result.status = ConversationReadStatus::BudgetExhausted; return result; }
    result.records.push_back({m_impl->decode(*record), record->correction, record->generation});
    result.output_bytes = record->charge();
    result.status = ConversationReadStatus::Complete;
    return result;
}

ConversationReadResult InMemoryConversationStore::read_history(const ConversationStoreAccess& access,
    const ConversationRef& ref, const ConversationReadLimits& limits,
    const std::optional<ConversationContinuation>& token) const {
    if(!valid_access(access)) return read_refusal(ConversationReadStatus::Denied);
    if(!valid_limits(limits) || !is_valid_conversation_ref(ref)) return read_refusal(ConversationReadStatus::InvalidRequest);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    std::uint64_t frontier, seek_entry, seek_revision;
    if(!m_impl->resume(access, ref, false, token, frontier, seek_entry, seek_revision))
        return read_refusal(ConversationReadStatus::InvalidContinuation);
    const auto conversation = m_impl->conversations.find(ref);
    if(conversation == m_impl->conversations.end() || conversation->second.scope.value() != access.scope.value())
        return read_refusal(ConversationReadStatus::NotFound);
    ConversationReadResult result;
    result.commit_generation = frontier;
    for(auto entry = conversation->second.entries.lower_bound(seek_entry); entry != conversation->second.entries.end(); ++entry) {
        if(entry->second.revisions.front().generation > frontier) break;
        const auto& revisions = entry->second.revisions;
        const auto start = entry->first == seek_entry ? static_cast<std::size_t>(seek_revision) : 0;
        for(auto index = start; index < revisions.size(); ++index) {
            const auto& record = revisions[index];
            if(record.generation > frontier) break;
            auto stop = ConversationReadStatus::Complete;
            if(result.records.size() >= limits.max_items) stop = result.records.empty() ?
                ConversationReadStatus::BudgetExhausted : ConversationReadStatus::Partial;
            else if(record.charge() > limits.max_output_bytes - result.output_bytes) stop = result.records.empty() ?
                ConversationReadStatus::OversizedItem : ConversationReadStatus::Partial;
            else if(!m_impl->consume(record, limits, result)) stop = ConversationReadStatus::BudgetExhausted;
            if(stop != ConversationReadStatus::Complete) {
                result.status = stop;
                result.continuation = m_impl->cursor(access, ref, false, frontier, seek_entry, seek_revision);
                return result;
            }
            result.records.push_back({m_impl->decode(record), record.correction, record.generation});
            result.output_bytes += record.charge();
            seek_entry = entry->first;
            seek_revision = record.ref.revision.value();
        }
    }
    result.status = ConversationReadStatus::Complete;
    return result;
}

ConversationReadResult InMemoryConversationStore::read_current(const ConversationStoreAccess& access,
    const ConversationRef& ref, const ConversationReadLimits& limits,
    const std::optional<ConversationContinuation>& token) const {
    if(!valid_access(access)) return read_refusal(ConversationReadStatus::Denied);
    if(!valid_limits(limits) || !is_valid_conversation_ref(ref)) return read_refusal(ConversationReadStatus::InvalidRequest);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    std::uint64_t frontier, seek_entry, seek_revision;
    if(!m_impl->resume(access, ref, true, token, frontier, seek_entry, seek_revision))
        return read_refusal(ConversationReadStatus::InvalidContinuation);
    const auto conversation = m_impl->conversations.find(ref);
    if(conversation == m_impl->conversations.end() || conversation->second.scope.value() != access.scope.value())
        return read_refusal(ConversationReadStatus::NotFound);
    ConversationReadResult result;
    result.commit_generation = frontier;
    struct Fold final { ConversationMessageView view; std::size_t root_bytes = 0, state_bytes = 0, content_bytes = 0; };
    std::map<std::uint64_t, Fold> folds;
    for(const auto& pair : conversation->second.entries) {
        const auto& entry = pair.second;
        if(entry.revisions.front().generation > frontier) break;
        const auto end = std::upper_bound(entry.revisions.begin(), entry.revisions.end(), frontier,
            [](std::uint64_t value, const Impl::Record& record) { return value < record.generation; });
        const auto& record = *std::prev(end);
        if(!m_impl->consume(record, limits, result)) {
            result.status = ConversationReadStatus::BudgetExhausted;
            result.continuation = m_impl->cursor(access, ref, true, frontier, seek_entry, 0);
            return result;
        }
        auto envelope = m_impl->decode(record);
        if(!entry.root) continue;
        const auto type = family(envelope);
        if(type == 0 || type == 1) {
            Fold fold;
            fold.view.origin = envelope.metadata.ref;
            fold.view.state = envelope.metadata.ref;
            fold.view.origin_metadata = std::move(envelope.metadata);
            fold.view.content = *content(envelope);
            fold.view.content_ref = record.content_ref;
            fold.view.is_snapshot_origin = type == 1;
            fold.root_bytes = fold.state_bytes = fold.content_bytes = record.bytes.size();
            folds.emplace(pair.first, std::move(fold));
            continue;
        }
        auto& fold = folds.at(*entry.root);
        if(fold.view.status == ConversationMessageState::Ambiguous) continue;
        if(affected(envelope)->entry != fold.view.state.entry ||
            (type == 2 && fold.view.status == ConversationMessageState::Deleted)) {
            fold.view.status = ConversationMessageState::Ambiguous;
            fold.view.state = {};
            fold.view.content.reset();
            fold.view.content_ref.reset();
            fold.content_bytes = 0;
            continue;
        }
        fold.view.state = envelope.metadata.ref;
        fold.state_bytes = record.bytes.size();
        fold.view.content.reset();
        fold.view.content_ref.reset();
        fold.content_bytes = 0;
        if(type == 3) { fold.view.status = ConversationMessageState::Deleted; continue; }
        fold.view.status = ConversationMessageState::Live;
        fold.view.content_ref = record.content_ref;
        if(type == 2) {
            fold.view.content = *content(envelope);
            fold.content_bytes = record.bytes.size();
        } else {
            const auto* retained = m_impl->find_record(*record.content_ref, access.scope);
            if(!m_impl->consume(*retained, limits, result)) {
                result.status = ConversationReadStatus::BudgetExhausted;
                result.continuation = m_impl->cursor(access, ref, true, frontier, seek_entry, 0);
                return result;
            }
            const auto retained_envelope = m_impl->decode(*retained);
            fold.view.content = *content(retained_envelope);
            fold.content_bytes = retained->bytes.size();
        }
    }
    for(auto& pair : folds) {
        if(pair.first <= seek_entry) continue;
        auto& fold = pair.second;
        const auto charge = fold.root_bytes + fold.state_bytes + fold.content_bytes;
        if(result.messages.size() >= limits.max_items || charge > limits.max_output_bytes - result.output_bytes) {
            result.status = result.messages.empty() ? (limits.max_items == 0 ? ConversationReadStatus::BudgetExhausted :
                ConversationReadStatus::OversizedItem) : ConversationReadStatus::Partial;
            result.continuation = m_impl->cursor(access, ref, true, frontier, seek_entry, 0);
            return result;
        }
        result.messages.push_back(std::move(fold.view));
        result.output_bytes += charge;
        seek_entry = pair.first;
    }
    result.status = ConversationReadStatus::Complete;
    return result;
}

} // namespace agent_memory
