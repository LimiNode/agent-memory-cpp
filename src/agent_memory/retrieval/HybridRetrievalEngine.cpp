#include "HybridRetrievalEngine.hpp"

#include <agent_memory/lexical/IQueryAnalyzer.hpp>
#include <agent_memory/lexical/IReranker.hpp>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <vector>
#include <utility>

namespace agent_memory {

    namespace {

        [[nodiscard]] bool same_candidate_identity(
            const LexicalSearchResult& left,
            const LexicalSearchResult& right
        ) noexcept {
            // A chunk may legitimately have more than one structural result
            // tier. Keep the full upstream identity instead of keying only on
            // chunk_id and accidentally collapsing distinct admitted records.
            return left.chunk_id == right.chunk_id
                && left.resource_id == right.resource_id
                && left.section_id == right.section_id
                && left.result_tier == right.result_tier;
        }

        [[nodiscard]] bool same_protected_fields(
            const LexicalSearchResult& left,
            const LexicalSearchResult& right
        ) noexcept {
            return same_candidate_identity(left, right)
                && left.metadata.values() == right.metadata.values()
                && left.enrichment_level == right.enrichment_level;
        }

        [[nodiscard]] bool finite_score(const float score) noexcept {
            return std::isfinite(score);
        }

        [[nodiscard]] std::vector<LexicalSearchResult> admit_reranked_hits(
            const std::vector<LexicalSearchResult>& admitted,
            const std::vector<LexicalSearchResult>& reranked
        ) {
            if(reranked.size() > admitted.size()) {
                return {};
            }

            // Build the final response from trusted upstream records. The
            // reranker may reorder records and adjust finite scores only.
            std::vector<LexicalSearchResult> result;
            result.reserve(reranked.size());
            for(const auto& candidate : reranked) {
                if(!finite_score(candidate.score)) {
                    return {};
                }

                const auto matching = std::find_if(
                    admitted.begin(),
                    admitted.end(),
                    [&candidate](const LexicalSearchResult& original) {
                        return same_candidate_identity(original, candidate);
                    }
                );
                if(matching == admitted.end()
                   || !same_protected_fields(*matching, candidate)
                   || std::find_if(
                          result.begin(),
                          result.end(),
                          [&candidate](const LexicalSearchResult& previous) {
                              return same_candidate_identity(previous, candidate);
                          }
                      ) != result.end()) {
                    // Fail closed: a malformed reranker cannot smuggle an
                    // unadmitted identity or duplicate into the response.
                    return {};
                }

                auto trusted = *matching;
                trusted.score = candidate.score;
                result.push_back(std::move(trusted));
            }
            return result;
        }

    } // namespace

    HybridRetrievalEngine::HybridRetrievalEngine()
        : m_reranker(std::make_unique<IdentityReranker>())
        , m_analyzer(std::make_unique<PassthroughQueryAnalyzer>()) {}

    HybridRetrievalEngine::HybridRetrievalEngine(const ILexicalIndex& index)
        : m_index(&index)
        , m_reranker(std::make_unique<IdentityReranker>())
        , m_analyzer(std::make_unique<PassthroughQueryAnalyzer>()) {}

    void HybridRetrievalEngine::set_lexical_index(const ILexicalIndex& index) noexcept {
        m_index = &index;
    }

    void HybridRetrievalEngine::set_reranker(std::unique_ptr<IReranker> reranker) {
        if(reranker) {
            m_reranker = std::move(reranker);
        } else {
            m_reranker = std::make_unique<IdentityReranker>();
        }
    }

    void HybridRetrievalEngine::set_query_analyzer(std::unique_ptr<IQueryAnalyzer> analyzer) {
        if(analyzer) {
            m_analyzer = std::move(analyzer);
        } else {
            m_analyzer = std::make_unique<PassthroughQueryAnalyzer>();
        }
    }

    RetrievalResponse HybridRetrievalEngine::retrieve(
        const RetrievalRequest& request
    ) const {
        RetrievalResponse response;

        if(request.limit == 0 || request.query.empty() || m_index == nullptr) {
            return response;
        }

        const QueryAnalysis analysis = m_analyzer->analyze(request.query);

        LexicalSearchQuery lexical_query;
        lexical_query.terms = analysis.keywords;
        if(lexical_query.terms.empty()) {
            lexical_query.terms.push_back(request.query);
        }
        lexical_query.limit = request.limit;
        lexical_query.metadata_filters = request.metadata_filters;

        std::vector<LexicalSearchResult> hits = m_index->search(lexical_query);
        if(hits.size() > request.limit) {
            hits.resize(request.limit);
        }

        const auto admitted_hits = hits;
        hits = m_reranker->rerank(request.query, std::move(hits));
        hits = admit_reranked_hits(admitted_hits, hits);

        response.items.reserve(hits.size());
        for(const auto& hit : hits) {
            RetrievalResponseItem item;
            item.lexical = hit;
            // The lexical hit already carries section_id, resource_id,
            // and result_tier; readers should pull those values from
            // item.lexical. We only copy chunk-id and metadata onto
            // item.object, plus the enrichment level carried by the
            // upstream enricher.
            item.object.type = ObjectType::Chunk;
            item.object.metadata = hit.metadata;
            item.object.enrichment_level = hit.enrichment_level;
            if(hit.result_tier == 0) {
                item.object.id = MemoryObjectId{std::string(hit.chunk_id.value())};
            }
            response.items.push_back(std::move(item));
        }

        return response;
    }

} // namespace agent_memory
