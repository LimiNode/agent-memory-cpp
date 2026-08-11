#include "AsymmetricBinarySignatureScorer.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <utility>

namespace agent_memory {
    namespace {

        constexpr std::size_t kBitsPerByte = 8;
        constexpr std::size_t kBytesPerWord = sizeof(std::uint64_t);

        [[nodiscard]] std::size_t byte_count_for_bits(std::size_t bit_count) noexcept {
            return (bit_count + kBitsPerByte - 1U) / kBitsPerByte;
        }

        [[nodiscard]] std::uint8_t signature_byte(
            const BinarySignature& signature,
            std::size_t byte_index
        ) noexcept {
            const auto word_index = byte_index / kBytesPerWord;
            const auto byte_in_word = byte_index % kBytesPerWord;
            return static_cast<std::uint8_t>(
                (signature.words()[word_index] >> (byte_in_word * kBitsPerByte)) & 0xFFU
            );
        }

    } // namespace

    std::string_view asymmetric_binary_signature_scoring_backend_name(
        AsymmetricBinarySignatureScoringBackend backend
    ) noexcept {
        switch(backend) {
            case AsymmetricBinarySignatureScoringBackend::ScalarReference:
                return "scalar_reference";
            case AsymmetricBinarySignatureScoringBackend::ByteLookupTable:
                return "byte_lookup_table";
        }
        return {};
    }

    AsymmetricBinarySignatureScorer::AsymmetricBinarySignatureScorer(
        std::vector<float> query_affine_projections,
        AsymmetricBinarySignatureScoringBackend backend
    )
        : m_bit_count(query_affine_projections.size()),
          m_backend(backend),
          m_query_affine_projections(std::move(query_affine_projections)) {
        if(m_bit_count == 0) {
            throw std::invalid_argument("asymmetric query projections must not be empty");
        }
        for(const auto projection : m_query_affine_projections) {
            if(!std::isfinite(projection)) {
                throw std::invalid_argument("asymmetric query projections must be finite");
            }
        }
        if(m_backend == AsymmetricBinarySignatureScoringBackend::ScalarReference) {
            return;
        }
        if(m_backend != AsymmetricBinarySignatureScoringBackend::ByteLookupTable) {
            throw std::invalid_argument("unknown asymmetric binary signature scoring backend");
        }
        m_byte_lookup_tables.resize(byte_count_for_bits(m_bit_count));
        for(std::size_t byte_index = 0;
            byte_index < m_byte_lookup_tables.size();
            ++byte_index) {
            const auto first_bit = byte_index * kBitsPerByte;
            const auto bit_count = std::min(kBitsPerByte, m_bit_count - first_bit);
            auto& lookup = m_byte_lookup_tables[byte_index];
            for(std::size_t value = 0; value < lookup.size(); ++value) {
                float signed_sum = 0.0F;
                for(std::size_t bit = 0; bit < bit_count; ++bit) {
                    const auto projection = m_query_affine_projections[first_bit + bit];
                    signed_sum += (value & (std::size_t{1} << bit)) != 0U
                        ? projection
                        : -projection;
                }
                lookup[value] = signed_sum;
            }
        }
    }

    std::size_t AsymmetricBinarySignatureScorer::bit_count() const noexcept {
        return m_bit_count;
    }

    AsymmetricBinarySignatureScoringBackend
    AsymmetricBinarySignatureScorer::backend() const noexcept {
        return m_backend;
    }

    float AsymmetricBinarySignatureScorer::score(const BinarySignature& signature) const {
        if(signature.bit_count() != m_bit_count) {
            throw std::invalid_argument("asymmetric binary signature width mismatch");
        }
        if(m_backend == AsymmetricBinarySignatureScoringBackend::ScalarReference) {
            float signed_sum = 0.0F;
            for(std::size_t bit = 0; bit < m_bit_count; ++bit) {
                signed_sum += signature.bit(bit)
                    ? m_query_affine_projections[bit]
                    : -m_query_affine_projections[bit];
            }
            return signed_sum;
        }

        float signed_sum = 0.0F;
        for(std::size_t byte_index = 0;
            byte_index < m_byte_lookup_tables.size();
            ++byte_index) {
            signed_sum += m_byte_lookup_tables[byte_index][signature_byte(signature, byte_index)];
        }
        return signed_sum;
    }

} // namespace agent_memory
