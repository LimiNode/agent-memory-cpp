#include "FlatLexicalIndex.hpp"

#include <limits>
#include <stdexcept>
#include <utility>

namespace agent_memory {

bool is_valid_flat_lexical_unit_input(
    const FlatLexicalUnitInput& input
) noexcept {
    if(input.scope_id.empty()
        || input.unit_id.empty()
        || !is_valid_projection_kind(input.projection_kind)
        || input.resource_id.empty()
        || input.resource_generation == 0
        || input.unit_revision == 0
        || !is_valid_projection_version(input.projection_version)
        || input.projection_version.unit_revision_at_build != input.unit_revision
        || input.normalized_tokens.size() > std::numeric_limits<std::uint32_t>::max()
        || input.normalized_tokens.empty()) {
        return false;
    }

    for(const auto& token : input.normalized_tokens) {
        if(token.empty()) {
            return false;
        }
    }
    return true;
}

FlatLexicalIndex::FlatLexicalIndex(
    LexicalIndexDescriptor descriptor,
    const std::uint32_t posting_layout_version
) : m_descriptor(std::move(descriptor)) {
    if(!is_valid_lexical_index_descriptor(m_descriptor)) {
        throw std::invalid_argument("invalid lexical index descriptor");
    }

    if(posting_layout_version == 0) {
        m_active_snapshot.posting_layout_version =
            m_descriptor.supported_posting_layout_versions.front();
    } else {
        m_active_snapshot.posting_layout_version = posting_layout_version;
    }

    if(!supports_exact_lexical_read(
        LexicalReadFrontier{
            LexicalPublicationSequence{1},
            LexicalActiveSnapshot{
                m_descriptor.ref.statistics_epoch,
                1,
                m_active_snapshot.posting_layout_version
            },
            m_descriptor,
            LexicalReadConsistency::PinnedSnapshot,
            {}
        },
        LexicalIndexReaderProfile{
            m_descriptor.projection_schema_id,
            m_descriptor.projection_schema_version,
            m_descriptor.tokenizer_fingerprint,
            m_descriptor.normalization_fingerprint,
            m_descriptor.supported_posting_layout_versions
        }
    )) {
        throw std::invalid_argument("unsupported lexical posting layout");
    }
    m_active_snapshot.statistics_epoch = m_descriptor.ref.statistics_epoch;
    m_active_snapshot.statistics_generation = 1;
}

const LexicalIndexDescriptor& FlatLexicalIndex::descriptor() const noexcept {
    return m_descriptor;
}

const LexicalActiveSnapshot& FlatLexicalIndex::active_snapshot() const noexcept {
    return m_active_snapshot;
}

std::size_t FlatLexicalIndex::size() const noexcept {
    return m_units.size();
}

TokenId FlatLexicalIndex::get_or_create_token(const std::string& normalized_token) {
    const auto found = m_token_by_text.find(normalized_token);
    if(found != m_token_by_text.end()) {
        return found->second;
    }

    if(m_next_token_id == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("lexical token id space exhausted");
    }

    const TokenId token_id{m_next_token_id++};
    m_token_by_text.emplace(normalized_token, token_id);
    m_text_by_token.emplace(token_id, normalized_token);
    return token_id;
}

void FlatLexicalIndex::remove_unit(const KnowledgeUnitId& unit_id) {
    const auto unit = m_units.find(unit_id);
    if(unit == m_units.end()) {
        return;
    }

    for(const auto& term : unit->second.term_frequencies) {
        const auto posting_bucket = m_postings.find(term.first);
        if(posting_bucket != m_postings.end()) {
            posting_bucket->second.erase(unit_id);
            if(posting_bucket->second.empty()) {
                m_postings.erase(posting_bucket);
            }
        }

        const auto stats = m_token_stats.find(term.first);
        if(stats != m_token_stats.end()) {
            --stats->second.document_frequency;
            stats->second.collection_frequency -= term.second;
            if(stats->second.document_frequency == 0
                && stats->second.collection_frequency == 0) {
                m_token_stats.erase(stats);
            }
        }
    }

    --m_collection_stats.unit_count;
    m_collection_stats.total_token_count -= unit->second.stats.token_count;
    m_units.erase(unit);
}

void FlatLexicalIndex::upsert(FlatLexicalUnitInput input) {
    if(!is_valid_flat_lexical_unit_input(input)
        || input.scope_id != m_descriptor.ref.scope_id
        || input.projection_kind != m_descriptor.ref.projection_kind) {
        throw std::invalid_argument("invalid flat lexical unit input");
    }

    std::map<TokenId, std::uint32_t> term_frequencies;
    for(const auto& token : input.normalized_tokens) {
        ++term_frequencies[get_or_create_token(token)];
    }

    remove_unit(input.unit_id);

    UnitState state{
        FlatLexicalUnitStats{
            input.unit_id,
            input.resource_id,
            input.resource_generation,
            input.unit_revision,
            input.projection_version,
            static_cast<std::uint32_t>(input.normalized_tokens.size()),
            static_cast<std::uint32_t>(term_frequencies.size())
        },
        term_frequencies
    };

    m_collection_stats.unit_count++;
    m_collection_stats.total_token_count += state.stats.token_count;
    for(const auto& term : term_frequencies) {
        m_postings[term.first][input.unit_id] = FlatLexicalPosting{
            term.first,
            input.unit_id,
            input.resource_id,
            input.resource_generation,
            input.projection_version,
            term.second
        };
        auto& stats = m_token_stats[term.first];
        stats.token_id = term.first;
        ++stats.document_frequency;
        stats.collection_frequency += term.second;
    }
    m_units[input.unit_id] = std::move(state);
    ++m_active_snapshot.statistics_generation;
}

bool FlatLexicalIndex::erase(const KnowledgeUnitId& unit_id) {
    if(m_units.find(unit_id) == m_units.end()) {
        return false;
    }
    remove_unit(unit_id);
    ++m_active_snapshot.statistics_generation;
    return true;
}

void FlatLexicalIndex::clear() {
    m_units.clear();
    m_postings.clear();
    m_token_stats.clear();
    m_collection_stats = {};
    ++m_active_snapshot.statistics_generation;
}

std::optional<FlatLexicalUnitStats> FlatLexicalIndex::find_unit_stats(
    const KnowledgeUnitId& unit_id
) const {
    const auto found = m_units.find(unit_id);
    if(found == m_units.end()) {
        return std::nullopt;
    }
    return found->second.stats;
}

std::optional<TokenId> FlatLexicalIndex::find_token_id(
    const std::string& normalized_token
) const {
    const auto found = m_token_by_text.find(normalized_token);
    if(found == m_token_by_text.end()) {
        return std::nullopt;
    }
    return found->second;
}

std::optional<FlatLexicalTokenStats> FlatLexicalIndex::find_token_stats(
    const TokenId& token_id
) const {
    const auto found = m_token_stats.find(token_id);
    if(found == m_token_stats.end()) {
        return std::nullopt;
    }
    return found->second;
}

std::vector<FlatLexicalPosting> FlatLexicalIndex::postings(
    const TokenId& token_id
) const {
    std::vector<FlatLexicalPosting> result;
    const auto found = m_postings.find(token_id);
    if(found == m_postings.end()) {
        return result;
    }

    result.reserve(found->second.size());
    for(const auto& entry : found->second) {
        result.push_back(entry.second);
    }
    return result;
}

FlatLexicalCollectionStats FlatLexicalIndex::collection_stats() const noexcept {
    return m_collection_stats;
}

} // namespace agent_memory
