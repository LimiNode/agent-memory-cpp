#include "neuroute_record_codec.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>

#if defined(_MSC_VER) && (defined(_M_IX86) || defined(_M_X64))
#include <intrin.h>
#endif

#ifndef AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2
#define AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2 0
#endif
#ifndef AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2
#define AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2 0
#endif

namespace agent_memory::neuroute {
#if AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2
void decode_nonlinear_int5_codes_sse2(const std::uint8_t*, std::uint32_t*);
#endif
#if AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2
void decode_nonlinear_int5_codes_avx2(const std::uint8_t*, std::uint32_t*);
float score_nonlinear_int5_power_half_avx2(const std::uint8_t*, const float*);
#endif

namespace {

std::uint32_t load_u32_le(const std::uint8_t* bytes) noexcept {
    return static_cast<std::uint32_t>(bytes[0]) |
        (static_cast<std::uint32_t>(bytes[1]) << 8U) |
        (static_cast<std::uint32_t>(bytes[2]) << 16U) |
        (static_cast<std::uint32_t>(bytes[3]) << 24U);
}

void store_u32_le(std::uint8_t* bytes, std::uint32_t value) noexcept {
    for (std::size_t index = 0; index != 4; ++index)
        bytes[index] = static_cast<std::uint8_t>(value >> (index * 8U));
}

void decode_block_portable(const std::uint8_t* packed,
                           std::uint32_t* output) noexcept {
    std::array<std::array<std::uint32_t, 4>, 5> words{};
    for (std::size_t word = 0; word != words.size(); ++word) {
        for (std::size_t lane = 0; lane != 4; ++lane) {
            words[word][lane] = load_u32_le(
                packed + word * 16U + lane * 4U);
        }
    }
    for (std::size_t group = 0; group != 32; ++group) {
        const auto bit = group * 5U;
        const auto word = bit / 32U;
        const auto shift = static_cast<unsigned>(bit % 32U);
        for (std::size_t lane = 0; lane != 4; ++lane) {
            auto value = words[word][lane] >> shift;
            if (shift > 27U)
                value |= words[word + 1U][lane] << (32U - shift);
            output[group * 4U + lane] = value & 31U;
        }
    }
}

void pack_block_portable(const std::uint32_t* input,
                         std::uint8_t* packed) {
    std::array<std::array<std::uint32_t, 4>, 5> words{};
    for (std::size_t group = 0; group != 32; ++group) {
        const auto bit = group * 5U;
        const auto word = bit / 32U;
        const auto shift = static_cast<unsigned>(bit % 32U);
        for (std::size_t lane = 0; lane != 4; ++lane) {
            const auto value = input[group * 4U + lane];
            if (value > 30U)
                throw std::invalid_argument("nonlinear INT5 code exceeds 30");
            words[word][lane] |= value << shift;
            if (shift > 27U)
                words[word + 1U][lane] |= value >> (32U - shift);
        }
    }
    for (std::size_t word = 0; word != words.size(); ++word) {
        for (std::size_t lane = 0; lane != 4; ++lane) {
            store_u32_le(packed + word * 16U + lane * 4U,
                         words[word][lane]);
        }
    }
}

bool cpu_has_sse2() noexcept {
#if defined(_M_X64) || defined(__x86_64__)
    return true;
#elif defined(_MSC_VER) && defined(_M_IX86)
    int registers[4]{};
    __cpuid(registers, 1);
    return (registers[3] & (1 << 26)) != 0;
#elif defined(__i386__) && (defined(__GNUC__) || defined(__clang__))
    return __builtin_cpu_supports("sse2");
#else
    return false;
#endif
}

bool cpu_has_avx2() noexcept {
#if defined(_MSC_VER) && (defined(_M_IX86) || defined(_M_X64))
    int registers[4]{};
    __cpuid(registers, 1);
    const bool osxsave = (registers[2] & (1 << 27)) != 0;
    const bool avx = (registers[2] & (1 << 28)) != 0;
    if (!osxsave || !avx || (_xgetbv(0) & 0x6U) != 0x6U) return false;
    __cpuidex(registers, 7, 0);
    return (registers[1] & (1 << 5)) != 0;
#elif (defined(__x86_64__) || defined(__i386__)) && \
      (defined(__GNUC__) || defined(__clang__))
    return __builtin_cpu_supports("avx2");
#else
    return false;
#endif
}

}  // namespace

RepresentativeStorageMode parse_storage_mode(std::string_view value) {
    if (value == "int8") return RepresentativeStorageMode::Int8;
    if (value == "nonlinear_int5_power_half")
        return RepresentativeStorageMode::NonlinearInt5PowerHalf;
    throw std::invalid_argument("unsupported representative storage mode: " +
                                std::string(value));
}

std::string_view storage_mode_name(RepresentativeStorageMode value) noexcept {
    return value == RepresentativeStorageMode::Int8
        ? "int8" : "nonlinear_int5_power_half";
}

std::string_view execution_kernel_name(RecordExecutionKernel value) noexcept {
    switch (value) {
        case RecordExecutionKernel::Portable: return "portable";
        case RecordExecutionKernel::Sse2: return "sse2";
        case RecordExecutionKernel::Avx2: return "avx2";
    }
    return "unknown";
}

RecordFormatDescriptor record_format(
        RepresentativeStorageMode value) noexcept {
    if (value == RepresentativeStorageMode::Int8) return {};
    return {record_format_version, record_dimensions, value,
        nonlinear_int5_record_bytes, "simdcomp_bp128_le_v1",
        "ieee754_binary32_little_endian", "power_0.5_signed_square"};
}

bool execution_kernel_compiled(RecordExecutionKernel value) noexcept {
    switch (value) {
        case RecordExecutionKernel::Portable: return true;
        case RecordExecutionKernel::Sse2:
            return AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2 != 0;
        case RecordExecutionKernel::Avx2:
            return AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2 != 0;
    }
    return false;
}

bool execution_kernel_supported(RecordExecutionKernel value) noexcept {
    if (!execution_kernel_compiled(value)) return false;
    switch (value) {
        case RecordExecutionKernel::Portable: return true;
        case RecordExecutionKernel::Sse2: return cpu_has_sse2();
        case RecordExecutionKernel::Avx2: return cpu_has_avx2();
    }
    return false;
}

void pack_nonlinear_int5_power_half(
        const std::array<std::uint32_t, record_dimensions>& codes,
        float amplitude,
        std::array<std::uint8_t, nonlinear_int5_record_bytes>& record) {
    static_assert(std::numeric_limits<float>::is_iec559 && sizeof(float) == 4,
                  "NeuRoute records require IEEE-754 binary32");
    for (std::size_t block = 0; block != 3; ++block)
        pack_block_portable(codes.data() + block * 128U,
                            record.data() + block * 80U);
    std::uint32_t bits = 0;
    std::memcpy(&bits, &amplitude, sizeof(bits));
    store_u32_le(record.data() + 240U, bits);
}

void decode_nonlinear_int5_codes(
        const std::uint8_t* record,
        std::array<std::uint32_t, record_dimensions>& codes,
        RecordExecutionKernel kernel) {
    if (!execution_kernel_supported(kernel))
        throw std::runtime_error("requested NeuRoute execution kernel is unavailable");
    switch (kernel) {
        case RecordExecutionKernel::Portable:
            for (std::size_t block = 0; block != 3; ++block)
                decode_block_portable(record + block * 80U,
                                      codes.data() + block * 128U);
            return;
        case RecordExecutionKernel::Sse2:
#if AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2
            decode_nonlinear_int5_codes_sse2(record, codes.data());
            return;
#else
            break;
#endif
        case RecordExecutionKernel::Avx2:
#if AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2
            decode_nonlinear_int5_codes_avx2(record, codes.data());
            return;
#else
            break;
#endif
    }
    throw std::runtime_error("requested NeuRoute execution kernel was not compiled");
}

float nonlinear_int5_amplitude(const std::uint8_t* record) noexcept {
    const auto bits = load_u32_le(record + 240U);
    float result = 0.0F;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

float score_nonlinear_int5_power_half(
        const std::uint8_t* record, const float* query,
        RecordExecutionKernel kernel) {
    std::array<std::uint32_t, record_dimensions> codes{};
    decode_nonlinear_int5_codes(record, codes, kernel);
    float score = 0.0F;
    for (std::size_t dimension = 0; dimension != record_dimensions;
         ++dimension) {
        const auto centered = static_cast<int>(codes[dimension]) - 15;
        const auto signed_square = centered * std::abs(centered);
        score += static_cast<float>(signed_square) * query[dimension];
    }
    return score * nonlinear_int5_amplitude(record) / 225.0F;
}

float score_nonlinear_int5_power_half_fast(
        const std::uint8_t* record, const float* query,
        RecordExecutionKernel kernel) {
    if (!execution_kernel_supported(kernel))
        throw std::runtime_error("requested NeuRoute execution kernel is unavailable");
#if AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2
    if (kernel == RecordExecutionKernel::Avx2)
        return score_nonlinear_int5_power_half_avx2(record, query);
#endif
    return score_nonlinear_int5_power_half(record, query, kernel);
}

float score_int8(const std::uint8_t* record, const float* query) {
    const auto bits = load_u32_le(record + record_dimensions);
    float scale = 0.0F;
    std::memcpy(&scale, &bits, sizeof(scale));
    float score = 0.0F;
    for (std::size_t dimension = 0; dimension != record_dimensions;
         ++dimension) {
        score += static_cast<float>(static_cast<int>(record[dimension]) - 127) *
                 scale * query[dimension];
    }
    return score;
}

}  // namespace agent_memory::neuroute
