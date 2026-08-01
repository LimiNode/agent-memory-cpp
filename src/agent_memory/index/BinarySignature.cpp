#include "BinarySignature.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <limits>
#include <map>
#include <numeric>
#include <stdexcept>
#include <utility>

#if (defined(__GNUC__) || defined(__clang__)) && \
    (defined(__x86_64__) || defined(__i386__) || defined(_M_X64) || defined(_M_IX86))
#include <immintrin.h>
#define AGENT_MEMORY_HAS_GNU_X86_INTRINSICS 1
#else
#define AGENT_MEMORY_HAS_GNU_X86_INTRINSICS 0
#endif

#if defined(_MSC_VER) && (defined(_M_X64) || defined(_M_IX86))
#include <intrin.h>
#define AGENT_MEMORY_HAS_MSVC_X86_INTRINSICS 1
#else
#define AGENT_MEMORY_HAS_MSVC_X86_INTRINSICS 0
#endif

namespace agent_memory {
    namespace {

        constexpr std::size_t kBitsPerWord = 64;

        [[nodiscard]] constexpr std::array<std::uint8_t, 256>
        make_byte_popcount_table() noexcept {
            std::array<std::uint8_t, 256> table{};
            for(std::size_t value = 1; value < table.size(); ++value) {
                table[value] = static_cast<std::uint8_t>(
                    table[value >> 1U] + (value & std::size_t{1})
                );
            }
            return table;
        }

        constexpr auto kBytePopcount = make_byte_popcount_table();

        constexpr std::size_t kMinimumAvx2WordCount = 16;

        [[nodiscard]] std::uint64_t valid_tail_mask(std::size_t bit_count) noexcept {
            const auto remainder = bit_count % kBitsPerWord;
            if(remainder == 0) {
                return std::numeric_limits<std::uint64_t>::max();
            }
            return (std::uint64_t{1} << remainder) - std::uint64_t{1};
        }

        [[nodiscard]] std::size_t popcount64_lookup(std::uint64_t value) noexcept {
            std::size_t count = 0;
            for(std::size_t byte = 0; byte < sizeof(value); ++byte) {
                count += kBytePopcount[value & std::uint64_t{0xFF}];
                value >>= 8U;
            }
            return count;
        }

#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS
        [[nodiscard]] bool runtime_supports_avx2() noexcept {
            __builtin_cpu_init();
            return __builtin_cpu_supports("avx2") != 0;
        }

        [[nodiscard]] bool runtime_supports_popcnt() noexcept {
            __builtin_cpu_init();
            return __builtin_cpu_supports("popcnt") != 0;
        }

        [[nodiscard]] __attribute__((target("popcnt"))) std::size_t
        popcount64_hardware(std::uint64_t value) noexcept {
            return static_cast<std::size_t>(
                __builtin_popcountll(static_cast<unsigned long long>(value))
            );
        }

