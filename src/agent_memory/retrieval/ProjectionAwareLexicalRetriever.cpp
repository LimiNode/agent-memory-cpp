#include "ProjectionAwareLexicalRetriever.hpp"

#include <agent_memory/domain/Document.hpp>
#include <agent_memory/lexical/ExactLexicalIndex.hpp>

#include <algorithm>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>

namespace agent_memory {
namespace {

std::string unit_key(const KnowledgeUnitId& id) {
    return "knowledge-unit:" + std::to_string(id.value());
}

bool has_publication_binding(
    const KnowledgeUnitEnvelope& envelope,
    IResourceManifestStorage& manifests
) {
    const auto expected_key = "original_projection:" +
        std::to_string(envelope.id.value());
    for(const auto& source : envelope.sources) {
        if(!source.resource_revision) continue;
        const auto manifest = manifests.find_manifest(source.resource_id);
        if(!manifest || !is_active_resource_manifest(*manifest)) continue;
        if(manifest->revision.resource_id != source.resource_id ||
           manifest->revision.generation != source.resource_revision->generation) {
            continue;
        }
        const auto& expected_digest = source.resource_revision->body_digest;
        const auto& actual_digest = manifest->revision.body_digest;
        if(!actual_digest ||
           expected_digest.algorithm != actual_digest->algorithm ||
           expected_digest.bytes != actual_digest->bytes) {
            continue;
        }
        for(const auto& record : manifest->records) {
            if(record.kind == DerivedRecordKind::Custom &&
               record.key == expected_key) {
                return true;
            }
        }
    }
    return false;
}

std::optional<SearchProjection> find_active_projection(
    const KnowledgeUnitEnvelope& envelope,
    ProjectionKind kind,
    IProjectionStore& projections,
    IResourceManifestStorage& manifests
) {
    if(envelope.lifecycle_state != KnowledgeUnitLifecycleState::Active ||
       !has_publication_binding(envelope, manifests)) {
        return std::nullopt;
    }

    std::optional<SearchProjection> result;
    for(auto projection : projections.list(envelope.scope_id, envelope.id, kind)) {
        if(projection.kind != kind ||
           projection.version.unit_revision_at_build != envelope.revision ||
           projection.version.projection_revision != envelope.revision ||
           !is_valid_search_projection(projection)) {
            continue;
        }
        if(result) {
            throw std::runtime_error(
                "multiple fresh projections are published for one knowledge unit"
            );
        }
        result = std::move(projection);
    }
    return result;
}

ResourceRevision lexical_revision(const KnowledgeUnitEnvelope& envelope) {
    if(!envelope.sources.empty() && envelope.sources.front().resource_revision) {
        const auto& source_revision = *envelope.sources.front().resource_revision;
        return ResourceRevision{
            source_revision.resource_id,
            source_revision.generation,
            0,
            0,
            source_revision.body_digest,
            SourceTextOrigin::OriginalText,
            std::nullopt
        };
    }
    return ResourceRevision{
        ResourceId{"knowledge-unit:" + std::to_string(envelope.id.value())},
        envelope.revision,
        0,
        0,
        std::nullopt,
        SourceTextOrigin::OriginalText,
        std::nullopt
    };
}

RetrievedChunk make_result(
    const KnowledgeUnitEnvelope& envelope,
    float score
) {
    const auto id = unit_key(envelope.id);
    DocumentChunk chunk;
    chunk.id = ChunkId{id};
    chunk.document_id = DocumentId{id};
    chunk.source_range = TextRange{0, envelope.primary_text.size()};
    chunk.text = envelope.primary_text;
    RetrievedChunk result;
    result.chunk = std::move(chunk);
    result.score = score;
    return result;
}

} // namespace

ProjectionAwareLexicalRetriever::ProjectionAwareLexicalRetriever(
    IKnowledgeUnitStore& unit_store,
    IProjectionStore& projection_store,
    IResourceManifestStorage& manifest_store,
    ScopeId scope_id,
    ITokenizer& tokenizer,
    ProjectionAwareLexicalRetrieverOptions options
) : m_unit_store(&unit_store),
    m_projection_store(&projection_store),
    m_manifest_store(&manifest_store),
    m_scope_id(std::move(scope_id)),
    m_tokenizer(&tokenizer),
    m_options(options) {
    if(m_scope_id.empty()) {
        throw std::invalid_argument("projection-aware lexical scope must not be empty");
    }
    if(!is_valid_projection_kind(m_options.projection_kind) ||
       m_options.projection_kind != ProjectionKind::Original) {
        throw std::invalid_argument("projection-aware lexical kind is invalid");
    }
    if(m_options.max_candidates == 0) {
        throw std::invalid_argument("projection-aware lexical max_candidates must be > 0");
    }
}

RetrievalResult ProjectionAwareLexicalRetriever::retrieve(
    const RetrievalQuery& query
) const {
    RetrievalResult result;
    if(query.limit == 0 || !query.has_text() || !query.metadata_filters.empty()) {
        return result;
    }

    const auto query_tokens = m_tokenizer->tokenize(query.text);
    if(query_tokens.empty()) return result;

    ExactLexicalIndex index;
    std::unordered_map<std::string, KnowledgeUnitEnvelope> envelopes;
    const auto ids = m_unit_store->scan_by_scope(m_scope_id);
    envelopes.reserve(ids.size());
    for(const auto& id : ids) {
        const auto envelope = m_unit_store->find(id);
        if(!envelope || envelope->scope_id.value() != m_scope_id.value()) continue;
        const auto projection = find_active_projection(
            *envelope,
            m_options.projection_kind,
            *m_projection_store,
            *m_manifest_store
        );
        if(!projection || projection->body.empty()) continue;
        const auto tokens = m_tokenizer->tokenize(projection->body);
        if(tokens.empty()) continue;
        LexicalDocumentRecord record;
        record.chunk_id = ChunkId{unit_key(envelope->id)};
        record.revision = lexical_revision(*envelope);
        record.tokens = tokens.tokens;
        index.upsert(std::move(record));
        envelopes.emplace(unit_key(envelope->id), *envelope);
    }

    LexicalSearchQuery lexical_query;
    lexical_query.limit = std::min(query.limit, m_options.max_candidates);
    lexical_query.terms.reserve(query_tokens.tokens.size());
    for(const auto& token : query_tokens.tokens) {
        lexical_query.terms.push_back(token.text);
    }
    const auto hits = index.search(lexical_query);
    result.chunks.reserve(std::min(query.limit, hits.size()));
    for(const auto& hit : hits) {
        const auto envelope_it = envelopes.find(hit.chunk_id.value());
        if(envelope_it == envelopes.end()) continue;
        const auto current = m_unit_store->find(envelope_it->second.id);
        if(!current || current->revision != envelope_it->second.revision ||
           current->lifecycle_state != KnowledgeUnitLifecycleState::Active ||
           !find_active_projection(
                *current,
                m_options.projection_kind,
                *m_projection_store,
                *m_manifest_store
            )) {
            continue;
        }
        result.chunks.push_back(make_result(*current, hit.score));
        if(result.chunks.size() >= query.limit) break;
    }
    return result;
}

} // namespace agent_memory
