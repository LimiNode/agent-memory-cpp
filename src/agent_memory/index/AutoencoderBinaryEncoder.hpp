#pragma once
#ifndef AGENT_MEMORY_HEADER_INDEX_AUTOENCODER_BINARY_ENCODER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INDEX_AUTOENCODER_BINARY_ENCODER_HPP_INCLUDED

/// \file AutoencoderBinaryEncoder.hpp
/// \brief Inference contracts for a trained linear binary autoencoder artifact.

#include "IBinarySignatureEncoder.hpp"
#include "VectorSimilarityComputer.hpp"

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Immutable encoder weights exported by the offline autoencoder trainer.
    struct AutoencoderBinaryEncoderOptions final {
        /// \brief Dense input width accepted by the encoder.
        std::size_t input_dimension = 0;
        /// \brief Number of sign bits emitted by the encoder.
        std::size_t bit_count = 0;
        /// \brief Document-only training seed recorded by the artifact.
        std::uint64_t seed = 0;
        /// \brief SHA-256 of the JSON artifact that supplied these weights.
        std::string artifact_sha256;
        /// \brief Row-major `bit_count * input_dimension` encoder matrix.
        std::vector<float> weights;
        /// \brief One affine bias per output bit.
        std::vector<float> bias;
    };

    /// \brief Dependency-free inference encoder for `linear_binary_autoencoder_ste`.
    ///
    /// A bit is set when `dot(weight_row, input) + bias >= 0`. This matches the
    /// hard-code rule in `train-binary-autoencoder.py`; unlike its differentiable
    /// training surrogate, C++ inference is strictly deterministic.
    class AutoencoderBinaryEncoder final : public IBinarySignatureEncoder {
    public:
        explicit AutoencoderBinaryEncoder(AutoencoderBinaryEncoderOptions options);

        [[nodiscard]] const BinarySignatureEncoderInfo& info() const noexcept override;
        [[nodiscard]] BinarySignature encode(const Embedding& vector) const override;
        [[nodiscard]] std::vector<BinarySignature> encode_batch(
            const std::vector<Embedding>& vectors
        ) const override;

        /// \brief SIMD backend selected for affine projection dot products.
        [[nodiscard]] VectorSimilarityBackend similarity_backend() const noexcept;

    private:
        void validate_input(const Embedding& vector) const;
        [[nodiscard]] BinarySignature encode_validated(const Embedding& vector) const;

        AutoencoderBinaryEncoderOptions m_options;
        BinarySignatureEncoderInfo m_info;
        VectorSimilarityComputer m_similarity;
    };

    /// \brief Immutable optional decoder weights from the same autoencoder artifact.
    struct AutoencoderBinaryDecoderOptions final {
        /// \brief Width of the reconstructed dense vector.
        std::size_t output_dimension = 0;
        /// \brief Width of the input binary signature.
        std::size_t bit_count = 0;
        /// \brief Row-major `output_dimension * bit_count` decoder matrix.
        std::vector<float> weights;
        /// \brief One affine bias per reconstructed dimension.
        std::vector<float> bias;
    };

    /// \brief Reconstructs approximate dense vectors from packed binary signatures.
    ///
    /// This is an experimental evaluation path. Production-safe retrieval keeps
    /// original float vectors and uses binary signatures only for candidate
    /// selection followed by exact reranking.
    class AutoencoderBinaryDecoder final {
    public:
        explicit AutoencoderBinaryDecoder(AutoencoderBinaryDecoderOptions options);

        /// \brief Reconstructs `decoder(signatures)` using -1/+1 hard-code values.
        [[nodiscard]] Embedding decode(const BinarySignature& signature) const;

        [[nodiscard]] std::size_t output_dimension() const noexcept;
        [[nodiscard]] std::size_t bit_count() const noexcept;

    private:
        AutoencoderBinaryDecoderOptions m_options;
    };

} // namespace agent_memory

#endif
