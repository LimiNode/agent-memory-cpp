#include "MdbxPersistentLexicalCandidateAdapter.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
#include <map>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

struct AccumulatedCandidate final {
    PersistentLexicalCandidate candidate;
    float score = 0.0F;
};

bool same_projection_version(
    const ProjectionVersionRef& left,
    const ProjectionVersionRef& right
) {
    return left.unit_revision_at_build == right.unit_revision_at_build &&
           left.projection_revision == right.projection_revision &&
           left.derivation_generation == right.derivation_generation &&
           left.derivation_fingerprint == right.derivation_fingerprint;
}

bool same_candidate_observation(
    const PersistentLexicalCandidate& left,
    const PersistentLexicalCandidate& right
) {
    return left.scope_id == right.scope_id &&
           left.resource_id == right.resource_id &&
           left.resource_generation == right.resource_generation &&
           left.identity.unit_revision == right.identity.unit_revision &&
           left.identity.projection_kind == right.identity.projection_kind &&
           same_projection_version(left.projection_version, right.projection_version);
}

bool matches_current_projection_observation(
    const PersistentLexicalCandidate& candidate,
    const RevisionBoundRetrievalResult& admitted,
    IProjectionStore& projection_store
) {
    if(candidate.scope_id != admitted.scope_id ||
       candidate.identity.projection_kind != ProjectionKind::Original ||
       !is_valid_projection_version(candidate.projection_version)) {
        return false;
    }
    const auto projections = projection_store.list(
        admitted.scope_id,
        candidate.identity.knowledge_unit_id,
        ProjectionKind::Original
    );
    for(const auto& projection : projections) {
        if(projection.scope_id != admitted.scope_id ||
           projection.unit_id != candidate.identity.knowledge_unit_id ||
           projection.kind != ProjectionKind::Original ||
           !is_valid_search_projection(projection) ||
           projection.body != admitted.text ||
           projection.version.unit_revision_at_build != admitted.unit_revision ||
           !same_projection_version(
                projection.version, candidate.projection_version)) {
            continue;
        }
        return true;
    }
    return false;
}

} // namespace

MdbxPersistentLexicalCandidateAdapter::MdbxPersistentLexicalCandidateAdapter(
    MdbxPersistentLexicalIndex& index,
    ITokenizer& tokenizer,
    PersistentLexicalCandidateOptions options
) : m_index(&index), m_tokenizer(&tokenizer), m_options(std::move(options)) {
    if(m_options.max_candidates == 0 ||
       m_options.max_postings_per_term == 0 ||
       m_options.max_query_terms == 0) {
        throw std::invalid_argument("persistent candidate limits must be non-zero");
    }
}

