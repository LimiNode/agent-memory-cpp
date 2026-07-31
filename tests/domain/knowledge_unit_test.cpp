#include <agent_memory/domain/KnowledgeUnit.hpp>

#include <array>
#include <iostream>
#include <limits>
#include <string_view>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::SourceRefSummary make_source_summary() {
        agent_memory::SourceRefSummary summary;
        summary.resource_id = agent_memory::ResourceId{"resource:guide"};
        summary.resource_revision = agent_memory::ResourceRevisionRef{
            summary.resource_id,
            7,
            agent_memory::ResourceBodyDigest{}
        };
        summary.uri = "memory://guide#intro";
        summary.excerpt = agent_memory::TextRange{12, 24};
        summary.quote_hash[0] = 7;
        summary.confidence = 0.9;
        summary.preview = "A stable cited preview.";
        return summary;
    }

    agent_memory::KnowledgeUnitEnvelope make_envelope() {
        agent_memory::KnowledgeUnitEnvelope envelope;
        envelope.id = agent_memory::KnowledgeUnitId{42};
        envelope.kind = agent_memory::KnowledgeUnitKind::Note;
        envelope.scope_id = agent_memory::ScopeId{"project:alpha"};
        envelope.primary_text = "A compact retrieval seed.";
        envelope.display_text = "A compact retrieval seed.";
        envelope.sources.push_back(make_source_summary());
        envelope.created_at_ms = 100;
        envelope.updated_at_ms = 110;
        envelope.observed_at_ms = 90;
        envelope.revision = 1;
        envelope.content_hash.bytes[0] = 1;
        envelope.content_hash_recipe_version = 1;
        envelope.priority_weight = 0.5;
        return envelope;
    }

} // namespace

