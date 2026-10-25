#pragma once
#ifndef AGENT_MEMORY_HEADER_LEXICAL_FLAT_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_LEXICAL_FLAT_LEXICAL_INDEX_HPP_INCLUDED

/// \file FlatLexicalIndex.hpp
/// \brief Dependency-free flat lexical postings and statistics state.

#include "Lexical.hpp"
#include "PersistentLexicalIndex.hpp"

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace agent_memory {

    /// \brief One normalized unit ready for the flat lexical backend.
    struct FlatLexicalUnitInput final {
        ScopeId scope_id;
        KnowledgeUnitId unit_id;
        ProjectionKind projection_kind = ProjectionKind::Original;
        ResourceId resource_id;
        std::uint64_t resource_generation = 0;
        std::uint64_t unit_revision = 0;
        ProjectionVersionRef projection_version;
        std::vector<std::string> normalized_tokens;
    };

    /// \brief Expected active identity used to guard a replacement.
    struct FlatLexicalExpectedState final {
        KnowledgeUnitId unit_id;
        std::uint64_t resource_generation = 0;
        std::uint64_t unit_revision = 0;
        ProjectionVersionRef projection_version;
    };

    /// \brief Deterministic failure points used by conformance tests.
    enum class FlatLexicalUpsertFailurePoint : std::uint8_t {
        None,
        AfterTokenPreparation,
        AfterDictionaryApplication,
        AfterPostingApplication
    };

    /// \brief One active token occurrence aggregate for a unit.
    struct FlatLexicalPosting final {
        TokenId token_id;
        KnowledgeUnitId unit_id;
        ResourceId resource_id;
        std::uint64_t resource_generation = 0;
        ProjectionVersionRef projection_version;
        std::uint32_t term_frequency = 0;
    };

    /// \brief One deterministic, bounded partition of a token posting list.
    ///
    /// Segments are a dependency-free preparation contract for the durable
    /// posting-row layout.  They are immutable observations of one active
    /// statistics generation; the segment id is local to the token and starts
    /// at zero for the first ordered partition.  This type does not imply
    /// durable publication or a compressed wire format.
    struct FlatLexicalPostingSegment final {
        LexicalStatisticsEpoch statistics_epoch;
        std::uint64_t statistics_generation = 0;
        std::uint32_t posting_layout_version = 0;
        TokenId token_id;
        std::uint32_t segment_id = 0;
        std::vector<FlatLexicalPosting> postings;
    };

    /// \brief Trusted expectations used to validate one reconstructed posting partition.
    ///
    /// The expected posting records are an independent witness supplied by a
    /// caller that already owns the authoritative logical view. A future
    /// storage reader may replace that witness with an equivalent digest, but
    /// a count derived only from received segments is not sufficient evidence
    /// of complete membership.
    struct FlatLexicalPostingPartitionExpectation final {
        LexicalStatisticsEpoch statistics_epoch;
        std::uint64_t statistics_generation = 0;
        std::uint32_t posting_layout_version = 0;
        TokenId token_id;
        std::size_t expected_segment_count = 0;
        std::size_t expected_posting_count = 0;
        std::size_t max_postings_per_segment = 0;
        std::vector<FlatLexicalPosting> expected_postings;
    };

    /// \brief Admission result for a reconstructed posting partition.
    enum class FlatLexicalPostingPartitionReadStatus : std::uint8_t {
        Complete,
        ConfirmedEmpty,
        InvalidExpectation,
        SegmentCountMismatch,
        DuplicateSegmentId,
        OutOfOrderSegmentId,
        MetadataMismatch,
        InvalidSegment,
        SegmentLimitExceeded,
        SegmentLayoutMismatch,
        PostingCountMismatch,
        PostingOrderMismatch,
        MembershipMismatch
    };

    /// \brief Reconstructed postings and their fail-closed admission status.
    struct FlatLexicalPostingPartitionRead final {
        FlatLexicalPostingPartitionReadStatus status =
            FlatLexicalPostingPartitionReadStatus::InvalidExpectation;
        std::vector<FlatLexicalPosting> postings;

        [[nodiscard]] bool complete() const noexcept {
            return status == FlatLexicalPostingPartitionReadStatus::Complete;
        }

        [[nodiscard]] bool confirmed_empty() const noexcept {
            return status == FlatLexicalPostingPartitionReadStatus::ConfirmedEmpty;
        }
    };

    /// \brief Statistics for one active indexed unit.
    struct FlatLexicalUnitStats final {
        KnowledgeUnitId unit_id;
        ResourceId resource_id;
        std::uint64_t resource_generation = 0;
        std::uint64_t unit_revision = 0;
        ProjectionVersionRef projection_version;
        std::uint32_t token_count = 0;
        std::uint32_t unique_token_count = 0;
    };

    /// \brief Aggregate document and collection frequency for one token.
    struct FlatLexicalTokenStats final {
        TokenId token_id;
        std::uint64_t document_frequency = 0;
        std::uint64_t collection_frequency = 0;
    };

    /// \brief Aggregate statistics for one active lexical snapshot.
    struct FlatLexicalCollectionStats final {
        std::uint64_t unit_count = 0;
        std::uint64_t total_token_count = 0;
    };

    /// \brief Flat logical postings backend used before a durable storage adapter.
    ///
    /// This class owns one projection and one immutable descriptor. It is a
    /// dependency-free conformance baseline: postings are flat maps, token ids
    /// are never reused, and all aggregate counters describe the current active
    /// unit set. It does not provide MDBX durability or a publication transaction.
    class FlatLexicalIndex final {
    public:
        explicit FlatLexicalIndex(
            LexicalIndexDescriptor descriptor,
            std::uint32_t posting_layout_version = 0
        );

        [[nodiscard]] const LexicalIndexDescriptor& descriptor() const noexcept;
        [[nodiscard]] const LexicalActiveSnapshot& active_snapshot() const noexcept;
        [[nodiscard]] std::size_t size() const noexcept;

        /// \brief Restores durable snapshot metadata after logical rows are reloaded.
        ///
        /// This is used by storage adapters after replaying the persisted unit
        /// inputs. It never changes postings or statistics and rejects an epoch
        /// or layout that is incompatible with this index descriptor.
        void restore_active_snapshot_for_storage(
            LexicalActiveSnapshot snapshot
        );

        /// \brief Restores the durable token dictionary and allocator frontier.
        ///
        /// Storage adapters call this on a fresh index before replaying active
        /// units. Historical dictionary entries are retained even when no
        /// active posting currently references them, so token ids remain
        /// monotonic and are never reused after erase or clear.
        void restore_token_dictionary_for_storage(
            std::vector<std::pair<std::string, TokenId>> entries,
            TokenId next_token_id
        );

        [[nodiscard]] std::vector<std::pair<std::string, TokenId>>
        token_dictionary_for_storage() const;

        [[nodiscard]] TokenId next_token_id_for_storage() const noexcept;

        /// \brief Inserts or replaces one active unit contribution.
        /// \throws std::invalid_argument when the input is not normalized or
        /// does not match this index descriptor.
        /// \throws std::runtime_error when revision admission fails.
        /// \throws std::overflow_error when the token or generation space is exhausted.
        void upsert(
            FlatLexicalUnitInput input,
            std::optional<FlatLexicalExpectedState> expected_current = std::nullopt
        );

        /// \brief Arms one deterministic failure for exception-safety tests.
        /// \note This hook is dependency-free and has no effect unless armed.
        void set_failure_injection_for_testing(
            FlatLexicalUpsertFailurePoint point
        ) noexcept;

        /// \brief Removes one active unit contribution idempotently.
        [[nodiscard]] bool erase(const KnowledgeUnitId& unit_id);

        /// \brief Removes all active contributions while retaining token ids.
        void clear();

        [[nodiscard]] std::optional<FlatLexicalUnitStats> find_unit_stats(
            const KnowledgeUnitId& unit_id
        ) const;

        [[nodiscard]] std::optional<TokenId> find_token_id(
            const std::string& normalized_token
        ) const;

        [[nodiscard]] std::optional<FlatLexicalTokenStats> find_token_stats(
            const TokenId& token_id
        ) const;

        [[nodiscard]] std::vector<FlatLexicalPosting> postings(
            const TokenId& token_id
        ) const;

        /// \brief Partitions one ordered posting list into bounded segments.
        ///
        /// The returned partitions preserve posting order and collectively
        /// contain exactly the current active posting list.  A zero limit is
        /// rejected; an unknown token returns an empty vector.
        [[nodiscard]] std::vector<FlatLexicalPostingSegment> posting_segments(
            const TokenId& token_id,
            std::size_t max_postings_per_segment
        ) const;

        [[nodiscard]] FlatLexicalCollectionStats collection_stats() const noexcept;

    private:
        struct UnitState final {
            FlatLexicalUnitStats stats;
            std::map<TokenId, std::uint32_t> term_frequencies;
            std::vector<std::string> normalized_tokens;
        };

        void remove_unit(const KnowledgeUnitId& unit_id);
        void advance_generation();

        LexicalIndexDescriptor m_descriptor;
        LexicalActiveSnapshot m_active_snapshot;
        std::uint64_t m_next_token_id = 1;
        std::map<std::string, TokenId> m_token_by_text;
        std::map<TokenId, std::string> m_text_by_token;
        std::map<KnowledgeUnitId, UnitState> m_units;
        std::map<TokenId, std::map<KnowledgeUnitId, FlatLexicalPosting>> m_postings;
        std::map<TokenId, FlatLexicalTokenStats> m_token_stats;
        FlatLexicalCollectionStats m_collection_stats;
        FlatLexicalUpsertFailurePoint m_failure_injection =
            FlatLexicalUpsertFailurePoint::None;
    };

    [[nodiscard]] bool is_valid_flat_lexical_unit_input(
        const FlatLexicalUnitInput& input
    ) noexcept;

    [[nodiscard]] bool is_valid_flat_lexical_posting_segment(
        const FlatLexicalPostingSegment& segment
    ) noexcept;

    /// \brief Reconstructs one complete posting partition from received segments.
    ///
    /// This is a dependency-free, fail-closed reader admission helper. It does
    /// not claim a storage snapshot or route completion; callers must supply
    /// trusted epoch/generation/layout metadata and an independent posting
    /// witness in the expectation. No partial postings are returned on failure.
    [[nodiscard]] FlatLexicalPostingPartitionRead
    reconstruct_flat_lexical_posting_partition(
        const std::vector<FlatLexicalPostingSegment>& segments,
        const FlatLexicalPostingPartitionExpectation& expectation
    );

} // namespace agent_memory

#endif
