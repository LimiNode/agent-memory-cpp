#include "ExactLexicalRetriever.hpp"

#include <agent_memory/domain/Document.hpp>
#include <agent_memory/lexical/Lexical.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/lexical/Tokenizer.hpp>

#include <algorithm>
#include <cstddef>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace agent_memory {

    namespace {

        /// \brief Synthetic revision shared by every chunk ingested from a
        ///        single retriever build. The corpus has no resource
        ///        hierarchy, so one bucket is sufficient.
        ResourceRevision make_revision() {
            return ResourceRevision{
                ResourceId{"resource:corpus"},
                1,
                0,
                0,
                std::nullopt
            };
        }

        /// \brief Builds a `RetrievedChunk` from a scored lexical hit. The
        ///        `chunk.id` and `chunk.document_id` both reuse the corpus
        ///        item id so downstream eval layers can map hits to qrels
        ///        without an extra lookup step. The retrieved metadata is
        ///        propagated from the lexical hit so eval layers can join
        ///        against qrel-side metadata without re-indexing.
        RetrievedChunk make_chunk(
            const std::string& item_id,
            const std::string& text,
            float score,
            Metadata metadata
        ) {
            DocumentChunk chunk;
            chunk.id = ChunkId{item_id};
            chunk.document_id = DocumentId{item_id};
            chunk.source_range = TextRange{0, text.size()};
            chunk.text = text;
            RetrievedChunk retrieved;
            retrieved.chunk = std::move(chunk);
            retrieved.score = score;
            retrieved.metadata = std::move(metadata);
            return retrieved;
        }

    } // namespace

    ITokenizer& ExactLexicalRetriever::default_tokenizer() {
        static StandardTokenizer instance;
        return instance;
    }

    ExactLexicalRetriever::ExactLexicalRetriever(
        std::vector<std::string> corpus_ids,
        std::vector<std::string> corpus_texts,
        std::vector<Metadata> corpus_metadata,
        ITokenizer& tokenizer,
        std::size_t k_neighbours_max
    ) : m_corpus_ids(std::move(corpus_ids)),
        m_corpus_texts(std::move(corpus_texts)),
        m_corpus_metadata(std::move(corpus_metadata)),
        m_tokenizer(&tokenizer),
        m_k_neighbours_max(k_neighbours_max) {
        if(k_neighbours_max == 0) {
            throw std::invalid_argument(
                "ExactLexicalRetriever: k_neighbours_max must be > 0; "
                "use std::numeric_limits<size_t>::max() to effectively disable the cap"
            );
        }
        for(std::size_t index = 0; index < m_corpus_ids.size(); ++index) {
            if(m_corpus_ids[index].empty()) {
                throw std::invalid_argument(
                    "ExactLexicalRetriever: corpus_id must not be empty at index "
                    + std::to_string(index)
                );
            }
        }
        std::unordered_set<std::string> seen;
        seen.reserve(m_corpus_ids.size() * 2);
        for(std::size_t index = 0; index < m_corpus_ids.size(); ++index) {
            if(!seen.insert(m_corpus_ids[index]).second) {
                throw std::invalid_argument(
                    "ExactLexicalRetriever: duplicate corpus_id "
                    + m_corpus_ids[index]
                    + " at index "
                    + std::to_string(index)
                );
            }
        }
        if(m_corpus_ids.size() != m_corpus_texts.size()) {
            throw std::invalid_argument(
                "ExactLexicalRetriever: corpus_ids and corpus_texts must be parallel"
            );
        }
        if(m_corpus_metadata.empty() && !m_corpus_ids.empty()) {
            m_corpus_metadata.assign(m_corpus_ids.size(), Metadata{});
        } else if(m_corpus_metadata.size() != m_corpus_ids.size()) {
            throw std::invalid_argument(
                "ExactLexicalRetriever: corpus_metadata size "
                + std::to_string(m_corpus_metadata.size())
                + " != ids size "
                + std::to_string(m_corpus_ids.size())
            );
        }

        m_id_to_index.reserve(m_corpus_ids.size() * 2);
        for(std::size_t index = 0; index < m_corpus_ids.size(); ++index) {
            m_id_to_index.emplace(m_corpus_ids[index], index);
        }

        const ResourceRevision revision = make_revision();

        for(std::size_t index = 0; index < m_corpus_texts.size(); ++index) {
            auto tokens_result = m_tokenizer->tokenize(m_corpus_texts[index]);
            if(tokens_result.empty()) {
                throw std::invalid_argument(
                    "ExactLexicalRetriever: corpus_texts["
                    + std::to_string(index)
                    + "] tokenizes to empty"
                );
            }

            LexicalDocumentRecord record;
            record.chunk_id = ChunkId{m_corpus_ids[index]};
            record.revision = revision;
            record.tokens = std::move(tokens_result.tokens);
            record.metadata = m_corpus_metadata[index];
            m_index.upsert(std::move(record));
        }
    }

    RetrievalResult ExactLexicalRetriever::retrieve(
        const RetrievalQuery& query
    ) const {
        RetrievalResult result;
        if(query.limit == 0 || m_corpus_ids.empty()) {
            return result;
        }
        if(!query.has_text()) {
            return result;
        }

        const auto tokens_result = m_tokenizer->tokenize(query.text);
        if(tokens_result.empty()) {
            // Query yields no normalized terms; mirrors `query.limit == 0`
            // semantics by returning an empty ordered list.
            return result;
        }

        LexicalSearchQuery lexical_query;
        lexical_query.terms.reserve(tokens_result.tokens.size());
        for(const auto& token : tokens_result.tokens) {
            lexical_query.terms.push_back(token.text);
        }
        lexical_query.limit = std::min(query.limit, m_k_neighbours_max);
        lexical_query.metadata_filters = query.metadata_filters;

        const auto hits = m_index.search(lexical_query);
        result.chunks.reserve(hits.size());
        for(const auto& hit : hits) {
            const auto& item_id = hit.chunk_id.value();
            const auto index = m_id_to_index.at(item_id);
            result.chunks.push_back(
                make_chunk(
                    item_id,
                    m_corpus_texts[index],
                    hit.score,
                    hit.metadata
                )
            );
        }
        return result;
    }

} // namespace agent_memory
