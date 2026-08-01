#include <agent_memory.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    bool near(double lhs, double rhs, double tolerance = 1.0e-9) {
        return std::fabs(lhs - rhs) <= tolerance;
    }

    template <typename Function>
    bool throws_invalid_argument(Function&& fn) {
        try {
            fn();
        } catch(const std::invalid_argument&) {
            return true;
        }
        return false;
    }

    bool throws_out_of_range(void (*fn)()) {
        try {
            fn();
        } catch(const std::out_of_range&) {
            return true;
        }
        return false;
    }

    agent_memory::BinarySignature make_signature(
        std::size_t bit_count,
        std::vector<std::size_t> bits
    ) {
        agent_memory::BinarySignature signature(bit_count);
        for(const auto bit : bits) {
            signature.set_bit(bit);
        }
        return signature;
    }

    agent_memory::BinarySignature make_low_bits_signature(std::size_t value) {
        agent_memory::BinarySignature signature(8);
        for(std::size_t bit = 0; bit < signature.bit_count(); ++bit) {
            signature.set_bit(bit, ((value >> bit) & std::size_t{1}) != 0);
        }
        return signature;
    }

    agent_memory::BinarySignature make_pattern_signature(
        std::size_t bit_count,
        std::size_t salt
    ) {
        agent_memory::BinarySignature signature(bit_count);
        for(std::size_t bit = 0; bit < bit_count; ++bit) {
            const auto mixed = bit * std::size_t{2654435761U}
                + salt * std::size_t{2246822519U};
            signature.set_bit(bit, ((mixed >> (bit % 13U)) & 1U) != 0U);
        }
        return signature;
    }

    void construct_with_bad_word_count() {
        (void)agent_memory::BinarySignature(65, {0ULL});
    }

    void construct_with_tail_bits() {
        (void)agent_memory::BinarySignature(3, {0b1000ULL});
    }

    void read_out_of_range() {
        agent_memory::BinarySignature signature(3);
        (void)signature.bit(3);
    }

    void write_out_of_range() {
        agent_memory::BinarySignature signature(3);
        signature.set_bit(3);
    }

    void hamming_width_mismatch() {
        const auto lhs = make_signature(3, {0});
        const auto rhs = make_signature(4, {0});
        (void)agent_memory::hamming_distance(lhs, rhs);
    }

    void health_width_mismatch() {
        const auto lhs = make_signature(3, {0});
        const auto rhs = make_signature(4, {0});
        (void)agent_memory::analyze_binary_code_health({lhs, rhs});
    }

} // namespace