PersistentLexicalCandidateResponse
MdbxPersistentLexicalCandidateAdapter::generate(const RetrievalQuery& query) const {
    PersistentLexicalCandidateResponse response;
    const auto snapshot = m_index->read_snapshot();
    response.frontier = snapshot.frontier;
    if(query.limit == 0 || !query.has_text() || !query.metadata_filters.empty()) {
        response.completeness = PersistentLexicalCandidateCompleteness::UnsupportedQuery;
        response.diagnostic = query.metadata_filters.empty()
            ? "empty persistent lexical query"
            : "persistent lexical candidate generation does not support metadata filters";
        return response;
    }

    const auto tokens = m_tokenizer->tokenize(query.text);
    if(tokens.tokens.empty() || tokens.tokens.size() > m_options.max_query_terms) {
        response.completeness = PersistentLexicalCandidateCompleteness::UnsupportedQuery;
        response.diagnostic = tokens.tokens.empty()
            ? "query produced no normalized terms"
            : "query term count exceeds persistent candidate budget";
        return response;
    }

    std::map<KnowledgeUnitId, AccumulatedCandidate> accumulated;
    for(const auto& token : tokens.tokens) {
        const auto token_id = snapshot.index.find_token_id(token.text);
        if(!token_id) continue;
        const auto token_stats = snapshot.index.find_token_stats(*token_id);
        if(!token_stats) {
            response.completeness =
                PersistentLexicalCandidateCompleteness::InconsistentSnapshot;
            response.diagnostic =
                "persistent lexical token is missing collection statistics";
            return response;
        }
        const auto posting_count = token_stats->document_frequency;
        const auto physical_posting_count = snapshot.index.posting_count(*token_id);
        if(!is_consistent_flat_lexical_posting_count(
                posting_count, physical_posting_count)) {
            response.completeness =
                PersistentLexicalCandidateCompleteness::InconsistentSnapshot;
            response.diagnostic =
                "persistent lexical posting/statistics counts disagree";
            return response;
        }
        const auto posting_limit = posting_count > m_options.max_postings_per_term
            ? m_options.max_postings_per_term
            : static_cast<std::size_t>(posting_count);
        if(posting_count > m_options.max_postings_per_term) {
            response.completeness = PersistentLexicalCandidateCompleteness::Truncated;
        }
        const auto postings = snapshot.index.postings_prefix(
            *token_id, posting_limit);
        const auto window_completeness =
            persistent_lexical_candidate_detail::classify_posting_window(
                posting_limit, postings.size(),
                posting_count > m_options.max_postings_per_term);
        if(window_completeness ==
           PersistentLexicalCandidateCompleteness::InconsistentSnapshot) {
            // The flat snapshot normally keeps DF and physical membership in
            // lockstep. If a future reader observes a torn/corrupt pair, do
            // not index past the returned vector or claim completeness.
            response.candidates.clear();
            response.completeness =
                PersistentLexicalCandidateCompleteness::InconsistentSnapshot;
            response.diagnostic =
                "persistent lexical posting/statistics counts disagree";
            return response;
        }
        if(window_completeness == PersistentLexicalCandidateCompleteness::Truncated) {
            response.completeness = window_completeness;
        }
        for(std::size_t index = 0; index < postings.size(); ++index) {
            ++response.examined_postings;
            const auto& posting = postings[index];
            auto found = accumulated.find(posting.unit_id);
            if(found == accumulated.end()) {
                if(accumulated.size() >= m_options.max_candidates) {
                    response.completeness = PersistentLexicalCandidateCompleteness::Truncated;
                    continue;
                }
                PersistentLexicalCandidate candidate;
                candidate.identity.knowledge_unit_id = posting.unit_id;
                candidate.identity.unit_revision =
                    posting.projection_version.unit_revision_at_build;
                candidate.identity.projection_kind =
                    snapshot.frontier.index_descriptor.ref.projection_kind;
                candidate.scope_id = snapshot.frontier.index_descriptor.ref.scope_id;
                candidate.resource_id = posting.resource_id;
                candidate.resource_generation = posting.resource_generation;
                candidate.projection_version = posting.projection_version;
                found = accumulated.emplace(
                    posting.unit_id,
                    AccumulatedCandidate{std::move(candidate), 0.0F}
                ).first;
            } else if(!same_candidate_observation(
                          found->second.candidate, PersistentLexicalCandidate{
                              found->second.candidate.identity,
                              snapshot.frontier.index_descriptor.ref.scope_id,
                              posting.resource_id,
                              posting.resource_generation,
                              posting.projection_version
                          })) {
                response.candidates.clear();
                response.completeness =
                    PersistentLexicalCandidateCompleteness::InconsistentSnapshot;
                response.diagnostic =
                    "persistent lexical postings disagree for one knowledge unit";
                return response;
            }
            found->second.score += static_cast<float>(posting.term_frequency);
        }
    }

    response.candidates.reserve(accumulated.size());
    for(auto& entry : accumulated) {
        entry.second.candidate.identity.score = entry.second.score;
        response.candidates.push_back(std::move(entry.second.candidate));
    }
    std::stable_sort(
        response.candidates.begin(), response.candidates.end(),
        [](const auto& lhs, const auto& rhs) {
            if(lhs.identity.score != rhs.identity.score) {
                return lhs.identity.score > rhs.identity.score;
            }
            return lhs.identity.knowledge_unit_id.value() <
                rhs.identity.knowledge_unit_id.value();
        }
    );
    if(response.candidates.size() > query.limit) {
        response.candidates.resize(query.limit);
    }
    return response;
}

