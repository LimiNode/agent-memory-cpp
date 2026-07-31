#include "KnowledgeUnit.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <utility>

namespace agent_memory {

    namespace {

        constexpr std::size_t MAX_SOURCE_SUMMARIES = 3;
        constexpr std::size_t MAX_SOURCE_PREVIEW_BYTES = 256;
        constexpr std::size_t MAX_PRIMARY_TEXT_BYTES = 1024;

        struct KnowledgeUnitKindName final {
            KnowledgeUnitKind kind = KnowledgeUnitKind::Chunk;
            std::string_view name;
        };

        constexpr std::array<KnowledgeUnitKindName, 17> KNOWLEDGE_UNIT_KIND_NAMES{{
            {KnowledgeUnitKind::Chunk, "chunk"},
            {KnowledgeUnitKind::QAPair, "qa_pair"},
            {KnowledgeUnitKind::Fact, "fact"},
            {KnowledgeUnitKind::Event, "event"},
            {KnowledgeUnitKind::Entity, "entity"},
            {KnowledgeUnitKind::Relation, "relation"},
            {KnowledgeUnitKind::Summary, "summary"},
            {KnowledgeUnitKind::CompiledArticle, "compiled_article"},
            {KnowledgeUnitKind::ConversationEpisode, "conversation_episode"},
            {KnowledgeUnitKind::Note, "note"},
            {KnowledgeUnitKind::Task, "task"},
            {KnowledgeUnitKind::Decision, "decision"},
            {KnowledgeUnitKind::Custom, "custom"},
            {KnowledgeUnitKind::Playbook, "playbook"},
            {KnowledgeUnitKind::DomainMap, "domain_map"},
            {KnowledgeUnitKind::CapabilityMap, "capability_map"},
            {KnowledgeUnitKind::Procedure, "procedure"}
        }};

        std::string lowercase_ascii(std::string_view text) {
            std::string result;
            result.reserve(text.size());
            for(const unsigned char character : text) {
                result.push_back(static_cast<char>(std::tolower(character)));
            }
            return result;
        }

        bool contains_duplicate_or_self_reference(
            const std::vector<KnowledgeUnitId>& ids,
            const KnowledgeUnitId self
        ) noexcept {
            for(std::size_t left = 0; left < ids.size(); ++left) {
                if(ids[left].empty() || ids[left] == self) {
                    return true;
                }

                for(std::size_t right = left + 1; right < ids.size(); ++right) {
                    if(ids[left] == ids[right]) {
                        return true;
                    }
                }
            }
            return false;
        }

        bool contains_reference(
            const std::vector<KnowledgeUnitId>& ids,
            const KnowledgeUnitId id
        ) noexcept {
            return std::find(ids.begin(), ids.end(), id) != ids.end();
        }

    } // namespace

    ScopeId::ScopeId(std::string value)
        : m_value(std::move(value)) {}

    ScopeId ScopeId::global() {
        return ScopeId{"global"};
    }

    const std::string& ScopeId::value() const noexcept {
        return m_value;
    }

    bool ScopeId::empty() const noexcept {
        return m_value.empty();
    }

    std::string_view to_string(KnowledgeUnitKind kind) noexcept {
        for(const auto& item : KNOWLEDGE_UNIT_KIND_NAMES) {
            if(item.kind == kind) {
                return item.name;
            }
        }
        return {};
    }

    bool parse_knowledge_unit_kind(std::string_view text, KnowledgeUnitKind& kind) {
        const auto normalized = lowercase_ascii(text);
        for(const auto& item : KNOWLEDGE_UNIT_KIND_NAMES) {
            if(normalized == item.name) {
                kind = item.kind;
                return true;
            }
        }
        return false;
    }

    bool is_valid_knowledge_unit_kind(KnowledgeUnitKind kind) noexcept {
        return std::any_of(
            KNOWLEDGE_UNIT_KIND_NAMES.begin(),
            KNOWLEDGE_UNIT_KIND_NAMES.end(),
            [kind](const KnowledgeUnitKindName& item) { return item.kind == kind; }
        );
    }

    bool is_valid_knowledge_unit_lifecycle_state(
        KnowledgeUnitLifecycleState state
    ) noexcept {
        switch(state) {
        case KnowledgeUnitLifecycleState::Active:
        case KnowledgeUnitLifecycleState::Superseded:
        case KnowledgeUnitLifecycleState::Deprecated:
        case KnowledgeUnitLifecycleState::Erased:
            return true;
        }
        return false;
    }