int main() {
    using agent_memory::ContentHash;
    using agent_memory::KnowledgeUnitId;
    using agent_memory::KnowledgeUnitKey;
    using agent_memory::KnowledgeUnitKind;
    using agent_memory::KnowledgeUnitLifecycleState;
    using agent_memory::ScopeId;
    using agent_memory::SourceReferenceMode;

    const auto global_scope = ScopeId::global();
    if(global_scope.value() != "global" || global_scope.empty()) {
        return fail("global scope must be an explicit non-empty identifier");
    }

    if(KnowledgeUnitId{7}.empty() || !(KnowledgeUnitId{7} < KnowledgeUnitId{8})) {
        return fail("knowledge unit ids must preserve non-zero numeric ordering");
    }

    KnowledgeUnitKind parsed_kind = KnowledgeUnitKind::Chunk;
    if(
        !agent_memory::parse_knowledge_unit_kind("Compiled_Article", parsed_kind)
        || parsed_kind != KnowledgeUnitKind::CompiledArticle
        || agent_memory::to_string(parsed_kind) != "compiled_article"
        || agent_memory::parse_knowledge_unit_kind("unknown", parsed_kind)
    ) {
        return fail("knowledge unit kind names must be stable and parse case-insensitively");
    }

    if(!agent_memory::to_string(static_cast<KnowledgeUnitKind>(99)).empty()) {
        return fail("unknown knowledge unit kinds must not stringify as custom");
    }

    ContentHash first_hash;
    first_hash.bytes[0] = 1;
    ContentHash second_hash;
    second_hash.bytes[0] = 2;
    const KnowledgeUnitKey first_key{
        KnowledgeUnitKind::Note,
        ScopeId{"project:alpha"},
        first_hash
    };
    const KnowledgeUnitKey same_key{
        KnowledgeUnitKind::Note,
        ScopeId{"project:alpha"},
        first_hash
    };
    const KnowledgeUnitKey different_key{
        KnowledgeUnitKind::Note,
        ScopeId{"project:alpha"},
        second_hash
    };
    if(first_key != same_key || !(first_key < different_key)) {
        return fail("knowledge unit keys must compare every identity field");
    }

    if(
        !agent_memory::is_valid_knowledge_unit_lifecycle_transition(
            KnowledgeUnitLifecycleState::Active,
            KnowledgeUnitLifecycleState::Superseded
        )
        || !agent_memory::is_valid_knowledge_unit_lifecycle_transition(
            KnowledgeUnitLifecycleState::Deprecated,
            KnowledgeUnitLifecycleState::Erased
        )
        || agent_memory::is_valid_knowledge_unit_lifecycle_transition(
            KnowledgeUnitLifecycleState::Deprecated,
            KnowledgeUnitLifecycleState::Active
        )
        || agent_memory::is_valid_knowledge_unit_lifecycle_transition(
            KnowledgeUnitLifecycleState::Active,
            KnowledgeUnitLifecycleState::Active
        )
    ) {
        return fail("knowledge unit lifecycle transitions must be fail-closed");
    }

    const auto source = make_source_summary();
    if(!agent_memory::is_valid_source_ref_summary(source)) {
        return fail("complete source summary must be valid");
    }

    auto missing_revision = source;
    missing_revision.resource_revision.reset();
    if(agent_memory::is_valid_source_ref_summary(missing_revision)) {
        return fail("revision-bound source summaries must require a revision");
    }

    auto legacy_preview = source;
    legacy_preview.reference_mode = SourceReferenceMode::LegacyPreviewOnly;
    legacy_preview.resource_revision.reset();
    legacy_preview.excerpt = agent_memory::TextRange{};
    legacy_preview.quote_hash = {};
    if(!agent_memory::is_valid_source_ref_summary(legacy_preview)) {
        return fail("explicit legacy preview-only source summaries must be accepted");
    }

    auto legacy_preview_with_offset = legacy_preview;
    legacy_preview_with_offset.excerpt.offset = 12345;
    if(agent_memory::is_valid_source_ref_summary(legacy_preview_with_offset)) {
        return fail("legacy preview-only source summaries must not carry a location");
    }

    auto mismatched_revision = source;
    mismatched_revision.resource_revision->resource_id = agent_memory::ResourceId{"resource:other"};
    if(agent_memory::is_valid_source_ref_summary(mismatched_revision)) {
        return fail("source revision must bind the same resource id");
    }

    auto non_finite_confidence = source;
    non_finite_confidence.confidence = std::numeric_limits<double>::infinity();
    if(agent_memory::is_valid_source_ref_summary(non_finite_confidence)) {
        return fail("source confidence must be finite");
    }

    auto too_large_preview = source;
    too_large_preview.preview.assign(257, 'x');
    if(agent_memory::is_valid_source_ref_summary(too_large_preview)) {
        return fail("source preview must remain within the M0 inline budget");
    }

    const auto envelope = make_envelope();
    if(!agent_memory::is_valid_knowledge_unit_envelope(envelope)) {
        return fail("complete M0 knowledge unit envelope must be valid");
    }

    auto empty_scope = envelope;
    empty_scope.scope_id = ScopeId{};
    if(agent_memory::is_valid_knowledge_unit_envelope(empty_scope)) {
        return fail("knowledge unit envelope must require a scope");
    }

    auto invalid_priority = envelope;
    invalid_priority.priority_weight = 1.1;
    if(agent_memory::is_valid_knowledge_unit_envelope(invalid_priority)) {
        return fail("knowledge unit envelope priority must remain normalized");
    }

    auto too_many_sources = envelope;
    too_many_sources.sources.assign(4, source);
    if(agent_memory::is_valid_knowledge_unit_envelope(too_many_sources)) {
        return fail("knowledge unit envelope must enforce the M0 source budget");
    }

    auto self_supersedes = envelope;
    self_supersedes.supersedes.push_back(self_supersedes.id);
    if(agent_memory::is_valid_knowledge_unit_envelope(self_supersedes)) {
        return fail("knowledge unit envelope must reject self lineage");
    }

    auto directly_cyclic_lineage = envelope;
    directly_cyclic_lineage.supersedes.push_back(KnowledgeUnitId{41});
    directly_cyclic_lineage.superseded_by = KnowledgeUnitId{41};
    if(agent_memory::is_valid_knowledge_unit_envelope(directly_cyclic_lineage)) {
        return fail("knowledge unit envelope must reject directly cyclic lineage");
    }

    auto invalid_kind = envelope;
    invalid_kind.kind = static_cast<KnowledgeUnitKind>(99);
    if(agent_memory::is_valid_knowledge_unit_envelope(invalid_kind)) {
        return fail("knowledge unit envelope must reject unknown kind values");
    }

    return 0;
}
