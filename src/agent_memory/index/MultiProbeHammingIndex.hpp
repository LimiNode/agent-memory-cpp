#pragma once
#ifndef AGENT_MEMORY_HEADER_INDEX_MULTI_PROBE_HAMMING_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INDEX_MULTI_PROBE_HAMMING_INDEX_HPP_INCLUDED

/// \file MultiProbeHammingIndex.hpp
/// \brief Bucketed in-memory candidate index for packed binary signatures.

#include "IBinarySignatureIndex.hpp"

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <unordered_map>
#include <vector>

namespace agent_memory {

    /// \brief Deterministic failure points for exception-safety tests.
    enum class MultiProbeHammingIndexFaultPoint : std::uint8_t {
        None = 0,
        AfterRecordAppendBeforeSignatureStorage,
        AfterSignatureStorageBeforeTables,
        AfterFirstTableInsert
    };

    /// \brief Configuration for deterministic multi-probe Hamming buckets.
    struct MultiProbeHammingIndexOptions final {
        /// \brief Binary-space identity accepted by this index.
        std::optional<BinarySignatureInfo> signature_info;
        /// \brief Number of independent projected-bit hash tables.
        std::size_t table_count = 8;
        /// \brief Projected bits per table. Must not exceed 63.
        std::size_t bits_per_table = 8;
        /// \brief Maximum Hamming radius probed inside each table key.
        std::size_t max_probe_radius = 1;
        /// \brief Candidate target relative to requested result count.
        std::size_t candidate_multiplier = 64;
        /// \brief Absolute candidate target before probing may stop early.
        std::size_t minimum_candidate_count = 128;
    };

    /// \brief Candidate-generation diagnostics for one multi-probe search.
    struct MultiProbeHammingSearchResult final {
        std::vector<BinarySignatureSearchResult> results;
        /// \brief Unique probed candidates remaining after metadata filters.
        std::size_t candidate_count = 0;
        /// \brief Number of table buckets looked up, including empty buckets.
        std::size_t probed_bucket_count = 0;
        /// \brief Number of posting entries visited before deduplication.
        std::size_t visited_posting_count = 0;
    };

    /// \brief Approximate Hamming index using deterministic projected-bit buckets.
    ///
    /// Each table projects a disjoint, evenly spaced subset of signature bits.
    /// Query-time multi-probe enumerates nearby table keys until the configured
    /// candidate target or radius limit is reached, then ranks only the union of
    /// those candidates by exact Hamming distance. The index may return fewer
    /// than `query.limit` results when the bounded probes find too few records.
    /// Selective buckets can reduce work below a flat scan, but adversarial or
    /// low-selectivity codes provide no worst-case sub-linear guarantee.
    class MultiProbeHammingIndex final : public IBinarySignatureIndex {
    public:
        MultiProbeHammingIndex();
        explicit MultiProbeHammingIndex(MultiProbeHammingIndexOptions options);

        [[nodiscard]] std::size_t bit_count() const noexcept override;
        [[nodiscard]] std::size_t size() const noexcept override;

        void upsert(BinarySignatureRecord record) override;
        [[nodiscard]] std::optional<BinarySignatureRecord> find(
            const ChunkId& chunk_id
        ) const override;
        [[nodiscard]] std::vector<BinarySignatureSearchResult> search(
            const BinarySignatureSearchQuery& query
        ) const override;
        [[nodiscard]] MultiProbeHammingSearchResult search_with_diagnostics(
            const BinarySignatureSearchQuery& query
        ) const;
        [[nodiscard]] bool erase(const ChunkId& chunk_id) override;
        void clear() override;

        /// \brief Inject one failure into the next matching insertion phase.
        /// \note Testing hook; production callers must leave it at None.
        void set_failure_injection_for_testing(
            MultiProbeHammingIndexFaultPoint point
        ) noexcept;

    private:
        struct StoredRecord final {
            ChunkId chunk_id;
            Metadata metadata;
        };

        using Bucket = std::vector<std::size_t>;
        using Table = std::unordered_map<std::uint64_t, Bucket>;

        void initialize_for_identity();
        void validate_options() const;
        void validate_record(const BinarySignatureRecord& record) const;
        void validate_query(const BinarySignatureSearchQuery& query) const;
        void require_matching_identity(const BinarySignatureInfo& info) const;
        [[nodiscard]] const std::uint64_t* signature_words(std::size_t position) const noexcept;
        [[nodiscard]] std::uint64_t* signature_words(std::size_t position) noexcept;
        [[nodiscard]] std::uint64_t bucket_key(
            std::size_t table,
            const std::uint64_t* words
        ) const noexcept;
        void add_to_tables(std::size_t position);
        void remove_from_tables(std::size_t position);
        void remove_one_posting(
            std::size_t position,
            const std::uint64_t* words,
            std::size_t table
        ) noexcept;
        void rollback_staged_postings(
            std::size_t position,
            const std::uint64_t* words,
            std::size_t staged_tables
        ) noexcept;
        void inject_failure(MultiProbeHammingIndexFaultPoint point);

        MultiProbeHammingIndexOptions m_options;
        std::vector<StoredRecord> m_records;
        std::vector<std::uint64_t> m_signature_words;
        std::map<ChunkId, std::size_t> m_positions;
        std::vector<Table> m_tables;
        std::optional<HammingDistanceComputer> m_distance;
        MultiProbeHammingIndexFaultPoint m_failure =
            MultiProbeHammingIndexFaultPoint::None;
    };

} // namespace agent_memory

#endif