RevisionBoundRetrievalResponse admit_persistent_lexical_candidates(
    const ScopeId& scope_id,
    const PersistentLexicalCandidateResponse& candidates,
    IKnowledgeUnitStore& unit_store,
    IProjectionStore& projection_store,
    IResourceManifestStorage& manifest_store,
    ITokenizer& tokenizer,
    ProjectionAwareLexicalRetrieverOptions options
) {
    if(candidates.completeness ==
           PersistentLexicalCandidateCompleteness::InconsistentSnapshot) {
        RevisionBoundRetrievalResponse result;
        result.completeness = RevisionBoundCompleteness::InconsistentSnapshot;
        result.diagnostic = candidates.diagnostic;
        return result;
    }
    if(candidates.completeness ==
           PersistentLexicalCandidateCompleteness::UnsupportedQuery &&
       !candidates.candidates.empty()) {
        RevisionBoundRetrievalResponse result;
        result.completeness = RevisionBoundCompleteness::InconsistentSnapshot;
        result.diagnostic =
            "unsupported persistent lexical response carried candidates";
        return result;
    }
    std::map<KnowledgeUnitId, PersistentLexicalCandidate> observations;
    for(const auto& candidate : candidates.candidates) {
        const auto found = observations.find(candidate.identity.knowledge_unit_id);
        if(found == observations.end()) {
            observations.emplace(candidate.identity.knowledge_unit_id, candidate);
        } else {
            RevisionBoundRetrievalResponse result;
            result.completeness = RevisionBoundCompleteness::InconsistentSnapshot;
            result.diagnostic =
                same_candidate_observation(found->second, candidate)
                    ? "persistent lexical response contains duplicate unit observations"
                    : "persistent lexical response has conflicting unit observations";
            return result;
        }
    }
    RevisionBoundLexicalRetriever admission(
        unit_store, projection_store, manifest_store, scope_id, tokenizer,
        std::move(options));
    std::vector<ProjectionAwareLexicalCandidate> identities;
    std::vector<float> scores;
    identities.reserve(candidates.candidates.size());
    scores.reserve(candidates.candidates.size());
    for(const auto& candidate : candidates.candidates) {
        identities.push_back(candidate.identity);
        scores.push_back(candidate.identity.score);
    }
    auto result = admission.admit_candidates(identities, scores);
    // The canonical admission owns freshness. The persistent observations are
    // additionally checked here so an old resource-generation observation
    // cannot be mistaken for the admitted source revision.
    for(std::size_t index = 0; index < candidates.candidates.size() &&
                                index < result.results.size(); ++index) {
        const auto& candidate = candidates.candidates[index];
        auto& admitted = result.results[index];
        if(admitted.freshness == RevisionBoundFreshness::Current &&
           (admitted.root_resource_id != candidate.resource_id ||
            admitted.root_generation != candidate.resource_generation ||
            !matches_current_projection_observation(
                candidate, admitted, projection_store))) {
            admitted.freshness = RevisionBoundFreshness::Stale;
            admitted.materialization = RevisionBoundMaterialization::Unavailable;
            admitted.text.clear();
            if(result.admitted_count != 0) --result.admitted_count;
            ++result.stale_count;
        }
    }
    if(candidates.completeness == PersistentLexicalCandidateCompleteness::Truncated) {
        result.completeness = RevisionBoundCompleteness::BudgetExceeded;
        result.diagnostic = "persistent lexical candidate window was truncated";
    } else if(candidates.completeness ==
              PersistentLexicalCandidateCompleteness::UnsupportedQuery) {
        result.completeness = RevisionBoundCompleteness::UnsupportedQuery;
        result.diagnostic = candidates.diagnostic;
    }
    return result;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
