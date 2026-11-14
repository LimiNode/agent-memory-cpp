#include "MultiProbeHammingIndex.hpp"

#include <algorithm>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace agent_memory {

    MultiProbeHammingIndex::MultiProbeHammingIndex()
        : MultiProbeHammingIndex(MultiProbeHammingIndexOptions{}) {}

    MultiProbeHammingIndex::MultiProbeHammingIndex(MultiProbeHammingIndexOptions options)
        : m_options(std::move(options)) {
        validate_options();
        if(m_options.signature_info) {
            if(!is_valid(*m_options.signature_info)) {
                throw std::invalid_argument(
                    "MultiProbeHammingIndex configured signature identity must be valid"
                );
            }
            initialize_for_identity();
        }
    }

    std::size_t MultiProbeHammingIndex::bit_count() const noexcept {
        return m_options.signature_info ? m_options.signature_info->bit_count : 0;
    }

    std::size_t MultiProbeHammingIndex::size() const noexcept {
        return m_records.size();
    }

    void MultiProbeHammingIndex::upsert(BinarySignatureRecord record) {
        validate_record(record);

        bool adopted_identity = false;
        if(!m_options.signature_info) {
            // Identity adoption is part of the insertion transaction.  A failed
            // first write must not make a later, distinct identity look invalid.
            try {
                m_options.signature_info = record.signature_info;
                initialize_for_identity();
                adopted_identity = true;
            } catch(...) {
                m_options.signature_info.reset();
                m_tables.clear();
                m_distance.reset();
                throw;
            }
        }

        const auto existing = m_positions.find(record.chunk_id);
        if(existing != m_positions.end()) {
            const auto position = existing->second;
            const auto* current_words = signature_words(position);
            std::vector<std::uint64_t> previous_signature(
                current_words,
                current_words + static_cast<std::ptrdiff_t>(m_distance->word_count())
            );
            // Copy before touching the index.  This keeps all allocation points
            // before the staged posting update and lets the old record remain
            // searchable until the replacement is ready to commit.
            auto next_metadata = record.metadata;
            std::size_t staged_tables = 0;
            try {
                const auto* replacement_words = record.signature.words().data();
                for(std::size_t table = 0; table < m_tables.size(); ++table) {
                    const auto key = bucket_key(table, replacement_words);
                    auto [bucket, inserted] = m_tables[table].try_emplace(key);
                    try {
                        bucket->second.push_back(position);
                    } catch(...) {
                        // If this was a newly-created bucket, remove the empty
                        // container. An existing bucket still contains only
                        // the old posting and must remain untouched.
                        if(inserted && bucket->second.empty()) {
                            m_tables[table].erase(bucket);
                        }
                        throw;
                    }
                    ++staged_tables;
                    if(table == 0) {
                        inject_failure(MultiProbeHammingIndexFaultPoint::AfterFirstTableInsert);
                    }
                }

                // Validate the old postings before changing any observable state.
                for(std::size_t table = 0; table < m_tables.size(); ++table) {
                    const auto key = bucket_key(table, previous_signature.data());
                    const auto bucket = m_tables[table].find(key);
                    if(bucket == m_tables[table].end()
                       || std::find(
                              bucket->second.begin(),
                              bucket->second.end(),
                              position
                          ) == bucket->second.end()) {
                        throw std::logic_error(
                            "MultiProbeHammingIndex replacement posting is missing"
                        );
                    }
                }

                static_assert(
                    std::is_nothrow_move_assignable_v<Metadata>,
                    "Metadata move assignment must not throw during index commit"
                );
                m_records[position].metadata = std::move(next_metadata);
                for(std::size_t table = 0; table < m_tables.size(); ++table) {
                    remove_one_posting(position, previous_signature.data(), table);
                }
                std::copy(
                    record.signature.words().begin(),
                    record.signature.words().end(),
                    signature_words(position)
                );
            } catch(...) {
                rollback_staged_postings(
                    position,
                    record.signature.words().data(),
                    staged_tables
                );
                if(adopted_identity) {
                    m_options.signature_info.reset();
                    m_tables.clear();
                    m_distance.reset();
                }
                throw;
            }
            return;
        }

        const auto position = m_records.size();
        bool signature_storage_committed = false;
        bool tables_touched = false;
        try {
            m_records.push_back(StoredRecord{record.chunk_id, std::move(record.metadata)});
            inject_failure(
                MultiProbeHammingIndexFaultPoint::AfterRecordAppendBeforeSignatureStorage
            );
            m_signature_words.insert(
                m_signature_words.end(),
                record.signature.words().begin(),
                record.signature.words().end()
            );
            signature_storage_committed = true;
            inject_failure(
                MultiProbeHammingIndexFaultPoint::AfterSignatureStorageBeforeTables
            );
            tables_touched = true;
            add_to_tables(position);
            m_positions.emplace(record.chunk_id, position);
        } catch(...) {
            if(tables_touched && signature_storage_committed) {
                remove_from_tables(position);
            }
            if(signature_storage_committed) {
                m_signature_words.resize(position * m_distance->word_count());
            }
            if(m_records.size() > position) {
                m_records.pop_back();
            }
            m_positions.erase(record.chunk_id);
            if(adopted_identity) {
                m_options.signature_info.reset();
                m_tables.clear();
                m_distance.reset();
            }
            throw;
        }
    }

    std::optional<BinarySignatureRecord> MultiProbeHammingIndex::find(
        const ChunkId& chunk_id
    ) const {
        const auto found = m_positions.find(chunk_id);
        if(found == m_positions.end()) {
            return std::nullopt;
        }
        const auto position = found->second;
        const auto* words = signature_words(position);
        return BinarySignatureRecord{
            m_records[position].chunk_id,
            BinarySignature(
                bit_count(),
                std::vector<std::uint64_t>(
                    words,
                    words + static_cast<std::ptrdiff_t>(m_distance->word_count())
                )
            ),
            *m_options.signature_info,
            m_records[position].metadata
        };
    }

    std::vector<BinarySignatureSearchResult> MultiProbeHammingIndex::search(
        const BinarySignatureSearchQuery& query
    ) const {
        return search_with_diagnostics(query).results;
    }

    MultiProbeHammingSearchResult MultiProbeHammingIndex::search_with_diagnostics(
        const BinarySignatureSearchQuery& query
    ) const {
        MultiProbeHammingSearchResult output;
        if(query.limit == 0) {
            return output;
        }
        validate_query(query);
        if(m_records.empty()) {
            return output;
        }

        const auto multiply_would_overflow = query.limit >
            std::numeric_limits<std::size_t>::max() / m_options.candidate_multiplier;
        const auto multiplied_target = multiply_would_overflow
            ? std::numeric_limits<std::size_t>::max()
            : query.limit * m_options.candidate_multiplier;
        const auto candidate_target = std::min(
            m_records.size(),
            std::max(m_options.minimum_candidate_count, multiplied_target)
        );

        std::vector<unsigned char> seen(m_records.size(), 0);
        std::vector<std::size_t> candidates;
        candidates.reserve(candidate_target);
        const auto add_bucket = [&](std::size_t table, std::uint64_t key) {
            ++output.probed_bucket_count;
            const auto bucket = m_tables[table].find(key);
            if(bucket == m_tables[table].end()) {
                return;
            }
            for(const auto position : bucket->second) {
                ++output.visited_posting_count;
                if(seen[position] == 0) {
                    seen[position] = 1;
                    if(!query.metadata_filters.empty()
                       && !matches_metadata_filters(
                           m_records[position].metadata,
                           query.metadata_filters
                       )) {
                        continue;
                    }
                    candidates.push_back(position);
                }
            }
        };

        for(std::size_t radius = 0; radius <= m_options.max_probe_radius; ++radius) {
            for(std::size_t table = 0; table < m_tables.size(); ++table) {
                const auto key = bucket_key(table, query.signature.words().data());
                if(radius == 0) {
                    add_bucket(table, key);
                } else if(radius == 1) {
                    for(std::size_t first = 0; first < m_options.bits_per_table; ++first) {
                        add_bucket(table, key ^ (std::uint64_t{1} << first));
                    }
                } else if(radius == 2) {
                    for(std::size_t first = 0; first < m_options.bits_per_table; ++first) {
                        for(std::size_t second = first + 1;
                            second < m_options.bits_per_table; ++second) {
                            add_bucket(
                                table,
                                key ^ (std::uint64_t{1} << first)
                                    ^ (std::uint64_t{1} << second)
                            );
                        }
                    }
                }
            }
            if(candidates.size() >= candidate_target) {
                break;
            }
        }

        struct ScoredPosition final {
            std::size_t position = 0;
            std::size_t distance = 0;
        };
        std::vector<ScoredPosition> scored;
        scored.reserve(candidates.size());
        for(const auto position : candidates) {
            scored.push_back({
                position,
                m_distance->distance_words(
                    query.signature.words().data(),
                    signature_words(position)
                )
            });
        }
        output.candidate_count = scored.size();

        const auto closer = [this](const ScoredPosition& lhs, const ScoredPosition& rhs) {
            if(lhs.distance == rhs.distance) {
                return m_records[lhs.position].chunk_id < m_records[rhs.position].chunk_id;
            }
            return lhs.distance < rhs.distance;
        };
        if(scored.size() > query.limit) {
            std::partial_sort(
                scored.begin(),
                scored.begin() + static_cast<std::ptrdiff_t>(query.limit),
                scored.end(),
                closer
            );
            scored.resize(query.limit);
        } else {
            std::sort(scored.begin(), scored.end(), closer);
        }

        output.results.reserve(scored.size());
        for(const auto& item : scored) {
            output.results.push_back({
                m_records[item.position].chunk_id,
                item.distance,
                m_records[item.position].metadata
            });
        }
        return output;
    }

    bool MultiProbeHammingIndex::erase(const ChunkId& chunk_id) {
        const auto existing = m_positions.find(chunk_id);
        if(existing == m_positions.end()) {
            return false;
        }

        const auto position = existing->second;
        const auto last_position = m_records.size() - 1;
        remove_from_tables(position);
        if(position != last_position) {
            const auto* last_words = signature_words(last_position);
            for(std::size_t table = 0; table < m_tables.size(); ++table) {
                auto bucket = m_tables[table].find(bucket_key(table, last_words));
                if(bucket == m_tables[table].end()) {
                    throw std::logic_error(
                        "MultiProbeHammingIndex table posting is missing"
                    );
                }
                const auto posting = std::find(
                    bucket->second.begin(),
                    bucket->second.end(),
                    last_position
                );
                if(posting == bucket->second.end()) {
                    throw std::logic_error(
                        "MultiProbeHammingIndex table position is missing"
                    );
                }
                *posting = position;
            }
            m_records[position] = std::move(m_records[last_position]);
            std::copy(
                signature_words(last_position),
                signature_words(last_position)
                    + static_cast<std::ptrdiff_t>(m_distance->word_count()),
                signature_words(position)
            );
            m_positions.find(m_records[position].chunk_id)->second = position;
        }

        m_records.pop_back();
        m_signature_words.resize(m_records.size() * m_distance->word_count());
        m_positions.erase(existing);
        return true;
    }

    void MultiProbeHammingIndex::clear() {
        m_records.clear();
        m_signature_words.clear();
        m_positions.clear();
        for(auto& table : m_tables) {
            table.clear();
        }
    }

    void MultiProbeHammingIndex::initialize_for_identity() {
        validate_options();
        if(m_options.table_count > bit_count() / m_options.bits_per_table) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex projected bits must not exceed signature width"
            );
        }
        m_tables.clear();
        m_tables.resize(m_options.table_count);
        m_distance.emplace(binary_signature_word_count(bit_count()));
    }

    void MultiProbeHammingIndex::validate_options() const {
        if(m_options.table_count == 0) {
            throw std::invalid_argument("MultiProbeHammingIndex table_count must be positive");
        }
        if(m_options.bits_per_table == 0 || m_options.bits_per_table > 63) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex bits_per_table must be in [1, 63]"
            );
        }
        if(m_options.max_probe_radius > 2
           || m_options.max_probe_radius > m_options.bits_per_table) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex max_probe_radius must be at most two and fit the key"
            );
        }
        if(m_options.candidate_multiplier == 0) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex candidate_multiplier must be positive"
            );
        }
    }

    void MultiProbeHammingIndex::validate_record(
        const BinarySignatureRecord& record
    ) const {
        if(!is_valid(record.signature_info)) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex records must use valid signature identity"
            );
        }
        if(record.signature.bit_count() != record.signature_info.bit_count) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex record signature width must match identity"
            );
        }
        if(record.signature.empty()
           || (m_options.signature_info
               && record.signature.bit_count() != bit_count())) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex record signature width mismatch"
            );
        }
        require_matching_identity(record.signature_info);
    }

    void MultiProbeHammingIndex::validate_query(
        const BinarySignatureSearchQuery& query
    ) const {
        if(!is_valid(query.signature_info)) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex queries must use valid signature identity"
            );
        }
        if(query.signature.bit_count() != query.signature_info.bit_count) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex query signature width must match identity"
            );
        }
        if(query.signature.empty()) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex queries must not use empty signatures"
            );
        }
        require_matching_identity(query.signature_info);
    }

    void MultiProbeHammingIndex::require_matching_identity(
        const BinarySignatureInfo& info
    ) const {
        if(m_options.signature_info && info != *m_options.signature_info) {
            throw std::invalid_argument(
                "MultiProbeHammingIndex binary signature identity mismatch"
            );
        }
    }

    const std::uint64_t* MultiProbeHammingIndex::signature_words(
        std::size_t position
    ) const noexcept {
        return m_signature_words.data() + position * m_distance->word_count();
    }

    std::uint64_t* MultiProbeHammingIndex::signature_words(std::size_t position) noexcept {
        return m_signature_words.data() + position * m_distance->word_count();
    }

    std::uint64_t MultiProbeHammingIndex::bucket_key(
        std::size_t table,
        const std::uint64_t* words
    ) const noexcept {
        std::uint64_t key = 0;
        const auto projection_count = m_options.table_count * m_options.bits_per_table;
        for(std::size_t offset = 0; offset < m_options.bits_per_table; ++offset) {
            const auto projection = table * m_options.bits_per_table + offset;
            const auto source_bit = projection * bit_count() / projection_count;
            const auto value = (words[source_bit / 64] >> (source_bit % 64)) & 1ULL;
            key |= value << offset;
        }
        return key;
    }

    void MultiProbeHammingIndex::add_to_tables(std::size_t position) {
        const auto* words = signature_words(position);
        for(std::size_t table = 0; table < m_tables.size(); ++table) {
            m_tables[table][bucket_key(table, words)].push_back(position);
            if(table == 0) {
                inject_failure(MultiProbeHammingIndexFaultPoint::AfterFirstTableInsert);
            }
        }
    }

    void MultiProbeHammingIndex::remove_from_tables(std::size_t position) {
        if(position >= m_records.size()) {
            return;
        }
        const auto* words = signature_words(position);
        for(std::size_t table = 0; table < m_tables.size(); ++table) {
            auto bucket = m_tables[table].find(bucket_key(table, words));
            if(bucket == m_tables[table].end()) {
                continue;
            }
            auto& postings = bucket->second;
            postings.erase(std::remove(postings.begin(), postings.end(), position), postings.end());
            if(postings.empty()) {
                m_tables[table].erase(bucket);
            }
        }
    }

    void MultiProbeHammingIndex::remove_one_posting(
        const std::size_t position,
        const std::uint64_t* words,
        const std::size_t table
    ) noexcept {
        auto bucket = m_tables[table].find(bucket_key(table, words));
        if(bucket == m_tables[table].end()) {
            return;
        }
        auto& postings = bucket->second;
        const auto posting = std::find(postings.begin(), postings.end(), position);
        if(posting != postings.end()) {
            postings.erase(posting);
        }
        if(postings.empty()) {
            m_tables[table].erase(bucket);
        }
    }

    void MultiProbeHammingIndex::rollback_staged_postings(
        const std::size_t position,
        const std::uint64_t* words,
        const std::size_t staged_tables
    ) noexcept {
        const auto count = std::min(staged_tables, m_tables.size());
        for(std::size_t table = 0; table < count; ++table) {
            auto bucket = m_tables[table].find(bucket_key(table, words));
            if(bucket == m_tables[table].end()) {
                continue;
            }
            auto& postings = bucket->second;
            // The replacement posting was appended after any old posting in
            // this bucket. Remove the newest occurrence so a same-key
            // replacement cannot erase the still-live original posting.
            const auto posting = std::find(
                postings.rbegin(),
                postings.rend(),
                position
            );
            if(posting != postings.rend()) {
                postings.erase(std::next(posting).base());
            }
            if(postings.empty()) {
                m_tables[table].erase(bucket);
            }
        }
    }

    void MultiProbeHammingIndex::set_failure_injection_for_testing(
        const MultiProbeHammingIndexFaultPoint point
    ) noexcept {
        m_failure = point;
    }

    void MultiProbeHammingIndex::inject_failure(
        const MultiProbeHammingIndexFaultPoint point
    ) {
        if(m_failure != point) {
            return;
        }
        m_failure = MultiProbeHammingIndexFaultPoint::None;
        throw std::runtime_error("injected MultiProbeHammingIndex failure");
    }

} // namespace agent_memory