        [[nodiscard]] __attribute__((target("avx2"))) std::size_t
        hamming_distance_words_avx2(
            const std::uint64_t* lhs,
            const std::uint64_t* rhs,
            std::size_t word_count
        ) noexcept {
            const auto low_nibble_mask = _mm256_set1_epi8(0x0F);
            const auto nibble_popcount = _mm256_setr_epi8(
                0, 1, 1, 2, 1, 2, 2, 3,
                1, 2, 2, 3, 2, 3, 3, 4,
                0, 1, 1, 2, 1, 2, 2, 3,
                1, 2, 2, 3, 2, 3, 3, 4
            );
            const auto zero = _mm256_setzero_si256();

            std::size_t distance = 0;
            std::size_t word = 0;
            for(; word + 4 <= word_count; word += 4) {
                const auto lhs_words = _mm256_loadu_si256(
                    reinterpret_cast<const __m256i*>(lhs + word)
                );
                const auto rhs_words = _mm256_loadu_si256(
                    reinterpret_cast<const __m256i*>(rhs + word)
                );
                const auto xored = _mm256_xor_si256(lhs_words, rhs_words);
                const auto low_nibbles = _mm256_and_si256(xored, low_nibble_mask);
                const auto high_nibbles = _mm256_and_si256(
                    _mm256_srli_epi16(xored, 4),
                    low_nibble_mask
                );
                const auto low_counts = _mm256_shuffle_epi8(
                    nibble_popcount,
                    low_nibbles
                );
                const auto high_counts = _mm256_shuffle_epi8(
                    nibble_popcount,
                    high_nibbles
                );
                const auto byte_counts = _mm256_add_epi8(low_counts, high_counts);
                const auto partial_sums = _mm256_sad_epu8(byte_counts, zero);

                alignas(32) std::uint64_t lanes[4]{};
                _mm256_store_si256(
                    reinterpret_cast<__m256i*>(lanes),
                    partial_sums
                );
                distance += static_cast<std::size_t>(
                    lanes[0] + lanes[1] + lanes[2] + lanes[3]
                );
            }
            for(; word < word_count; ++word) {
                distance += popcount64_lookup(lhs[word] ^ rhs[word]);
            }
            return distance;
        }
#elif AGENT_MEMORY_HAS_MSVC_X86_INTRINSICS
        [[nodiscard]] bool runtime_supports_popcnt() noexcept {
            int registers[4]{};
            __cpuid(registers, 1);
            constexpr int kPopcntFeatureBit = 1 << 23;
            return (registers[2] & kPopcntFeatureBit) != 0;
        }

        [[nodiscard]] std::size_t popcount64_hardware(std::uint64_t value) noexcept {
#if defined(_M_X64)
            return static_cast<std::size_t>(__popcnt64(value));
#else
            return static_cast<std::size_t>(
                __popcnt(static_cast<unsigned int>(value)) +
                __popcnt(static_cast<unsigned int>(value >> 32U))
            );
#endif
        }
#endif

        [[nodiscard]] std::size_t hamming_distance_words_lookup(
            const std::uint64_t* lhs,
            const std::uint64_t* rhs,
            std::size_t word_count
        ) noexcept {
            std::size_t distance = 0;
            for(std::size_t word = 0; word < word_count; ++word) {
                distance += popcount64_lookup(lhs[word] ^ rhs[word]);
            }
            return distance;
        }

        void hamming_distances_lookup(
            const std::uint64_t* query,
            const std::uint64_t* records,
            std::size_t record_count,
            std::size_t word_count,
            std::size_t* output
        ) noexcept {
            for(std::size_t record = 0; record < record_count; ++record) {
                output[record] = hamming_distance_words_lookup(
                    query,
                    records + record * word_count,
                    word_count
                );
            }
        }

#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS || AGENT_MEMORY_HAS_MSVC_X86_INTRINSICS
#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS
        __attribute__((target("popcnt")))
#endif
        [[nodiscard]] std::size_t hamming_distance_words_popcnt(
            const std::uint64_t* lhs,
            const std::uint64_t* rhs,
            std::size_t word_count
        ) noexcept {
            std::size_t distance = 0;
            for(std::size_t word = 0; word < word_count; ++word) {
                distance += popcount64_hardware(lhs[word] ^ rhs[word]);
            }
            return distance;
        }

#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS
        __attribute__((target("popcnt")))
#endif
        void hamming_distances_popcnt(
            const std::uint64_t* query,
            const std::uint64_t* records,
            std::size_t record_count,
            std::size_t word_count,
            std::size_t* output
        ) noexcept {
            for(std::size_t record = 0; record < record_count; ++record) {
                output[record] = hamming_distance_words_popcnt(
                    query,
                    records + record * word_count,
                    word_count
                );
            }
        }
#endif

        using HammingSingleKernel = std::size_t (*)(
            const std::uint64_t*,
            const std::uint64_t*,
            std::size_t
        ) noexcept;
        using HammingBatchKernel = void (*)(
            const std::uint64_t*,
            const std::uint64_t*,
            std::size_t,
            std::size_t,
            std::size_t*
        ) noexcept;

