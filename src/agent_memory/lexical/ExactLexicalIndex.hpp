#pragma once
#ifndef AGENT_MEMORY_HEADER_LEXICAL_EXACT_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_LEXICAL_EXACT_LEXICAL_INDEX_HPP_INCLUDED

/// \file ExactLexicalIndex.hpp
/// \brief In-memory exact BM25 lexical index.

#include "ILexicalIndex.hpp"
#include "TokenDictionary.hpp"

#include <map>
#include <set>

namespace agent_memory {

    /// \brief Options used to create an exact in-memory lexical index.
    struct ExactLexicalIndexOptions final {
        Bm25Options bm25;
    };

    /// \brief Deterministic in-memory BM25 lexical index.
    ///
    /// Per-token scoring (BM25):
    ///   idf = log(1 + (N - df + 0.5) / (df + 0.5))   // BM25+ variant
    ///   numerator   = tf * (k1 + 1)
    ///   denominator = tf + k1 * (1 - b + b * |D| / avgdl)
    ///   score = idf * (numerator / denominator)
    ///
    /// Architectural note: this in-memory reference implementation owns
    /// its own token -> document_frequency map (m_dictionary_by_text) and
    /// does NOT route through ITokenDictionary. ITokenDictionary
    /// integration is the responsibility of future persistent backends;
    /// see the contract spec in ILexicalIndex.hpp.
    class ExactLexicalIndex final : public ILexicalIndex {
    public:
        ExactLexicalIndex();
        explicit ExactLexicalIndex(ExactLexicalIndexOptions options);

        [[nodiscard]] std::size_t size() const noexcept override;

        void upsert(LexicalDocumentRecord record) override;

        [[nodiscard]] std::optional<LexicalDocumentStats> find_stats(
            const ChunkId& chunk_id
        ) const override;

        /// \brief Searches all indexed chunks with Okapi BM25.
        [[nodiscard]] std::vector<LexicalSearchResult> search(
            const LexicalSearchQuery& query
        ) const override;

        [[nodiscard]] bool erase(const ChunkId& chunk_id) override;

        [[nodiscard]] std::size_t erase_resource(const ResourceId& resource_id) override;

        void clear() override;

    private:
        struct StoredRecord final {
            LexicalDocumentRecord record;
            std::map<std::string, std::vector<std::uint32_t>> positions_by_term;
        };

        [[nodiscard]] const TokenDictionaryEntry* find_token_entry(
            const std::string& term
        ) const noexcept;
        [[nodiscard]] float score_record(
            const StoredRecord& record,
            const LexicalSearchQuery& query
        ) const;
        [[nodiscard]] float score_term(
            const StoredRecord& record,
            const std::string& term,
            const Bm25Options& options
        ) const;
        [[nodiscard]] float average_document_length() const noexcept;

        void remove_record_stats(const StoredRecord& record);

        static void swap_stored_records(
            StoredRecord& left,
            StoredRecord& right
        ) noexcept;

        ExactLexicalIndexOptions m_options;
        std::map<ChunkId, StoredRecord> m_records;
        std::map<ResourceId, std::set<ChunkId>> m_chunks_by_resource;
        std::map<std::string, TokenDictionaryEntry> m_dictionary_by_text;
        std::uint64_t m_next_token_id = 1;
        std::size_t m_total_token_count = 0;
    };

} // namespace agent_memory

#endif
