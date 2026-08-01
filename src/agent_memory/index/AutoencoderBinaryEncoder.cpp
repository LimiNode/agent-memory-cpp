#include "AutoencoderBinaryEncoder.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <system_error>
#include <utility>

namespace agent_memory {
    namespace {

        [[nodiscard]] std::size_t checked_value_count(
            std::size_t row_count,
            std::size_t row_width,
            const char* description
        ) {
            if(row_count == 0 || row_width == 0) {
                return 0;
            }
            if(row_count > std::numeric_limits<std::size_t>::max() / row_width) {
                throw std::length_error(std::string{description} + " size overflows size_t");
            }
            return row_count * row_width;
        }

        void append_integer(std::string& output, std::size_t value) {
            std::array<char, 32> buffer{};
            const auto result = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value);
            if(result.ec != std::errc{}) {
                throw std::logic_error("failed to format autoencoder encoder fingerprint");
            }
            output.append(buffer.data(), result.ptr);
        }

        [[nodiscard]] std::string make_fingerprint(
            const AutoencoderBinaryEncoderOptions& options
        ) {
            std::string output = options.encoder_id;
            output += "_";
            output += options.encoder_version;
            output += ":sha256=";
            output += options.artifact_sha256;
            output += ":dim=";
            append_integer(output, options.input_dimension);
            output += ":bits=";
            append_integer(output, options.bit_count);
            if(options.input_transform == AutoencoderBinaryInputTransform::ClipMinusOneToOne) {
                output += ":input=clip_minus_one_one";
            }
            return output;
        }

        void validate_finite(const std::vector<float>& values, const char* description) {
            for(const auto value : values) {
                if(!std::isfinite(value)) {
                    throw std::invalid_argument(std::string{description} + " must be finite");
                }
            }
        }

        void validate_sha256(std::string_view value) {
            if(value.size() != 64) {
                throw std::invalid_argument("autoencoder artifact SHA-256 must be 64 lowercase hex characters");
            }
            for(const auto character : value) {
                if((character < '0' || character > '9') &&
                   (character < 'a' || character > 'f')) {
                    throw std::invalid_argument(
                        "autoencoder artifact SHA-256 must be 64 lowercase hex characters"
                    );
                }
            }
        }

        void validate_encoder_options(const AutoencoderBinaryEncoderOptions& options) {
            if(options.input_dimension == 0 || options.bit_count == 0) {
                throw std::invalid_argument("autoencoder encoder dimensions must be positive");
            }
            if(options.encoder_id.empty() || options.encoder_version.empty()) {
                throw std::invalid_argument("autoencoder encoder identity must not be empty");
            }
            validate_sha256(options.artifact_sha256);
            if(options.weights.size() != checked_value_count(
                   options.bit_count,
                   options.input_dimension,
                   "autoencoder encoder matrix"
               )) {
                throw std::invalid_argument("autoencoder encoder weight matrix size mismatch");
            }
            if(options.bias.size() != options.bit_count) {
                throw std::invalid_argument("autoencoder encoder bias count mismatch");
            }
            validate_finite(options.weights, "autoencoder encoder weights");
            validate_finite(options.bias, "autoencoder encoder bias");
        }