        struct HammingKernelSelection final {
            HammingDistanceBackend backend = HammingDistanceBackend::LookupTable;
            HammingSingleKernel single = hamming_distance_words_lookup;
            HammingBatchKernel batch = hamming_distances_lookup;
        };

#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS
        __attribute__((target("avx2"))) void hamming_distances_avx2(
            const std::uint64_t* query,
            const std::uint64_t* records,
            std::size_t record_count,
            std::size_t word_count,
            std::size_t* output
        ) noexcept {
            for(std::size_t record = 0; record < record_count; ++record) {
                output[record] = hamming_distance_words_avx2(
                    query,
                    records + record * word_count,
                    word_count
                );
            }
        }
#endif

        [[nodiscard]] bool is_hamming_backend_supported(
            HammingDistanceBackend backend
        ) noexcept {
            switch(backend) {
                case HammingDistanceBackend::LookupTable:
                    return true;
                case HammingDistanceBackend::HardwarePopcount:
#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS || AGENT_MEMORY_HAS_MSVC_X86_INTRINSICS
                {
                    static const bool supported = runtime_supports_popcnt();
                    return supported;
                }
#else
                    return false;
#endif
                case HammingDistanceBackend::Avx2Simd:
#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS
                {
                    static const bool supported = runtime_supports_avx2();
                    return supported;
                }
#else
                    return false;
#endif
            }
            return false;
        }

        [[nodiscard]] HammingKernelSelection hamming_kernel_for_backend(
            HammingDistanceBackend backend
        ) noexcept {
            switch(backend) {
                case HammingDistanceBackend::LookupTable:
                    return {};
#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS || AGENT_MEMORY_HAS_MSVC_X86_INTRINSICS
                case HammingDistanceBackend::HardwarePopcount:
                    return {
                        HammingDistanceBackend::HardwarePopcount,
                        hamming_distance_words_popcnt,
                        hamming_distances_popcnt
                    };
#endif
#if AGENT_MEMORY_HAS_GNU_X86_INTRINSICS
                case HammingDistanceBackend::Avx2Simd:
                    return {
                        HammingDistanceBackend::Avx2Simd,
                        hamming_distance_words_avx2,
                        hamming_distances_avx2
                    };
#endif
                default:
                    return {};
            }
        }

        [[nodiscard]] HammingKernelSelection select_hamming_distance_kernel(
            std::size_t word_count
        ) noexcept {
            if(word_count >= kMinimumAvx2WordCount
               && is_hamming_backend_supported(HammingDistanceBackend::Avx2Simd)) {
                return hamming_kernel_for_backend(HammingDistanceBackend::Avx2Simd);
            }
            if(is_hamming_backend_supported(HammingDistanceBackend::HardwarePopcount)) {
                return hamming_kernel_for_backend(
                    HammingDistanceBackend::HardwarePopcount
                );
            }
            return {};
        }

        [[nodiscard]] double bit_entropy(double probability_one) noexcept {
            if(probability_one <= 0.0 || probability_one >= 1.0) {
                return 0.0;
            }

            const double probability_zero = 1.0 - probability_one;
            const double log_two = std::log(2.0);
            return -(
                probability_one * (std::log(probability_one) / log_two) +
                probability_zero * (std::log(probability_zero) / log_two)
            );
        }

        [[nodiscard]] std::size_t total_pair_count(std::size_t n) {
            if(n < 2) {
                return 0;
            }
            if(n > (std::numeric_limits<std::size_t>::max() / (n - 1))) {
                throw std::overflow_error("Binary code pair count overflow");
            }
            return (n * (n - 1)) / 2;
        }

        [[nodiscard]] std::size_t pairs_before_row(std::size_t row, std::size_t n) noexcept {
            return (row * ((2 * n) - row - 1)) / 2;
        }

