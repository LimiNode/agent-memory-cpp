#include <agent_memory/index/AsymmetricBinarySignatureScorer.hpp>

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    template <typename Function>
    bool throws_invalid_argument(Function&& function) {
        try {
            function();
        } catch(const std::invalid_argument&) {
            return true;
        }
        return false;
    }

    agent_memory::BinarySignature make_signature(std::size_t bit_count, std::size_t value) {
        agent_memory::BinarySignature signature(bit_count);
        for(std::size_t bit = 0; bit < bit_count; ++bit) {
            signature.set_bit(bit, (value & (std::size_t{1} << bit)) != 0U);
        }
        return signature;
    }

    struct ScoredSignature final {
        std::size_t value = 0;
        float score = 0.0F;
    };

} // namespace

int main() {
    using agent_memory::AsymmetricBinarySignatureScorer;
    using agent_memory::AsymmetricBinarySignatureScoringBackend;

    if(agent_memory::asymmetric_binary_signature_scoring_backend_name(
           AsymmetricBinarySignatureScoringBackend::ScalarReference
       ) != "scalar_reference" ||
       agent_memory::asymmetric_binary_signature_scoring_backend_name(
           AsymmetricBinarySignatureScoringBackend::ByteLookupTable
       ) != "byte_lookup_table") {
        return fail("asymmetric scorer backend names");
    }
    if(!throws_invalid_argument([] {
           (void)AsymmetricBinarySignatureScorer({});
       }) ||
       !throws_invalid_argument([] {
           (void)AsymmetricBinarySignatureScorer({
               std::numeric_limits<float>::quiet_NaN(),
           });
       }) ||
       !throws_invalid_argument([] {
           (void)AsymmetricBinarySignatureScorer(
               {1.0F},
               static_cast<AsymmetricBinarySignatureScoringBackend>(42)
           );
       })) {
        return fail("asymmetric scorer construction validation");
    }

    const std::vector<float> projections{
        1.0F, 2.0F, 4.0F, 8.0F, 16.0F,
        32.0F, 64.0F, 128.0F, 256.0F, 512.0F,
    };
    const AsymmetricBinarySignatureScorer scalar(
        projections,
        AsymmetricBinarySignatureScoringBackend::ScalarReference
    );
    const AsymmetricBinarySignatureScorer byte_lut(projections);
    if(scalar.bit_count() != projections.size() ||
       byte_lut.backend() != AsymmetricBinarySignatureScoringBackend::ByteLookupTable) {
        return fail("asymmetric scorer metadata");
    }

    for(std::size_t value = 0; value < 1024U; ++value) {
        const auto signature = make_signature(projections.size(), value);
        const auto scalar_score = scalar.score(signature);
        const auto byte_lut_score = byte_lut.score(signature);
        if(scalar_score != byte_lut_score) {
            return fail("byte LUT must reproduce exact binary-power scalar scores");
        }
    }

    const agent_memory::BinarySignature wrong_width(9);
    if(!throws_invalid_argument([&] {
           (void)scalar.score(wrong_width);
       })) {
        return fail("asymmetric scorer width validation");
    }

    const std::vector<float> fractional_projections{
        -1.25F, 0.75F, 2.5F, -0.125F, 0.625F, 1.75F, -3.25F,
        0.375F, -0.875F, 1.125F, 2.25F, -0.5F, 0.0625F,
    };
    const AsymmetricBinarySignatureScorer fractional_scalar(
        fractional_projections,
        AsymmetricBinarySignatureScoringBackend::ScalarReference
    );
    const AsymmetricBinarySignatureScorer fractional_byte_lut(fractional_projections);
    constexpr float kMaximumFractionalScoreError = 2.0e-6F;
    for(std::size_t value = 0; value < 8192U; ++value) {
        const auto signature = make_signature(fractional_projections.size(), value);
        if(std::fabs(
               fractional_scalar.score(signature) - fractional_byte_lut.score(signature)
           ) > kMaximumFractionalScoreError) {
            return fail("byte LUT must numerically match fractional scalar scores");
        }
    }

    // Scores farther apart than accumulated rounding error must keep their order;
    // exact equality for nearly tied fractional scores is intentionally not part
    // of the scalar/LUT contract.
    const std::vector<ScoredSignature> fractional_candidates{
        {0U, fractional_scalar.score(make_signature(fractional_projections.size(), 0U))},
        {87U, fractional_scalar.score(make_signature(fractional_projections.size(), 87U))},
        {1903U, fractional_scalar.score(make_signature(fractional_projections.size(), 1903U))},
        {4095U, fractional_scalar.score(make_signature(fractional_projections.size(), 4095U))},
        {6211U, fractional_scalar.score(make_signature(fractional_projections.size(), 6211U))},
        {8191U, fractional_scalar.score(make_signature(fractional_projections.size(), 8191U))},
    };
    for(std::size_t lhs = 0; lhs < fractional_candidates.size(); ++lhs) {
        for(std::size_t rhs = lhs + 1; rhs < fractional_candidates.size(); ++rhs) {
            const auto lhs_signature = make_signature(
                fractional_projections.size(), fractional_candidates[lhs].value
            );
            const auto rhs_signature = make_signature(
                fractional_projections.size(), fractional_candidates[rhs].value
            );
            const auto scalar_difference =
                fractional_scalar.score(lhs_signature) - fractional_scalar.score(rhs_signature);
            if(std::fabs(scalar_difference) <= 2.0F * kMaximumFractionalScoreError) {
                continue;
            }
            const auto lut_difference =
                fractional_byte_lut.score(lhs_signature) - fractional_byte_lut.score(rhs_signature);
            if((scalar_difference > 0.0F) != (lut_difference > 0.0F)) {
                return fail("byte LUT must preserve clearly separated fractional ordering");
            }
        }
    }

    return 0;
}
