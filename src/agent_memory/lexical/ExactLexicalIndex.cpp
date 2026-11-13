#include "ExactLexicalIndex.hpp"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <map>
#include <stdexcept>
#include <utility>

namespace agent_memory {

    ExactLexicalIndex::ExactLexicalIndex()
        : ExactLexicalIndex(ExactLexicalIndexOptions{}) {}

    ExactLexicalIndex::ExactLexicalIndex(ExactLexicalIndexOptions options)
        : m_options(options) {
        if(!is_valid(m_options.bm25)) {
            throw std::invalid_argument("invalid BM25 options");
        }
    }

    std::size_t ExactLexicalIndex::size() const noexcept {
        return m_records.size();
    }

    void ExactLexicalIndex::upsert(LexicalDocumentRecord record) {
        if(!is_valid(record)) {
            throw std::invalid_argument("invalid lexical document record");
        }

        const auto chunk_id = record.chunk_id;
        const auto resource_id = record.revision.resource_id;

        StoredRecord new_record;
        new_record.record = std::move(record);
        for(const auto& token : new_record.record.tokens) {
            new_record.positions_by_term[token.text].push_back(
                static_cast<std::uint32_t>(token.position)
            );
        }

        const auto old_it = m_records.find(chunk_id);
        const StoredRecord* old_record = old_it == m_records.end()
            ? nullptr
            : &old_it->second;
        const auto old_token_count = old_record == nullptr
            ? std::size_t{0}
            : old_record->record.tokens.size();
        const auto new_token_count = new_record.record.tokens.size();

        // All potentially allocating work is completed in these temporary
        // structures. The live maps are touched only by the no-allocation
        // commit below, so std::bad_alloc cannot expose a partial upsert.
        std::map<std::string, int> document_frequency_deltas;
        for(const auto& item : new_record.positions_by_term) {
            ++document_frequency_deltas[item.first];
        }
        if(old_record != nullptr) {
            for(const auto& item : old_record->positions_by_term) {
                --document_frequency_deltas[item.first];
            }
        }

        std::map<std::string, TokenDictionaryEntry> staged_dictionary;
        std::uint64_t next_token_id = m_next_token_id;
        for(const auto& item : document_frequency_deltas) {
            if(item.second > 0 && find_token_entry(item.first) == nullptr) {
                staged_dictionary.emplace(
                    item.first,
                    TokenDictionaryEntry{
                        TokenId{next_token_id++},
                        item.first,
                        0
                    }
                );
            }
        }

        std::map<ResourceId, std::set<ChunkId>> staged_resources;
        const auto stage_resource = [this, &staged_resources](
            const ResourceId& id
        ) -> std::set<ChunkId>& {
            const auto existing = m_chunks_by_resource.find(id);
            auto staged = staged_resources.emplace(
                id,
                existing == m_chunks_by_resource.end()
                    ? std::set<ChunkId>{}
                    : existing->second
            );
            return staged.first->second;
        };

        if(old_record != nullptr) {
            stage_resource(old_record->record.revision.resource_id).erase(chunk_id);
        }
        stage_resource(resource_id).insert(chunk_id);

        // Stage the record in a node map as well.  Replacements extract the
        // old node and merge this already-allocated node into the live map;
        // this avoids a potentially-throwing value swap in the commit path.
        std::map<ChunkId, StoredRecord> staged_records;
        staged_records.emplace(chunk_id, std::move(new_record));

        // Commit order is deliberately last: all allocations have already
        // happened. Node-map merges and swaps do not allocate; scalar and
        // document-frequency changes are likewise non-throwing.
        if(old_record != nullptr) {
            const auto old_node = m_records.extract(old_it);
            (void)old_node;
            m_records.merge(staged_records);
        } else {
            m_records.merge(staged_records);
        }

        for(auto item = staged_resources.begin(); item != staged_resources.end();) {
            auto& staged = item->second;
            const auto existing = m_chunks_by_resource.find(item->first);
            if(staged.empty()) {
                if(existing != m_chunks_by_resource.end()) {
                    m_chunks_by_resource.erase(existing);
                }
            } else if(existing != m_chunks_by_resource.end()) {
                existing->second.swap(staged);
            }
            if(staged.empty()) {
                item = staged_resources.erase(item);
            } else {
                ++item;
            }
        }
        m_chunks_by_resource.merge(staged_resources);

        m_dictionary_by_text.merge(staged_dictionary);
        for(const auto& item : document_frequency_deltas) {
            const auto dictionary_it = m_dictionary_by_text.find(item.first);
            if(dictionary_it == m_dictionary_by_text.end()) {
                continue;
            }
            if(item.second > 0) {
                dictionary_it->second.document_frequency +=
                    static_cast<std::size_t>(item.second);
            } else if(item.second < 0) {
                const auto decrement = static_cast<std::size_t>(-item.second);
                dictionary_it->second.document_frequency -= decrement;
                if(dictionary_it->second.document_frequency == 0) {
                    m_dictionary_by_text.erase(dictionary_it);
                }
            }
        }

        m_total_token_count = m_total_token_count -
            old_token_count + new_token_count;
        m_next_token_id = next_token_id;
    }

    std::optional<LexicalDocumentStats> ExactLexicalIndex::find_stats(
        const ChunkId& chunk_id
    ) const {
        const auto it = m_records.find(chunk_id);
        if(it == m_records.end()) {
            return std::nullopt;
        }

        return LexicalDocumentStats{
            it->second.record.chunk_id,
            it->second.record.revision,
            it->second.record.tokens.size(),
            it->second.positions_by_term.size(),
            it->second.record.metadata
        };
    }