        [[nodiscard]] std::pair<std::size_t, std::size_t> decode_pair_ordinal(
            std::size_t ordinal,
            std::size_t signature_count
        ) noexcept {
            std::size_t low = 0;
            std::size_t high = signature_count - 1;

            while(low + 1 < high) {
                const auto middle = low + ((high - low) / 2);
                if(pairs_before_row(middle, signature_count) <= ordinal) {
                    low = middle;
                } else {
                    high = middle;
                }
            }

            const auto offset_in_row = ordinal - pairs_before_row(low, signature_count);
            return {low, low + 1 + offset_in_row};
        }

        [[nodiscard]] std::size_t deterministic_pair_stride(std::size_t pair_count) noexcept {
            if(pair_count <= 1) {
                return 1;
            }

            std::size_t stride = static_cast<std::size_t>(1099511628211ULL % pair_count);
            if(stride == 0) {
                stride = 1;
            }

            while(std::gcd(stride, pair_count) != 1) {
                ++stride;
                if(stride == pair_count) {
                    stride = 1;
                }
            }

            return stride;
        }

        [[nodiscard]] std::size_t deterministic_pair_offset(std::size_t pair_count) noexcept {
            if(pair_count == 0) {
                return 0;
            }
            return static_cast<std::size_t>(1469598103934665603ULL % pair_count);
        }

        [[nodiscard]] std::size_t add_mod(
            std::size_t value,
            std::size_t addend,
            std::size_t modulus
        ) noexcept {
            if(value >= modulus - addend) {
                return value - (modulus - addend);
            }
            return value + addend;
        }

    } // namespace

    std::size_t binary_signature_word_count(std::size_t bit_count) noexcept {
        return (bit_count / kBitsPerWord) + ((bit_count % kBitsPerWord) == 0 ? 0 : 1);
    }

    BinarySignature::BinarySignature(std::size_t bit_count)
        : m_bit_count(bit_count),
          m_words(binary_signature_word_count(bit_count), std::uint64_t{0}) {}

    BinarySignature::BinarySignature(std::size_t bit_count, std::vector<std::uint64_t> words)
        : m_bit_count(bit_count),
          m_words(std::move(words)) {
        validate_words(m_bit_count, m_words);
    }

    std::size_t BinarySignature::bit_count() const noexcept {
        return m_bit_count;
    }

    std::size_t BinarySignature::word_count() const noexcept {
        return m_words.size();
    }

    bool BinarySignature::empty() const noexcept {
        return m_bit_count == 0;
    }

    bool BinarySignature::bit(std::size_t index) const {
        if(index >= m_bit_count) {
            throw std::out_of_range("BinarySignature bit index out of range");
        }

        const auto word_index = index / kBitsPerWord;
        const auto bit_index = index % kBitsPerWord;
        return (m_words[word_index] & (std::uint64_t{1} << bit_index)) != 0;
    }

    void BinarySignature::set_bit(std::size_t index, bool value) {
        if(index >= m_bit_count) {
            throw std::out_of_range("BinarySignature bit index out of range");
        }

        const auto word_index = index / kBitsPerWord;
        const auto bit_index = index % kBitsPerWord;
        const auto mask = std::uint64_t{1} << bit_index;
        if(value) {
            m_words[word_index] |= mask;
        } else {
            m_words[word_index] &= ~mask;
        }
    }

    const std::vector<std::uint64_t>& BinarySignature::words() const noexcept {
        return m_words;
    }

    void BinarySignature::validate_words(
        std::size_t bit_count,
        const std::vector<std::uint64_t>& words
    ) {
        if(words.size() != binary_signature_word_count(bit_count)) {
            throw std::invalid_argument("BinarySignature word count does not match bit count");
        }

        if(words.empty()) {
            return;
        }

        const auto tail_mask = valid_tail_mask(bit_count);
        if((words.back() & ~tail_mask) != 0) {
            throw std::invalid_argument("BinarySignature unused tail bits must be zero");
        }
    }

