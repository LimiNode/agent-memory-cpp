#ifndef AGENT_MEMORY_HEADER_TOOLS_AGENT_MEMORY_BENCH_NEUROUTE_RECORD_CODEC_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_TOOLS_AGENT_MEMORY_BENCH_NEUROUTE_RECORD_CODEC_HPP_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace agent_memory::neuroute {

constexpr std::size_t record_dimensions = 384;
constexpr std::size_t int8_record_bytes = 388;
constexpr std::size_t nonlinear_int5_record_bytes = 244;
constexpr std::uint32_t record_format_version = 1;

enum class RepresentativeStorageMode {
    Int8,
    NonlinearInt5PowerHalf,
};

enum class RecordExecutionKernel {
    Portable,
    Sse2,
    Avx2,
};

struct RecordFormatDescriptor final {
    std::uint32_t version = record_format_version;
    std::size_t dimensions = record_dimensions;
    RepresentativeStorageMode codec = RepresentativeStorageMode::Int8;
    std::size_t record_bytes = int8_record_bytes;
    std::string_view code_layout = "biased_signed_int8";
    std::string_view scale_layout = "ieee754_binary32_little_endian";
    std::string_view compander = "none";
};

RepresentativeStorageMode parse_storage_mode(std::string_view value);
std::string_view storage_mode_name(RepresentativeStorageMode value) noexcept;
std::string_view execution_kernel_name(RecordExecutionKernel value) noexcept;
RecordFormatDescriptor record_format(RepresentativeStorageMode value) noexcept;

bool execution_kernel_compiled(RecordExecutionKernel value) noexcept;
bool execution_kernel_supported(RecordExecutionKernel value) noexcept;

void pack_nonlinear_int5_power_half(
    const std::array<std::uint32_t, record_dimensions>& codes,
    float amplitude, std::array<std::uint8_t,
    nonlinear_int5_record_bytes>& record);

void decode_nonlinear_int5_codes(
    const std::uint8_t* record,
    std::array<std::uint32_t, record_dimensions>& codes,
    RecordExecutionKernel kernel = RecordExecutionKernel::Portable);

float nonlinear_int5_amplitude(const std::uint8_t* record) noexcept;

float score_nonlinear_int5_power_half(
    const std::uint8_t* record, const float* query,
    RecordExecutionKernel kernel = RecordExecutionKernel::Portable);

float score_nonlinear_int5_power_half_fast(
    const std::uint8_t* record, const float* query,
    RecordExecutionKernel kernel = RecordExecutionKernel::Portable);

float score_int8(const std::uint8_t* record, const float* query);

}  // namespace agent_memory::neuroute

#endif
