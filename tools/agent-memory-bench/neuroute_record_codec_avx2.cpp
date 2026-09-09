#include "neuroute_record_codec.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <immintrin.h>
#include <utility>

namespace agent_memory::neuroute {
namespace {

template <std::size_t Group>
__m128i unpack_group(const std::array<__m128i, 5>& words) {
    constexpr std::size_t bit = Group * 5U;
    constexpr std::size_t word = bit / 32U;
    constexpr int shift = static_cast<int>(bit % 32U);
    const auto mask = _mm_set1_epi32(31);
    if constexpr (shift <= 27) {
        return _mm_and_si128(mask, _mm_srli_epi32(words[word], shift));
    } else {
        return _mm_and_si128(mask, _mm_or_si128(
            _mm_srli_epi32(words[word], shift),
            _mm_slli_epi32(words[word + 1U], 32 - shift)));
    }
}

template <std::size_t First>
void decode_pair(const std::array<__m128i, 5>& words,
                 std::uint32_t* output) {
    auto values = _mm256_castsi128_si256(unpack_group<First>(words));
    values = _mm256_inserti128_si256(values, unpack_group<First + 1U>(words), 1);
    _mm256_storeu_si256(reinterpret_cast<__m256i*>(output + First * 4U),
                        values);
}

template <std::size_t... Pairs>
void decode_block(const std::uint8_t* packed, std::uint32_t* output,
                  std::index_sequence<Pairs...>) {
    const std::array<__m128i, 5> words = {
        _mm_loadu_si128(reinterpret_cast<const __m128i*>(packed)),
        _mm_loadu_si128(reinterpret_cast<const __m128i*>(packed + 16U)),
        _mm_loadu_si128(reinterpret_cast<const __m128i*>(packed + 32U)),
        _mm_loadu_si128(reinterpret_cast<const __m128i*>(packed + 48U)),
        _mm_loadu_si128(reinterpret_cast<const __m128i*>(packed + 64U))};
    (decode_pair<Pairs * 2U>(words, output), ...);
}

}  // namespace

void decode_nonlinear_int5_codes_avx2(const std::uint8_t* record,
                                      std::uint32_t* output) {
    for (std::size_t block = 0; block != 3; ++block)
        decode_block(record + block * 80U, output + block * 128U,
                     std::make_index_sequence<16>{});
}

}  // namespace agent_memory::neuroute