    bool operator==(const BinarySignature& lhs, const BinarySignature& rhs) noexcept {
        return lhs.m_bit_count == rhs.m_bit_count && lhs.m_words == rhs.m_words;
    }

    bool operator!=(const BinarySignature& lhs, const BinarySignature& rhs) noexcept {
        return !(lhs == rhs);
    }

    std::string_view hamming_distance_backend_name(
        HammingDistanceBackend backend
    ) noexcept {
        switch(backend) {
            case HammingDistanceBackend::LookupTable:
                return "lookup_table";
            case HammingDistanceBackend::HardwarePopcount:
                return "hardware_popcount";
            case HammingDistanceBackend::Avx2Simd:
                return "avx2_simd";
        }
        return "unknown";
    }

    bool hamming_distance_backend_supported(HammingDistanceBackend backend) noexcept {
        return is_hamming_backend_supported(backend);
    }

    HammingDistanceComputer::HammingDistanceComputer(std::size_t word_count) noexcept
        : m_word_count(word_count) {
        const auto selection = select_hamming_distance_kernel(word_count);
        m_backend = selection.backend;
        m_single_kernel = selection.single;
        m_batch_kernel = selection.batch;
    }

    HammingDistanceComputer::HammingDistanceComputer(
        std::size_t word_count,
        HammingDistanceBackend backend
    )
        : m_word_count(word_count) {
        if(!is_hamming_backend_supported(backend)) {
            throw std::invalid_argument(
                "requested Hamming-distance backend is not supported by this build and CPU"
            );
        }
        const auto selection = hamming_kernel_for_backend(backend);
        m_backend = selection.backend;
        m_single_kernel = selection.single;
        m_batch_kernel = selection.batch;
    }

    std::size_t HammingDistanceComputer::word_count() const noexcept {
        return m_word_count;
    }

    HammingDistanceBackend HammingDistanceComputer::backend() const noexcept {
        return m_backend;
    }

    std::size_t HammingDistanceComputer::distance_words(
        const std::uint64_t* lhs,
        const std::uint64_t* rhs
    ) const noexcept {
        return m_single_kernel(lhs, rhs, m_word_count);
    }

    void HammingDistanceComputer::compute_distances(
        const std::uint64_t* query_words,
        const std::uint64_t* record_words,
        std::size_t record_count,
        std::size_t* output_distances
    ) const noexcept {
        m_batch_kernel(
            query_words,
            record_words,
            record_count,
            m_word_count,
            output_distances
        );
    }

    std::size_t hamming_distance(const BinarySignature& lhs, const BinarySignature& rhs) {
        if(lhs.bit_count() != rhs.bit_count()) {
            throw std::invalid_argument("Hamming distance requires equal-width signatures");
        }

        return HammingDistanceComputer(lhs.word_count()).distance_words(
            lhs.words().data(),
            rhs.words().data()
        );
    }