    std::vector<LexicalSearchResult> ExactLexicalIndex::search(
        const LexicalSearchQuery& query
    ) const {
        if(query.limit == 0) {
            return {};
        }

        if(!is_valid(query)) {
            throw std::invalid_argument("invalid lexical search query");
        }

        std::vector<LexicalSearchResult> results;
        for(const auto& item : m_records) {
            const auto& record = item.second;
            if(!matches_metadata_filters(record.record.metadata, query.metadata_filters)) {
                continue;
            }

            const auto score = score_record(record, query);
            if(score > 0.0F) {
                LexicalSearchResult hit;
                hit.chunk_id = record.record.chunk_id;
                hit.score = score;
                hit.metadata = record.record.metadata;
                hit.section_id = record.record.section_id;
                hit.resource_id = record.record.revision.resource_id;
                hit.enrichment_level = record.record.enrichment_level;
                hit.result_tier = 0;  // chunk-level result
                results.push_back(std::move(hit));
            }
        }

        std::sort(
            results.begin(),
            results.end(),
            [](const auto& lhs, const auto& rhs) {
                if(lhs.score == rhs.score) {
                    return lhs.chunk_id < rhs.chunk_id;
                }
                return lhs.score > rhs.score;
            }
        );

        if(results.size() > query.limit) {
            results.resize(query.limit);
        }

        return results;
    }

    bool ExactLexicalIndex::erase(const ChunkId& chunk_id) {
        const auto it = m_records.find(chunk_id);
        if(it == m_records.end()) {
            return false;
        }

        const auto resource_id = it->second.record.revision.resource_id;
        remove_record_stats(it->second);
        m_records.erase(it);

        const auto by_resource_it = m_chunks_by_resource.find(resource_id);
        if(by_resource_it != m_chunks_by_resource.end()) {
            by_resource_it->second.erase(chunk_id);
            if(by_resource_it->second.empty()) {
                m_chunks_by_resource.erase(by_resource_it);
            }
        }

        return true;
    }

    std::size_t ExactLexicalIndex::erase_resource(const ResourceId& resource_id) {
        const auto by_resource_it = m_chunks_by_resource.find(resource_id);
        if(by_resource_it == m_chunks_by_resource.end()) {
            return 0;
        }

        const auto chunk_ids = by_resource_it->second;
        std::size_t removed = 0;
        for(const auto& chunk_id : chunk_ids) {
            if(erase(chunk_id)) {
                ++removed;
            }
        }
        return removed;
    }

    void ExactLexicalIndex::clear() {
        m_records.clear();
        m_chunks_by_resource.clear();
        m_dictionary_by_text.clear();
        m_next_token_id = 1;
        m_total_token_count = 0;
    }

    const TokenDictionaryEntry* ExactLexicalIndex::find_token_entry(
        const std::string& term
    ) const noexcept {
        const auto it = m_dictionary_by_text.find(term);
        if(it == m_dictionary_by_text.end()) {
            return nullptr;
        }
        return &it->second;
    }

    float ExactLexicalIndex::score_record(
        const StoredRecord& record,
        const LexicalSearchQuery& query
    ) const {
        const Bm25Options& bm25 = query.bm25.has_value()
            ? *query.bm25
            : m_options.bm25;
        float score = 0.0F;
        for(const auto& term : query.terms) {
            score += score_term(record, term, bm25);
        }
        return score;
    }

    float ExactLexicalIndex::score_term(
        const StoredRecord& record,
        const std::string& term,
        const Bm25Options& options
    ) const {
        const auto positions_it = record.positions_by_term.find(term);
        if(positions_it == record.positions_by_term.end()) {
            return 0.0F;
        }

        const auto* entry = find_token_entry(term);
        if(entry == nullptr || entry->document_frequency == 0 || m_records.empty()) {
            return 0.0F;
        }

        const auto document_count = static_cast<float>(m_records.size());
        const auto document_frequency = static_cast<float>(entry->document_frequency);
        const auto idf = std::log(
            1.0F + ((document_count - document_frequency + 0.5F) /
                (document_frequency + 0.5F))
        );

        const auto term_frequency = static_cast<float>(positions_it->second.size());
        const auto document_length = static_cast<float>(record.record.tokens.size());
        const auto average_length = average_document_length();
        const auto length_ratio = average_length > 0.0F
            ? document_length / average_length
            : 1.0F;
        const auto denominator = term_frequency +
            options.k1 * (1.0F - options.b + options.b * length_ratio);

        // Denominator is mathematically >= tf >= 1 given k1 > 0,
        // b in [0, 1] and length_ratio >= 0 (validated by is_valid(Bm25Options)
        // and the avgdl > 0 fallback). The guard is dead; assert it instead.
        assert(denominator > 0.0F);

        return idf * ((term_frequency * (options.k1 + 1.0F)) / denominator);
    }

    float ExactLexicalIndex::average_document_length() const noexcept {
        if(m_records.empty()) {
            return 0.0F;
        }

        return static_cast<float>(m_total_token_count) /
            static_cast<float>(m_records.size());
    }

    void ExactLexicalIndex::remove_record_stats(const StoredRecord& record) {
        if(m_total_token_count >= record.record.tokens.size()) {
            m_total_token_count -= record.record.tokens.size();
        } else {
            m_total_token_count = 0;
        }

        for(const auto& term_item : record.positions_by_term) {
            const auto entry_it = m_dictionary_by_text.find(term_item.first);
            if(entry_it != m_dictionary_by_text.end() && entry_it->second.document_frequency > 0) {
                --entry_it->second.document_frequency;
                // Erase the entry once df reaches 0 to avoid stale entries
                // accumulating in the dictionary over a long-lived index.
                if(entry_it->second.document_frequency == 0) {
                    m_dictionary_by_text.erase(entry_it);
                }
            }
        }
    }

} // namespace agent_memory
