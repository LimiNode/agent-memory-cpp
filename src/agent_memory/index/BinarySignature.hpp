#pragma once
#ifndef AGENT_MEMORY_HEADER_INDEX_BINARY_SIGNATURE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INDEX_BINARY_SIGNATURE_HPP_INCLUDED

/// \file BinarySignature.hpp
/// \brief Packed binary signatures and diagnostic metrics for candidate filters.

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Returns the number of 64-bit storage words needed for `bit_count`.
    [[nodiscard]] std::size_t binary_signature_word_count(std::size_t bit_count) noexcept;

    /// \brief Packed binary code used by approximate candidate filters.
    class BinarySignature final {
    public:
        BinarySignature() = default;
        explicit BinarySignature(std::size_t bit_count);
        BinarySignature(std::size_t bit_count, std::vector<std::uint64_t> words);

        /// \brief Number of meaningful bits in the signature.
        [[nodiscard]] std::size_t bit_count() const noexcept;

        /// \brief Number of 64-bit words in the packed representation.
        [[nodiscard]] std::size_t word_count() const noexcept;

        /// \brief Returns true when the signature has no bits.
        [[nodiscard]] bool empty() const noexcept;

        /// \brief Reads one bit by zero-based index.
        [[nodiscard]] bool bit(std::size_t index) const;

        /// \brief Sets or clears one bit by zero-based index.
        void set_bit(std::size_t index, bool value = true);

        /// \brief Canonical packed words. Unused tail bits are always zero.
        [[nodiscard]] const std::vector<std::uint64_t>& words() const noexcept;

        friend bool operator==(
            const BinarySignature& lhs,
            const BinarySignature& rhs
        ) noexcept;
        friend bool operator!=(
            const BinarySignature& lhs,
            const BinarySignature& rhs
        ) noexcept;

    private:
        static void validate_words(std::size_t bit_count, const std::vector<std::uint64_t>& words);

        std::size_t m_bit_count = 0;
        std::vector<std::uint64_t> m_words;
    };

    /// \brief Runtime-selected implementation used for packed Hamming distance.
    enum class HammingDistanceBackend {
        LookupTable,
        HardwarePopcount,
        Avx2Simd
    };

    /// \brief Stable diagnostic name for a Hamming-distance backend.
    [[nodiscard]] std::string_view hamming_distance_backend_name(
        HammingDistanceBackend backend
    ) noexcept;

    /// \brief Returns whether this build and CPU can execute `backend` safely.
    [[nodiscard]] bool hamming_distance_backend_supported(
        HammingDistanceBackend backend
    ) noexcept;

    /// \brief Reusable same-width Hamming-distance kernel.
    ///
    /// Backend selection happens once at construction and accounts for both CPU
    /// capabilities and signature width. Batch scans avoid validation and
    /// dispatch inside the per-record hot loop.
    class HammingDistanceComputer final {
    public:
        explicit HammingDistanceComputer(std::size_t word_count) noexcept;
        /// \brief Uses an explicitly selected backend for tests and diagnostics.
        /// \throws std::invalid_argument when the backend is unavailable.
        HammingDistanceComputer(
            std::size_t word_count,
            HammingDistanceBackend backend
        );

        [[nodiscard]] std::size_t word_count() const noexcept;
        [[nodiscard]] HammingDistanceBackend backend() const noexcept;

        /// \brief Computes one distance over exactly `word_count()` packed words.
        /// \pre Both pointers address at least `word_count()` words.
        [[nodiscard]] std::size_t distance_words(
            const std::uint64_t* lhs,
            const std::uint64_t* rhs
        ) const noexcept;

        /// \brief Computes distances from one query to contiguous row-major signatures.
        /// \param query_words Query row containing `word_count()` words.
        /// \param record_words `record_count` consecutive rows of `word_count()` words.
        /// \param record_count Number of record rows.
        /// \param output_distances Output array containing at least `record_count` entries.
        void compute_distances(
            const std::uint64_t* query_words,
            const std::uint64_t* record_words,
            std::size_t record_count,
            std::size_t* output_distances
        ) const noexcept;

    private:
        using SingleKernel = std::size_t (*)(
            const std::uint64_t*,
            const std::uint64_t*,
            std::size_t
        ) noexcept;
        using BatchKernel = void (*)(
            const std::uint64_t*,
            const std::uint64_t*,
            std::size_t,
            std::size_t,
            std::size_t*
        ) noexcept;

        std::size_t m_word_count = 0;
        HammingDistanceBackend m_backend = HammingDistanceBackend::LookupTable;
        SingleKernel m_single_kernel = nullptr;
        BatchKernel m_batch_kernel = nullptr;
    };

    /// \brief Hamming distance between two equal-width binary signatures.
    ///
    /// Uses the best available implementation for the current build and CPU
    /// support, falling back to a portable lookup-table implementation.
    [[nodiscard]] std::size_t hamming_distance(
        const BinarySignature& lhs,
        const BinarySignature& rhs
    );

    /// \brief Options for binary-code health analysis.
    struct BinaryCodeHealthOptions final {
        /// \brief Maximum deterministic pair samples used for pairwise Hamming.
        std::size_t max_pairwise_samples = 4096;
    };

    /// \brief Diagnostics that reveal degenerate or unbalanced binary codes.
    struct BinaryCodeHealthMetrics final {
        /// \brief Number of signatures included in the diagnostic pass.
        std::size_t signature_count = 0;
        /// \brief Shared width of every signature.
        std::size_t bit_count = 0;
        /// \brief Per-bit occupancy, where `0.0` means always zero and `1.0` always one.
        std::vector<double> fraction_ones_per_bit;
        /// \brief Fraction of bit positions whose occupancy is exactly `0.0` or `1.0`.
        double constant_bit_fraction = 0.0;
        /// \brief Mean normalized binary entropy across bit positions.
        double mean_bit_entropy = 0.0;
        /// \brief Minimum normalized binary entropy across bit positions.
        double min_bit_entropy = 0.0;
        /// \brief Maximum normalized binary entropy across bit positions.
        double max_bit_entropy = 0.0;
        /// \brief Fraction of signatures beyond the unique-signature count.
        double duplicate_signature_rate = 0.0;
        /// \brief Mean Hamming distance across all pairs or a deterministic sample.
        double sampled_mean_pairwise_hamming_distance = 0.0;
        /// \brief Population standard deviation of sampled pairwise Hamming distances.
        double sampled_pairwise_hamming_distance_stddev = 0.0;
        /// \brief Smallest Hamming distance observed in the sampled pairs.
        std::size_t sampled_min_pairwise_hamming_distance = 0;
        /// \brief Largest Hamming distance observed in the sampled pairs.
        std::size_t sampled_max_pairwise_hamming_distance = 0;
        /// \brief Number of pairs used for sampled_mean_pairwise_hamming_distance.
        std::size_t sampled_pair_count = 0;
        /// \brief Descending sizes of exact-signature buckets.
        std::vector<std::size_t> exact_signature_bucket_sizes;
    };

    /// \brief Computes deterministic health diagnostics for same-width signatures.
    [[nodiscard]] BinaryCodeHealthMetrics analyze_binary_code_health(
        const std::vector<BinarySignature>& signatures,
        BinaryCodeHealthOptions options = {}
    );

} // namespace agent_memory

#endif