    BinaryCodeHealthMetrics analyze_binary_code_health(
        const std::vector<BinarySignature>& signatures,
        BinaryCodeHealthOptions options
    ) {
        BinaryCodeHealthMetrics metrics;
        metrics.signature_count = signatures.size();
        if(signatures.empty()) {
            return metrics;
        }

        metrics.bit_count = signatures.front().bit_count();
        for(const auto& signature : signatures) {
            if(signature.bit_count() != metrics.bit_count) {
                throw std::invalid_argument(
                    "Binary code health analysis requires equal-width signatures"
                );
            }
        }

        metrics.fraction_ones_per_bit.assign(metrics.bit_count, 0.0);
        if(metrics.bit_count > 0) {
            for(std::size_t bit = 0; bit < metrics.bit_count; ++bit) {
                std::size_t ones = 0;
                for(const auto& signature : signatures) {
                    if(signature.bit(bit)) {
                        ++ones;
                    }
                }
                metrics.fraction_ones_per_bit[bit] =
                    static_cast<double>(ones) / static_cast<double>(signatures.size());
            }

            std::size_t constant_bits = 0;
            double entropy_sum = 0.0;
            metrics.min_bit_entropy = std::numeric_limits<double>::infinity();
            metrics.max_bit_entropy = 0.0;

            for(const auto fraction : metrics.fraction_ones_per_bit) {
                if(fraction == 0.0 || fraction == 1.0) {
                    ++constant_bits;
                }

                const auto entropy = bit_entropy(fraction);
                entropy_sum += entropy;
                metrics.min_bit_entropy = std::min(metrics.min_bit_entropy, entropy);
                metrics.max_bit_entropy = std::max(metrics.max_bit_entropy, entropy);
            }

            metrics.constant_bit_fraction =
                static_cast<double>(constant_bits) / static_cast<double>(metrics.bit_count);
            metrics.mean_bit_entropy = entropy_sum / static_cast<double>(metrics.bit_count);
        }

        std::map<std::vector<std::uint64_t>, std::size_t> buckets;
        for(const auto& signature : signatures) {
            ++buckets[signature.words()];
        }

        metrics.exact_signature_bucket_sizes.reserve(buckets.size());
        for(const auto& item : buckets) {
            metrics.exact_signature_bucket_sizes.push_back(item.second);
        }
        std::sort(
            metrics.exact_signature_bucket_sizes.begin(),
            metrics.exact_signature_bucket_sizes.end(),
            std::greater<std::size_t>{}
        );

        metrics.duplicate_signature_rate =
            static_cast<double>(signatures.size() - buckets.size()) /
            static_cast<double>(signatures.size());

        const auto all_pairs = total_pair_count(signatures.size());
        const auto sample_limit = std::min(options.max_pairwise_samples, all_pairs);
        if(sample_limit == 0) {
            return metrics;
        }

        double distance_sum = 0.0;
        double distance_squared_sum = 0.0;
        const auto add_distance = [&metrics, &distance_sum, &distance_squared_sum](
            std::size_t distance
        ) {
            distance_sum += static_cast<double>(distance);
            distance_squared_sum += static_cast<double>(distance) *
                static_cast<double>(distance);
            if(metrics.sampled_pair_count == 0) {
                metrics.sampled_min_pairwise_hamming_distance = distance;
                metrics.sampled_max_pairwise_hamming_distance = distance;
            } else {
                metrics.sampled_min_pairwise_hamming_distance = std::min(
                    metrics.sampled_min_pairwise_hamming_distance,
                    distance
                );
                metrics.sampled_max_pairwise_hamming_distance = std::max(
                    metrics.sampled_max_pairwise_hamming_distance,
                    distance
                );
            }
            ++metrics.sampled_pair_count;
        };
        if(all_pairs <= options.max_pairwise_samples) {
            for(std::size_t i = 0; i < signatures.size(); ++i) {
                for(std::size_t j = i + 1; j < signatures.size(); ++j) {
                    add_distance(hamming_distance(signatures[i], signatures[j]));
                }
            }
        } else {
            auto ordinal = deterministic_pair_offset(all_pairs);
            const auto stride = deterministic_pair_stride(all_pairs);
            for(std::size_t sample = 0; sample < sample_limit; ++sample) {
                const auto pair = decode_pair_ordinal(ordinal, signatures.size());
                const auto i = pair.first;
                const auto j = pair.second;
                add_distance(hamming_distance(signatures[i], signatures[j]));
                ordinal = add_mod(ordinal, stride, all_pairs);
            }
        }

        metrics.sampled_mean_pairwise_hamming_distance =
            distance_sum / static_cast<double>(metrics.sampled_pair_count);
        const auto mean_square = distance_squared_sum /
            static_cast<double>(metrics.sampled_pair_count);
        metrics.sampled_pairwise_hamming_distance_stddev = std::sqrt(std::max(
            0.0,
            mean_square - metrics.sampled_mean_pairwise_hamming_distance *
                metrics.sampled_mean_pairwise_hamming_distance
        ));
        return metrics;
    }

} // namespace agent_memory
