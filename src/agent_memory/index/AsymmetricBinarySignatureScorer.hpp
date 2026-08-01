#pragma once
#ifndef AGENT_MEMORY_HEADER_INDEX_ASYMMETRIC_BINARY_SIGNATURE_SCORER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INDEX_ASYMMETRIC_BINARY_SIGNATURE_SCORER_HPP_INCLUDED

/// \file AsymmetricBinarySignatureScorer.hpp
/// \brief Query-conditioned scoring of packed binary document signatures.

#include "BinarySignature.hpp"

#include <array>
#include <cstddef>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Implementation selected for asymmetric binary-signature scoring.
    enum class AsymmetricBinarySignatureScoringBackend {
        /// \brief Per-bit direct floating-point implementation.
        ScalarReference,
        /// \brief Per-byte lookup table built once for each query projection vector.
        ByteLookupTable
    };

    /// \brief Stable diagnostic name for an asymmetric scoring backend.
    [[nodiscard]] std::string_view asymmetric_binary_signature_scoring_backend_name(
        AsymmetricBinarySignatureScoringBackend backend
    ) noexcept;

    /// \brief Scores packed document codes against continuous query projections.
    ///
    /// A set document bit contributes `+projection[bit]`; a cleared bit contributes
    /// `-projection[bit]`. The byte-LUT backend precomputes all 256 signed sums for
    /// every packed byte, then reads one table value per document-code byte.
    ///
    /// Both backends implement the same mathematical score. They use different
    /// floating-point reduction orders, so equal scores and candidate-boundary
    /// ordering are backend-specific for nearly equal fractional scores. Persisted
    /// experiment reports must identify the backend that produced their ranking.
    class AsymmetricBinarySignatureScorer final {
    public:
        /// \brief Creates a query-specific scorer for non-empty finite projections.
        explicit AsymmetricBinarySignatureScorer(
            std::vector<float> query_affine_projections,
            AsymmetricBinarySignatureScoringBackend backend =
                AsymmetricBinarySignatureScoringBackend::ByteLookupTable
        );

        /// \brief Number of signature bits accepted by score().
        [[nodiscard]] std::size_t bit_count() const noexcept;
        /// \brief Backend selected at construction.
        [[nodiscard]] AsymmetricBinarySignatureScoringBackend backend() const noexcept;

        /// \brief Computes a backend-specific floating-point asymmetric score.
        ///
        /// Scores from different backends are numerically equivalent within normal
        /// floating-point rounding error, but are not a bitwise ranking contract.
        /// \throws std::invalid_argument when `signature` has a different width.
        [[nodiscard]] float score(const BinarySignature& signature) const;

    private:
        std::size_t m_bit_count = 0;
        AsymmetricBinarySignatureScoringBackend m_backend =
            AsymmetricBinarySignatureScoringBackend::ByteLookupTable;
        std::vector<float> m_query_affine_projections;
        std::vector<std::array<float, 256>> m_byte_lookup_tables;
    };

} // namespace agent_memory

#endif