        void validate_decoder_options(const AutoencoderBinaryDecoderOptions& options) {
            if(options.output_dimension == 0 || options.bit_count == 0) {
                throw std::invalid_argument("autoencoder decoder dimensions must be positive");
            }
            if(options.weights.size() != checked_value_count(
                   options.output_dimension,
                   options.bit_count,
                   "autoencoder decoder matrix"
               )) {
                throw std::invalid_argument("autoencoder decoder weight matrix size mismatch");
            }
            if(options.bias.size() != options.output_dimension) {
                throw std::invalid_argument("autoencoder decoder bias count mismatch");
            }
            validate_finite(options.weights, "autoencoder decoder weights");
            validate_finite(options.bias, "autoencoder decoder bias");
        }

    } // namespace

    AutoencoderBinaryEncoder::AutoencoderBinaryEncoder(AutoencoderBinaryEncoderOptions options)
        : m_options(std::move(options)) {
        validate_encoder_options(m_options);
        m_info.encoder_id = m_options.encoder_id;
        m_info.encoder_version = m_options.encoder_version;
        m_info.input_dimension = m_options.input_dimension;
        m_info.bit_count = m_options.bit_count;
        m_info.seed = m_options.seed;
        m_info.config_fingerprint = make_fingerprint(m_options);
    }

    const BinarySignatureEncoderInfo& AutoencoderBinaryEncoder::info() const noexcept {
        return m_info;
    }

    BinarySignature AutoencoderBinaryEncoder::encode(const Embedding& vector) const {
        validate_input(vector);
        return encode_validated(vector);
    }

    std::vector<BinarySignature> AutoencoderBinaryEncoder::encode_batch(
        const std::vector<Embedding>& vectors
    ) const {
        for(const auto& vector : vectors) {
            validate_input(vector);
        }
        std::vector<BinarySignature> output;
        output.reserve(vectors.size());
        for(const auto& vector : vectors) {
            output.push_back(encode_validated(vector));
        }
        return output;
    }

    std::vector<float> AutoencoderBinaryEncoder::affine_projections(
        const Embedding& vector
    ) const {
        validate_input(vector);
        return affine_projections_validated(vector);
    }

    VectorSimilarityBackend AutoencoderBinaryEncoder::similarity_backend() const noexcept {
        return m_similarity.backend();
    }

    void AutoencoderBinaryEncoder::validate_input(const Embedding& vector) const {
        if(vector.dimension() != m_options.input_dimension) {
            throw std::invalid_argument("autoencoder encoder input dimension mismatch");
        }
        validate_finite(vector.values, "autoencoder encoder input");
    }

    BinarySignature AutoencoderBinaryEncoder::encode_validated(const Embedding& vector) const {
        BinarySignature output(m_options.bit_count);
        const auto projections = affine_projections_validated(vector);
        for(std::size_t bit = 0; bit < m_options.bit_count; ++bit) {
            output.set_bit(bit, projections[bit] >= 0.0F);
        }
        return output;
    }

    std::vector<float> AutoencoderBinaryEncoder::affine_projections_validated(
        const Embedding& vector
    ) const {
        std::vector<float> output(m_options.bit_count);
        for(std::size_t bit = 0; bit < m_options.bit_count; ++bit) {
            float dot = 0.0F;
            const auto* weights = m_options.weights.data() + bit * m_options.input_dimension;
            if(m_options.input_transform == AutoencoderBinaryInputTransform::Identity) {
                dot = m_similarity.dot_product_values(
                    vector.values.data(),
                    weights,
                    m_options.input_dimension
                );
            } else {
                for(std::size_t dimension = 0;
                    dimension < m_options.input_dimension;
                    ++dimension) {
                    dot += std::max(-1.0F, std::min(1.0F, vector.values[dimension])) *
                        weights[dimension];
                }
            }
            output[bit] = dot + m_options.bias[bit];
        }
        return output;
    }

    AutoencoderBinaryDecoder::AutoencoderBinaryDecoder(AutoencoderBinaryDecoderOptions options)
        : m_options(std::move(options)) {
        validate_decoder_options(m_options);
    }

    Embedding AutoencoderBinaryDecoder::decode(const BinarySignature& signature) const {
        if(signature.bit_count() != m_options.bit_count) {
            throw std::invalid_argument("autoencoder decoder signature width mismatch");
        }
        Embedding output;
        output.values.resize(m_options.output_dimension);
        for(std::size_t output_dimension = 0;
            output_dimension < m_options.output_dimension;
            ++output_dimension) {
            auto value = m_options.bias[output_dimension];
            const auto* row =
                m_options.weights.data() + output_dimension * m_options.bit_count;
            for(std::size_t bit = 0; bit < m_options.bit_count; ++bit) {
                const auto bit_value = m_options.code_value_encoding ==
                    AutoencoderBinaryCodeValueEncoding::NegativeOneToOne
                    ? (signature.bit(bit) ? 1.0F : -1.0F)
                    : (signature.bit(bit) ? 1.0F : 0.0F);
                value += row[bit] * bit_value;
            }
            output.values[output_dimension] = m_options.activation ==
                AutoencoderBinaryDecoderActivation::HyperbolicTangent
                ? std::tanh(value)
                : value;
        }
        return output;
    }

    std::size_t AutoencoderBinaryDecoder::output_dimension() const noexcept {
        return m_options.output_dimension;
    }

    std::size_t AutoencoderBinaryDecoder::bit_count() const noexcept {
        return m_options.bit_count;
    }

} // namespace agent_memory