    bool is_valid_knowledge_unit_lifecycle_transition(
        KnowledgeUnitLifecycleState from,
        KnowledgeUnitLifecycleState to
    ) noexcept {
        switch(from) {
        case KnowledgeUnitLifecycleState::Active:
            return to == KnowledgeUnitLifecycleState::Superseded
                || to == KnowledgeUnitLifecycleState::Deprecated
                || to == KnowledgeUnitLifecycleState::Erased;
        case KnowledgeUnitLifecycleState::Superseded:
            return to == KnowledgeUnitLifecycleState::Deprecated
                || to == KnowledgeUnitLifecycleState::Erased;
        case KnowledgeUnitLifecycleState::Deprecated:
            return to == KnowledgeUnitLifecycleState::Erased;
        case KnowledgeUnitLifecycleState::Erased:
            return false;
        }
        return false;
    }

    bool is_valid_source_ref_summary(const SourceRefSummary& summary) noexcept {
        if(
            summary.resource_id.empty()
            || summary.uri.empty()
            || summary.preview.empty()
            || summary.preview.size() > MAX_SOURCE_PREVIEW_BYTES
            || !std::isfinite(summary.confidence)
            || summary.confidence < 0.0
            || summary.confidence > 1.0
        ) {
            return false;
        }

        switch(summary.text_origin) {
        case SourceTextOrigin::OriginalText:
        case SourceTextOrigin::DerivedExtraction:
            break;
        default:
            return false;
        }

        switch(summary.reference_mode) {
        case SourceReferenceMode::RevisionBoundQuote:
            if(
                !summary.resource_revision
                || summary.excerpt.length == 0
                || summary.resource_revision->resource_id.empty()
                || summary.resource_revision->resource_id != summary.resource_id
                || !is_valid_resource_body_digest(summary.resource_revision->body_digest)
            ) {
                return false;
            }
            break;
        case SourceReferenceMode::LegacyPreviewOnly:
            if(
                summary.resource_revision
                || summary.excerpt.offset != 0
                || summary.excerpt.length != 0
                || std::any_of(
                    summary.quote_hash.begin(),
                    summary.quote_hash.end(),
                    [](const std::uint8_t byte) { return byte != 0; }
                )
            ) {
                return false;
            }
            break;
        default:
            return false;
        }

        return true;
    }

    bool is_valid_knowledge_unit_envelope(
        const KnowledgeUnitEnvelope& envelope
    ) noexcept {
        if(
            envelope.id.empty()
            || !is_valid_knowledge_unit_kind(envelope.kind)
            || envelope.scope_id.empty()
            || envelope.primary_text.empty()
            || envelope.primary_text.size() > MAX_PRIMARY_TEXT_BYTES
            || !is_valid_knowledge_unit_lifecycle_state(envelope.lifecycle_state)
            || envelope.sources.size() > MAX_SOURCE_SUMMARIES
            || !std::isfinite(envelope.priority_weight)
            || envelope.priority_weight < 0.0
            || envelope.priority_weight > 1.0
            || contains_duplicate_or_self_reference(envelope.supersedes, envelope.id)
        ) {
            return false;
        }

        if(
            (envelope.superseded_by && (
                envelope.superseded_by->empty()
                || *envelope.superseded_by == envelope.id
                || contains_reference(envelope.supersedes, *envelope.superseded_by)
            ))
            || (envelope.derived_from && (
                envelope.derived_from->empty() || *envelope.derived_from == envelope.id
            ))
        ) {
            return false;
        }

        for(const auto& source : envelope.sources) {
            if(!is_valid_source_ref_summary(source)) {
                return false;
            }
        }

        return true;
    }

    bool operator==(const ScopeId& lhs, const ScopeId& rhs) noexcept {
        return lhs.value() == rhs.value();
    }

    bool operator!=(const ScopeId& lhs, const ScopeId& rhs) noexcept {
        return !(lhs == rhs);
    }

    bool operator<(const ScopeId& lhs, const ScopeId& rhs) noexcept {
        return lhs.value() < rhs.value();
    }

    bool operator==(const ContentHash& lhs, const ContentHash& rhs) noexcept {
        return lhs.bytes == rhs.bytes;
    }

    bool operator!=(const ContentHash& lhs, const ContentHash& rhs) noexcept {
        return !(lhs == rhs);
    }

    bool operator<(const ContentHash& lhs, const ContentHash& rhs) noexcept {
        return lhs.bytes < rhs.bytes;
    }

    bool operator==(const KnowledgeUnitKey& lhs, const KnowledgeUnitKey& rhs) noexcept {
        return lhs.kind == rhs.kind
            && lhs.scope_id == rhs.scope_id
            && lhs.content_hash == rhs.content_hash;
    }

    bool operator!=(const KnowledgeUnitKey& lhs, const KnowledgeUnitKey& rhs) noexcept {
        return !(lhs == rhs);
    }

    bool operator<(const KnowledgeUnitKey& lhs, const KnowledgeUnitKey& rhs) noexcept {
        if(lhs.kind != rhs.kind) {
            return lhs.kind < rhs.kind;
        }
        if(lhs.scope_id != rhs.scope_id) {
            return lhs.scope_id < rhs.scope_id;
        }
        return lhs.content_hash < rhs.content_hash;
    }

} // namespace agent_memory