int main() {
    if(agent_memory::binary_signature_word_count(0) != 0) {
        return fail("zero-bit signatures must not allocate words");
    }
    if(agent_memory::binary_signature_word_count(1) != 1) {
        return fail("one-bit signatures must allocate one word");
    }
    if(agent_memory::binary_signature_word_count(64) != 1) {
        return fail("64-bit signatures must allocate one word");
    }
    if(agent_memory::binary_signature_word_count(65) != 2) {
        return fail("65-bit signatures must allocate two words");
    }
    const auto max_bits = std::numeric_limits<std::size_t>::max();
    if(agent_memory::binary_signature_word_count(max_bits) != (max_bits / 64U) + 1U) {
        return fail("word count must not overflow for very large bit counts");
    }

    agent_memory::BinarySignature signature(70);
    if(signature.bit_count() != 70 || signature.word_count() != 2 || signature.empty()) {
        return fail("BinarySignature must expose bit and word counts");
    }

    signature.set_bit(0);
    signature.set_bit(63);
    signature.set_bit(64);
    signature.set_bit(69);
    signature.set_bit(64, false);

    if(!signature.bit(0) || !signature.bit(63) || signature.bit(64) || !signature.bit(69)) {
        return fail("BinarySignature must read and write bits across word boundaries");
    }

    if(signature.words()[0] != ((std::uint64_t{1} << 0) | (std::uint64_t{1} << 63))) {
        return fail("BinarySignature first word packing is incorrect");
    }
    if(signature.words()[1] != (std::uint64_t{1} << 5)) {
        return fail("BinarySignature second word packing is incorrect");
    }

    if(!throws_invalid_argument(construct_with_bad_word_count)) {
        return fail("BinarySignature must reject word-count mismatches");
    }
    if(!throws_invalid_argument(construct_with_tail_bits)) {
        return fail("BinarySignature must reject non-zero unused tail bits");
    }
    if(!throws_out_of_range(read_out_of_range)) {
        return fail("BinarySignature::bit must reject out-of-range indexes");
    }
    if(!throws_out_of_range(write_out_of_range)) {
        return fail("BinarySignature::set_bit must reject out-of-range indexes");
    }

    const auto a = make_signature(8, {0, 2, 7});
    const auto b = make_signature(8, {1, 2, 6, 7});
    if(agent_memory::hamming_distance(a, b) != 3) {
        return fail("hamming_distance must count differing bits only");
    }
    const agent_memory::HammingDistanceComputer short_distance(a.word_count());
    const std::vector<std::uint64_t> short_records{
        a.words().front(),
        b.words().front(),
    };
    std::vector<std::size_t> batch_distances(2);
    short_distance.compute_distances(
        a.words().data(),
        short_records.data(),
        batch_distances.size(),
        batch_distances.data()
    );
    if(batch_distances != std::vector<std::size_t>{0, 3}
       || agent_memory::hamming_distance_backend_name(short_distance.backend()).empty()) {
        return fail("reusable Hamming batch kernels must match the checked free function");
    }
    const auto wide_a = make_signature(256, {0, 63, 64, 127, 128, 191, 192, 255});
    const auto wide_b = make_signature(256, {1, 63, 65, 127, 129, 191, 193, 255});
    if(agent_memory::hamming_distance(wide_a, wide_b) != 8) {
        return fail("hamming_distance must count differing bits across many words");
    }
    const auto avx_tail_a = make_signature(320, {0, 63, 64, 127, 128, 191, 192, 255, 256, 319});
    const auto avx_tail_b = make_signature(320, {1, 63, 65, 127, 129, 191, 193, 255, 257, 319});
    if(agent_memory::hamming_distance(avx_tail_a, avx_tail_b) != 10) {
        return fail("hamming_distance must count differing bits in non-four-word tails");
    }

    constexpr std::array<agent_memory::HammingDistanceBackend, 3> backends{
        agent_memory::HammingDistanceBackend::LookupTable,
        agent_memory::HammingDistanceBackend::HardwarePopcount,
        agent_memory::HammingDistanceBackend::Avx2Simd,
    };
    constexpr std::array<std::size_t, 4> backend_test_widths{
        1024,
        1025,
        1088,
        1216,
    };
    for(const auto width : backend_test_widths) {
        const auto query = make_pattern_signature(width, 1);
        std::vector<agent_memory::BinarySignature> records;
        std::vector<std::uint64_t> packed_records;
        for(std::size_t record = 0; record < 7; ++record) {
            records.push_back(make_pattern_signature(width, record + 2));
            packed_records.insert(
                packed_records.end(),
                records.back().words().begin(),
                records.back().words().end()
            );
        }

        const agent_memory::HammingDistanceComputer reference(
            query.word_count(),
            agent_memory::HammingDistanceBackend::LookupTable
        );
        std::vector<std::size_t> expected(records.size());
        reference.compute_distances(
            query.words().data(),
            packed_records.data(),
            records.size(),
            expected.data()
        );

        for(const auto backend : backends) {
            if(!agent_memory::hamming_distance_backend_supported(backend)) {
                continue;
            }
            const agent_memory::HammingDistanceComputer computer(
                query.word_count(),
                backend
            );
            std::vector<std::size_t> actual(records.size());
            computer.compute_distances(
                query.words().data(),
                packed_records.data(),
                records.size(),
                actual.data()
            );
            if(actual != expected) {
                return fail("forced Hamming batch backends must match the lookup reference");
            }
            for(std::size_t record = 0; record < records.size(); ++record) {
                if(computer.distance_words(
                       query.words().data(),
                       records[record].words().data()
                   ) != expected[record]) {
                    return fail(
                        "forced Hamming single backends must match the lookup reference"
                    );
                }
            }
        }
    }
    if(!throws_invalid_argument([] {
           (void)agent_memory::HammingDistanceComputer(
               16,
               static_cast<agent_memory::HammingDistanceBackend>(255)
           );
       })) {
        return fail("forced Hamming construction must reject unknown backends");
    }
    if(!throws_invalid_argument(hamming_width_mismatch)) {
        return fail("hamming_distance must reject width mismatches");
    }

    const auto health = agent_memory::analyze_binary_code_health({
        make_signature(4, {0, 2}),
        make_signature(4, {0, 2}),
        make_signature(4, {1, 2}),
        make_signature(4, {1, 3})
    });

    if(health.signature_count != 4 || health.bit_count != 4) {
        return fail("health metrics must report signature and bit counts");
    }
    if(health.fraction_ones_per_bit.size() != 4) {
        return fail("health metrics must expose per-bit occupancy");
    }
    if(
        !near(health.fraction_ones_per_bit[0], 0.5) ||
        !near(health.fraction_ones_per_bit[1], 0.5) ||
        !near(health.fraction_ones_per_bit[2], 0.75) ||
        !near(health.fraction_ones_per_bit[3], 0.25)
    ) {
        return fail("health metrics must compute per-bit fraction of ones");
    }
    if(!near(health.constant_bit_fraction, 0.0)) {
        return fail("health metrics must compute constant-bit fraction");
    }
    if(!(health.min_bit_entropy > 0.0 && health.max_bit_entropy <= 1.0)) {
        return fail("health metrics must compute entropy in the normalized binary range");
    }
    if(!near(health.total_bit_entropy, 2.0 + 2.0 * 0.8112781244591328) ||
       !near(health.p05_bit_entropy, 0.8112781244591328) ||
       !near(health.median_bit_entropy, 0.8112781244591328) ||
       !near(health.p95_bit_entropy, 1.0)) {
        return fail("health metrics must summarize per-bit entropy distribution");
    }
    if(health.correlation_sample_count != 4 ||
       !(health.max_absolute_bit_correlation >= 0.999999) ||
       !(health.bit_correlation_participation_ratio > 0.0 &&
         health.bit_correlation_participation_ratio < 4.0)) {
        return fail("health metrics must reveal correlated effective code dimensions");
    }
    if(!near(health.duplicate_signature_rate, 0.25)) {
        return fail("health metrics must compute duplicate-signature rate");
    }
    if(health.exact_signature_bucket_sizes != std::vector<std::size_t>{2, 1, 1}) {
        return fail("health metrics must expose descending exact-signature bucket sizes");
    }
    if(health.sampled_pair_count != 6) {
        return fail("small health analysis must inspect every pair");
    }
    if(!near(health.sampled_mean_pairwise_hamming_distance, 14.0 / 6.0)) {
        return fail("health metrics must compute mean pairwise Hamming distance");
    }

    std::vector<agent_memory::BinarySignature> many;
    for(std::size_t i = 0; i < 20; ++i) {
        many.push_back(make_low_bits_signature(i));
    }
    const auto sampled = agent_memory::analyze_binary_code_health(
        many,
        agent_memory::BinaryCodeHealthOptions{7}
    );
    if(sampled.sampled_pair_count != 7) {
        return fail("large health analysis must respect max_pairwise_samples");
    }
    if(!near(sampled.sampled_mean_pairwise_hamming_distance, 17.0 / 7.0)) {
        return fail("sampled health analysis must use deterministic unique pair ordinals");
    }

    const auto no_pair_sample = agent_memory::analyze_binary_code_health(
        many,
        agent_memory::BinaryCodeHealthOptions{0}
    );
    if(no_pair_sample.sampled_pair_count != 0) {
        return fail("health analysis must allow pairwise Hamming sampling to be disabled");
    }

    if(!throws_invalid_argument(health_width_mismatch)) {
        return fail("health analysis must reject width mismatches");
    }

    const auto empty = agent_memory::analyze_binary_code_health({});
    if(empty.signature_count != 0 || empty.bit_count != 0 || !empty.fraction_ones_per_bit.empty()) {
        return fail("health analysis must accept an empty signature collection");
    }

    return 0;
}
