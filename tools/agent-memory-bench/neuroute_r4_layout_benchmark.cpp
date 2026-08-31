#include <agent_memory.hpp>
#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <numeric>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
#include <immintrin.h>
#endif

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
#include <simdbitpacking.h>
#endif
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP_AVX2
#include <avxbitpacking.h>
#endif

#if AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
#include <zdict.h>
#include <zstd.h>
#endif

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t dimensions = 384;
constexpr std::size_t addresses_per_query = 1024;
constexpr std::size_t scalar_features = 22;
constexpr std::size_t local_hidden = 32;
constexpr std::size_t joined_dimensions = 160;
constexpr std::size_t score_hidden = 54;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

nlohmann::json read_json(const std::filesystem::path& path) {
    std::ifstream stream(path);
    require(static_cast<bool>(stream), "R4 layout JSON open failed");
    nlohmann::json value;
    stream >> value;
    return value;
}

void write_json(const std::filesystem::path& path, const nlohmann::json& value) {
    std::ofstream stream(path);
    require(static_cast<bool>(stream), "R4 layout JSON output failed");
    stream << value.dump(2) << '\n';
}

template <typename T>
std::vector<T> read_values(const std::filesystem::path& path) {
    const auto bytes = std::filesystem::file_size(path);
    require(bytes % sizeof(T) == 0, "R4 layout payload byte count differs");
    std::vector<T> values(bytes / sizeof(T));
    std::ifstream stream(path, std::ios::binary);
    stream.read(reinterpret_cast<char*>(values.data()),
                static_cast<std::streamsize>(bytes));
    require(static_cast<bool>(stream), "R4 layout payload read failed");
    return values;
}

const nlohmann::json& role(const nlohmann::json& rows, const std::string& value) {
    const auto found = std::find_if(rows.begin(), rows.end(),
        [&](const nlohmann::json& row) {
        return row.at("role").get<std::string>() == value;
    });
    require(found != rows.end(), "R4 layout role differs");
    return *found;
}

struct ProcessState {
    std::uint64_t faults = 0;
    std::uint64_t rss = 0;
};

ProcessState process_state() {
#if defined(_WIN32)
    PROCESS_MEMORY_COUNTERS counters{};
    counters.cb = sizeof(counters);
    require(GetProcessMemoryInfo(GetCurrentProcess(), &counters, sizeof(counters)) != 0,
            "R4 layout process counter failed");
    return {static_cast<std::uint64_t>(counters.PageFaultCount),
            static_cast<std::uint64_t>(counters.WorkingSetSize)};
#else
    rusage usage{};
    require(getrusage(RUSAGE_SELF, &usage) == 0, "R4 layout process counter failed");
    return {static_cast<std::uint64_t>(usage.ru_minflt + usage.ru_majflt),
            static_cast<std::uint64_t>(usage.ru_maxrss) * 1024U};
#endif
}

double milliseconds(Clock::time_point begin, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - begin).count();
}

class MappedFile {
public:
    explicit MappedFile(const std::filesystem::path& path) {
#if defined(_WIN32)
        file_ = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        require(file_ != INVALID_HANDLE_VALUE, "R4 mapped file open failed");
        LARGE_INTEGER size{};
        require(GetFileSizeEx(file_, &size) != 0 && size.QuadPart > 0,
                "R4 mapped file size failed");
        size_ = static_cast<std::size_t>(size.QuadPart);
        mapping_ = CreateFileMappingW(file_, nullptr, PAGE_READONLY, 0, 0, nullptr);
        require(mapping_ != nullptr, "R4 file mapping failed");
        data_ = static_cast<const std::uint8_t*>(
            MapViewOfFile(mapping_, FILE_MAP_READ, 0, 0, 0));
        require(data_ != nullptr, "R4 mapped view failed");
#else
        file_ = open(path.c_str(), O_RDONLY);
        require(file_ >= 0, "R4 mapped file open failed");
        struct stat status{};
        require(fstat(file_, &status) == 0 && status.st_size > 0,
                "R4 mapped file size failed");
        size_ = static_cast<std::size_t>(status.st_size);
        const auto view = mmap(nullptr, size_, PROT_READ, MAP_SHARED, file_, 0);
        require(view != MAP_FAILED, "R4 mapped view failed");
        data_ = static_cast<const std::uint8_t*>(view);
#endif
    }

    MappedFile(const MappedFile&) = delete;
    MappedFile& operator=(const MappedFile&) = delete;

    ~MappedFile() {
#if defined(_WIN32)
        if (data_ != nullptr) UnmapViewOfFile(data_);
        if (mapping_ != nullptr) CloseHandle(mapping_);
        if (file_ != INVALID_HANDLE_VALUE) CloseHandle(file_);
#else
        if (data_ != nullptr) munmap(const_cast<std::uint8_t*>(data_), size_);
        if (file_ >= 0) close(file_);
#endif
    }

    const std::uint8_t* data() const { return data_; }
    std::size_t size() const { return size_; }

private:
    const std::uint8_t* data_ = nullptr;
    std::size_t size_ = 0;
#if defined(_WIN32)
    HANDLE file_ = INVALID_HANDLE_VALUE;
    HANDLE mapping_ = nullptr;
#else
    int file_ = -1;
#endif
};

struct Model {
    std::vector<float> feature_mean, feature_deviation;
    std::vector<float> query_weight, query_bias;
    std::vector<float> local_weight, local_bias;
    std::vector<float> score_weight1, score_bias1;
    std::vector<float> score_weight2, score_bias2;
    std::vector<float> aggregate_mean, aggregate_deviation;
};

struct SeedContext {
    std::uint64_t seed = 0;
    std::filesystem::path root;
    std::vector<std::uint32_t> address_offsets, address_counts;
    std::vector<std::uint32_t> representative_offsets;
    std::vector<std::uint8_t> representative_counts;
    std::vector<std::int32_t> representative_documents;
    std::vector<std::uint32_t> occupied_addresses;
    std::vector<std::int32_t> physical_to_document;
    std::vector<float> queries, features;
    std::vector<std::uint32_t> shortlists;
    Model model;
    nlohmann::json layouts;
};

std::filesystem::path payload_path(const std::filesystem::path& root,
                                   const nlohmann::json& row) {
    auto current = root;
    if (row.contains("external_root")) current /= row.at("external_root").get<std::string>();
    return current / row.at("file").get<std::string>();
}

SeedContext load_seed(const std::filesystem::path& manifest_path,
                      const nlohmann::json& row) {
    const auto root = manifest_path.parent_path() /
                      ("seed-" + std::to_string(row.at("seed").get<std::uint64_t>()));
    const auto& mappings = row.at("mappings");
    const auto path = [&](const std::string& name) {
        return payload_path(root, role(mappings, name));
    };
    SeedContext value;
    value.seed = row.at("seed");
    value.root = root;
    value.address_offsets = read_values<std::uint32_t>(path("address_offsets"));
    value.address_counts = read_values<std::uint32_t>(path("address_counts"));
    value.representative_counts = read_values<std::uint8_t>(path("representative_counts"));
    value.representative_offsets.resize(value.representative_counts.size());
    for (std::size_t index = 1; index != value.representative_offsets.size(); ++index) {
        value.representative_offsets[index] = value.representative_offsets[index - 1] +
            value.representative_counts[index - 1];
    }
    value.representative_documents = read_values<std::int32_t>(
        path("representative_documents"));
    value.occupied_addresses = read_values<std::uint32_t>(path("occupied_addresses"));
    value.physical_to_document = read_values<std::int32_t>(path("physical_to_document"));
    value.queries = read_values<float>(path("query_vectors"));
    value.shortlists = read_values<std::uint32_t>(path("shortlist_rows"));
    value.features = read_values<float>(path("scalar_features"));
    require(value.queries.size() == 152 * dimensions &&
            value.shortlists.size() == 152 * addresses_per_query &&
            value.features.size() == 152 * addresses_per_query * scalar_features &&
            value.occupied_addresses.size() == value.address_counts.size() &&
            value.physical_to_document.size() == 1000000,
            "R4 layout request payload differs");
    const auto& model = row.at("model");
    const auto model_path = [&](const std::string& name) {
        return payload_path(root, role(model, "model_" + name));
    };
    value.model.feature_mean = read_values<float>(model_path("feature_mean"));
    value.model.feature_deviation = read_values<float>(model_path("feature_deviation"));
    value.model.query_weight = read_values<float>(model_path("query_weight"));
    value.model.query_bias = read_values<float>(model_path("query_bias"));
    value.model.local_weight = read_values<float>(model_path("local_weight"));
    value.model.local_bias = read_values<float>(model_path("local_bias"));
    value.model.score_weight1 = read_values<float>(model_path("score_weight1"));
    value.model.score_bias1 = read_values<float>(model_path("score_bias1"));
    value.model.score_weight2 = read_values<float>(model_path("score_weight2"));
    value.model.score_bias2 = read_values<float>(model_path("score_bias2"));
    value.model.aggregate_mean = read_values<float>(model_path("r4_aggregate_mean"));
    value.model.aggregate_deviation = read_values<float>(
        model_path("r4_aggregate_deviation"));
    value.layouts = row.at("layouts");
    return value;
}

const nlohmann::json& layout_row(const SeedContext& seed, const std::string& id) {
    return role(seed.layouts, id);
}

struct Sample {
    double fetch_ms = 0, decode_ms = 0, dot_ms = 0, score_ms = 0, total_ms = 0;
    std::uint64_t logical_bytes = 0, random_reads = 0, representatives = 0;
    std::uint64_t page_faults = 0;
    std::uint64_t address_spans = 0;
    std::int64_t rss_delta = 0;
    std::string score_sha256;
    double maximum_max_abs_error = 0, address_score_max_abs_error = 0;
    double representative_winner_agreement = 1, address_top128_overlap = 1;
};

struct Maximums {
    std::vector<float> values;
    std::vector<std::uint8_t> winners;
};

float int8_dot_scalar(const std::uint8_t* codes, float scale,
                      const float* query) {
    float score = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
        const float decoded = static_cast<float>(
            static_cast<int>(codes[dimension]) - 127) * scale;
        score += decoded * query[dimension];
    }
    return score;
}

float int8_dot_avx2(const std::uint8_t* codes, float scale,
                    const float* query) {
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    const auto offset = _mm256_set1_epi32(127);
    const auto scale8 = _mm256_set1_ps(scale);
    __m256 sum0 = _mm256_setzero_ps();
    __m256 sum1 = _mm256_setzero_ps();
    __m256 sum2 = _mm256_setzero_ps();
    __m256 sum3 = _mm256_setzero_ps();
    for (std::size_t dimension = 0; dimension != dimensions; dimension += 32) {
        const auto load = [&](std::size_t lane) {
            const auto bytes = _mm_loadl_epi64(reinterpret_cast<const __m128i*>(
                codes + dimension + lane));
            return _mm256_cvtepi32_ps(_mm256_sub_epi32(
                _mm256_cvtepu8_epi32(bytes), offset));
        };
        sum0 = _mm256_add_ps(sum0, _mm256_mul_ps(
            _mm256_mul_ps(load(0), scale8),
            _mm256_loadu_ps(query + dimension)));
        sum1 = _mm256_add_ps(sum1, _mm256_mul_ps(
            _mm256_mul_ps(load(8), scale8),
            _mm256_loadu_ps(query + dimension + 8)));
        sum2 = _mm256_add_ps(sum2, _mm256_mul_ps(
            _mm256_mul_ps(load(16), scale8),
            _mm256_loadu_ps(query + dimension + 16)));
        sum3 = _mm256_add_ps(sum3, _mm256_mul_ps(
            _mm256_mul_ps(load(24), scale8),
            _mm256_loadu_ps(query + dimension + 24)));
    }
    const auto sum = _mm256_add_ps(_mm256_add_ps(sum0, sum1),
                                   _mm256_add_ps(sum2, sum3));
    alignas(32) std::array<float, 8> lanes{};
    _mm256_store_ps(lanes.data(), sum);
    float result = 0.0F;
    for (const auto value : lanes) result += value;
    return result;
#else
    return int8_dot_scalar(codes, scale, query);
#endif
}

float int8_dot_avx2_ordered(const std::uint8_t* codes, float scale,
                            const float* query) {
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    const auto offset = _mm256_set1_epi32(127);
    const auto scale8 = _mm256_set1_ps(scale);
    alignas(32) std::array<float, 8> products{};
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; dimension += 8) {
        const auto bytes = _mm_loadl_epi64(reinterpret_cast<const __m128i*>(
            codes + dimension));
        const auto values = _mm256_cvtepi32_ps(_mm256_sub_epi32(
            _mm256_cvtepu8_epi32(bytes), offset));
        _mm256_store_ps(products.data(), _mm256_mul_ps(
            _mm256_mul_ps(values, scale8),
            _mm256_loadu_ps(query + dimension)));
        for (const auto value : products) result += value;
    }
    return result;
#else
    return int8_dot_scalar(codes, scale, query);
#endif
}

void simdcomp_unpack_block(const std::uint8_t* bytes, unsigned bits,
                           std::uint32_t* output);

std::array<float, 32> int5_power_half_decode_table() {
    std::array<float, 32> result{};
    constexpr float maximum = 15.0F;
    for (int code = 0; code <= 30; ++code) {
        const auto signed_code = code - 15;
        const auto magnitude = static_cast<float>(std::abs(signed_code)) / maximum;
        result[static_cast<std::size_t>(code)] = std::copysign(
            magnitude * magnitude, static_cast<float>(signed_code));
    }
    return result;
}

float int5_power_half_dot_avx2_legacy(const std::uint8_t* record,
                                      const std::array<float, 32>& table,
                                      const float* query) {
    alignas(32) std::array<std::uint32_t, dimensions> unpacked{};
    for (std::size_t block = 0; block != 3; ++block) {
        simdcomp_unpack_block(record + block * 80U, 5,
                              unpacked.data() + block * 128U);
    }
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    (void)table;
    const auto center = _mm256_set1_epi32(15);
    const auto sign_mask = _mm256_set1_ps(-0.0F);
    const auto scale8 = _mm256_set1_ps(amplitude / 225.0F);
    __m256 sum0 = _mm256_setzero_ps();
    __m256 sum1 = _mm256_setzero_ps();
    __m256 sum2 = _mm256_setzero_ps();
    __m256 sum3 = _mm256_setzero_ps();
    for (std::size_t dimension = 0; dimension != dimensions; dimension += 32) {
        const auto load = [&](std::size_t lane) {
            const auto indices = _mm256_load_si256(
                reinterpret_cast<const __m256i*>(
                    unpacked.data() + dimension + lane));
            const auto signed_values = _mm256_cvtepi32_ps(
                _mm256_sub_epi32(indices, center));
            const auto magnitudes = _mm256_andnot_ps(sign_mask, signed_values);
            return _mm256_mul_ps(_mm256_mul_ps(
                signed_values, magnitudes), scale8);
        };
        sum0 = _mm256_add_ps(sum0, _mm256_mul_ps(
            load(0), _mm256_loadu_ps(query + dimension)));
        sum1 = _mm256_add_ps(sum1, _mm256_mul_ps(
            load(8), _mm256_loadu_ps(query + dimension + 8)));
        sum2 = _mm256_add_ps(sum2, _mm256_mul_ps(
            load(16), _mm256_loadu_ps(query + dimension + 16)));
        sum3 = _mm256_add_ps(sum3, _mm256_mul_ps(
            load(24), _mm256_loadu_ps(query + dimension + 24)));
    }
    const auto sum = _mm256_add_ps(_mm256_add_ps(sum0, sum1),
                                   _mm256_add_ps(sum2, sum3));
    alignas(32) std::array<float, 8> lanes{};
    _mm256_store_ps(lanes.data(), sum);
    return std::accumulate(lanes.begin(), lanes.end(), 0.0F);
#else
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
        result += table[unpacked[dimension]] * amplitude * query[dimension];
    }
    return result;
#endif
}

float int5_power_half_dot_avx2(const std::uint8_t* record,
                               const float* query) {
    alignas(32) std::array<std::uint32_t, dimensions> unpacked;
    for (std::size_t block = 0; block != 3; ++block)
        simdcomp_unpack_block(record + block * 80U, 5,
                              unpacked.data() + block * 128U);
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    const auto center = _mm256_set1_epi32(15);
    __m256 sum0 = _mm256_setzero_ps();
    __m256 sum1 = _mm256_setzero_ps();
    __m256 sum2 = _mm256_setzero_ps();
    __m256 sum3 = _mm256_setzero_ps();
    for (std::size_t dimension = 0; dimension != dimensions; dimension += 32) {
        const auto load = [&](std::size_t lane) {
            const auto signed_values = _mm256_sub_epi32(_mm256_load_si256(
                reinterpret_cast<const __m256i*>(
                    unpacked.data() + dimension + lane)), center);
            return _mm256_cvtepi32_ps(_mm256_mullo_epi32(
                signed_values, _mm256_abs_epi32(signed_values)));
        };
        sum0 = _mm256_add_ps(sum0, _mm256_mul_ps(
            load(0), _mm256_loadu_ps(query + dimension)));
        sum1 = _mm256_add_ps(sum1, _mm256_mul_ps(
            load(8), _mm256_loadu_ps(query + dimension + 8)));
        sum2 = _mm256_add_ps(sum2, _mm256_mul_ps(
            load(16), _mm256_loadu_ps(query + dimension + 16)));
        sum3 = _mm256_add_ps(sum3, _mm256_mul_ps(
            load(24), _mm256_loadu_ps(query + dimension + 24)));
    }
    alignas(32) std::array<float, 8> lanes;
    _mm256_store_ps(lanes.data(), _mm256_add_ps(
        _mm256_add_ps(sum0, sum1), _mm256_add_ps(sum2, sum3)));
    return std::accumulate(lanes.begin(), lanes.end(), 0.0F) *
           amplitude / 225.0F;
#else
    const auto table = int5_power_half_decode_table();
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
        result += table[unpacked[dimension]] * query[dimension];
    return result * amplitude;
#endif
}

#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
template <std::size_t Group>
__m128i int5_sse_group(const std::array<__m128i, 5>& words) {
    constexpr std::size_t bit = Group * 5U;
    constexpr std::size_t word = bit / 32U;
    constexpr int shift = static_cast<int>(bit % 32U);
    const auto mask = _mm_set1_epi32(31);
    if constexpr (shift <= 27) {
        return _mm_and_si128(mask, _mm_srli_epi32(words[word], shift));
    } else {
        return _mm_and_si128(mask, _mm_or_si128(
            _mm_srli_epi32(words[word], shift),
            _mm_slli_epi32(words[word + 1], 32 - shift)));
    }
}

template <std::size_t Group>
__m256i int5_avx_group(const std::array<__m256i, 5>& words) {
    constexpr std::size_t bit = Group * 5U;
    constexpr std::size_t word = bit / 32U;
    constexpr int shift = static_cast<int>(bit % 32U);
    const auto mask = _mm256_set1_epi32(31);
    if constexpr (shift <= 27) {
        return _mm256_and_si256(mask,
                                _mm256_srli_epi32(words[word], shift));
    } else {
        return _mm256_and_si256(mask, _mm256_or_si256(
            _mm256_srli_epi32(words[word], shift),
            _mm256_slli_epi32(words[word + 1], 32 - shift)));
    }
}

template <std::size_t Group>
void accumulate_int5_sse_group(const std::array<__m128i, 5>& words,
                               const float* query,
                               std::array<__m128, 4>& sums) {
    const auto signed_values = _mm_sub_epi32(
        int5_sse_group<Group>(words), _mm_set1_epi32(15));
    const auto squared = _mm_mullo_epi32(signed_values,
                                         _mm_abs_epi32(signed_values));
    sums[Group % sums.size()] = _mm_add_ps(sums[Group % sums.size()],
        _mm_mul_ps(_mm_cvtepi32_ps(squared),
                   _mm_loadu_ps(query + Group * 4U)));
}

template <std::size_t... Groups>
float int5_sse_fused_block(const std::uint8_t* packed, const float* query,
                           std::index_sequence<Groups...>) {
    std::array<__m128i, 5> words;
    for (std::size_t word = 0; word != words.size(); ++word)
        words[word] = _mm_loadu_si128(reinterpret_cast<const __m128i*>(
            packed + word * sizeof(__m128i)));
    std::array<__m128, 4> sums = {_mm_setzero_ps(), _mm_setzero_ps(),
        _mm_setzero_ps(), _mm_setzero_ps()};
    (accumulate_int5_sse_group<Groups>(words, query, sums), ...);
    const auto sum = _mm_add_ps(_mm_add_ps(sums[0], sums[1]),
                                _mm_add_ps(sums[2], sums[3]));
    alignas(16) std::array<float, 4> lanes;
    _mm_store_ps(lanes.data(), sum);
    return std::accumulate(lanes.begin(), lanes.end(), 0.0F);
}

template <std::size_t Group>
void accumulate_int5_avx_group(const std::array<__m256i, 5>& words,
                               const float* query,
                               std::array<__m256, 4>& sums) {
    const auto signed_values = _mm256_sub_epi32(
        int5_avx_group<Group>(words), _mm256_set1_epi32(15));
    const auto squared = _mm256_mullo_epi32(
        signed_values, _mm256_abs_epi32(signed_values));
    sums[Group % sums.size()] = _mm256_add_ps(sums[Group % sums.size()],
        _mm256_mul_ps(_mm256_cvtepi32_ps(squared),
                      _mm256_loadu_ps(query + Group * 8U)));
}

template <std::size_t... Groups>
float int5_avx_fused_block(const std::uint8_t* packed, const float* query,
                           std::index_sequence<Groups...>) {
    std::array<__m256i, 5> words;
    for (std::size_t word = 0; word != words.size(); ++word)
        words[word] = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
            packed + word * sizeof(__m256i)));
    std::array<__m256, 4> sums = {_mm256_setzero_ps(), _mm256_setzero_ps(),
        _mm256_setzero_ps(), _mm256_setzero_ps()};
    (accumulate_int5_avx_group<Groups>(words, query, sums), ...);
    const auto sum = _mm256_add_ps(_mm256_add_ps(sums[0], sums[1]),
                                   _mm256_add_ps(sums[2], sums[3]));
    alignas(32) std::array<float, 8> lanes;
    _mm256_store_ps(lanes.data(), sum);
    return std::accumulate(lanes.begin(), lanes.end(), 0.0F);
}

template <std::size_t Group>
void accumulate_int5_sse_integer_group(
        const std::array<__m128i, 5>& words, const std::int16_t* query,
        std::array<__m128i, 4>& sums) {
    const auto signed_values = _mm_sub_epi32(
        int5_sse_group<Group>(words), _mm_set1_epi32(15));
    const auto squared = _mm_mullo_epi32(signed_values,
                                         _mm_abs_epi32(signed_values));
    const auto coefficients = _mm_packs_epi32(squared, _mm_setzero_si128());
    const auto query4 = _mm_loadl_epi64(reinterpret_cast<const __m128i*>(
        query + Group * 4U));
    sums[Group % sums.size()] = _mm_add_epi32(sums[Group % sums.size()],
        _mm_madd_epi16(coefficients, query4));
}

template <std::size_t... Groups>
std::int64_t int5_sse_fused_integer_block(
        const std::uint8_t* packed, const std::int16_t* query,
        std::index_sequence<Groups...>) {
    std::array<__m128i, 5> words;
    for (std::size_t word = 0; word != words.size(); ++word)
        words[word] = _mm_loadu_si128(reinterpret_cast<const __m128i*>(
            packed + word * sizeof(__m128i)));
    std::array<__m128i, 4> sums = {_mm_setzero_si128(), _mm_setzero_si128(),
        _mm_setzero_si128(), _mm_setzero_si128()};
    (accumulate_int5_sse_integer_group<Groups>(words, query, sums), ...);
    const auto sum = _mm_add_epi32(_mm_add_epi32(sums[0], sums[1]),
                                   _mm_add_epi32(sums[2], sums[3]));
    alignas(16) std::array<std::int32_t, 4> lanes;
    _mm_store_si128(reinterpret_cast<__m128i*>(lanes.data()), sum);
    return std::accumulate(lanes.begin(), lanes.end(), std::int64_t{0});
}

template <std::size_t Group>
void accumulate_int5_avx_integer_group(
        const std::array<__m256i, 5>& words, const std::int16_t* query,
        std::array<__m128i, 4>& sums) {
    const auto signed_values = _mm256_sub_epi32(
        int5_avx_group<Group>(words), _mm256_set1_epi32(15));
    const auto squared = _mm256_mullo_epi32(
        signed_values, _mm256_abs_epi32(signed_values));
    const auto coefficients = _mm_packs_epi32(
        _mm256_castsi256_si128(squared),
        _mm256_extracti128_si256(squared, 1));
    const auto query8 = _mm_loadu_si128(reinterpret_cast<const __m128i*>(
        query + Group * 8U));
    sums[Group % sums.size()] = _mm_add_epi32(sums[Group % sums.size()],
        _mm_madd_epi16(coefficients, query8));
}

template <std::size_t... Groups>
std::int64_t int5_avx_fused_integer_block(
        const std::uint8_t* packed, const std::int16_t* query,
        std::index_sequence<Groups...>) {
    std::array<__m256i, 5> words;
    for (std::size_t word = 0; word != words.size(); ++word)
        words[word] = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
            packed + word * sizeof(__m256i)));
    std::array<__m128i, 4> sums = {_mm_setzero_si128(), _mm_setzero_si128(),
        _mm_setzero_si128(), _mm_setzero_si128()};
    (accumulate_int5_avx_integer_group<Groups>(words, query, sums), ...);
    const auto sum = _mm_add_epi32(_mm_add_epi32(sums[0], sums[1]),
                                   _mm_add_epi32(sums[2], sums[3]));
    alignas(16) std::array<std::int32_t, 4> lanes;
    _mm_store_si128(reinterpret_cast<__m128i*>(lanes.data()), sum);
    return std::accumulate(lanes.begin(), lanes.end(), std::int64_t{0});
}
#endif

float int5_power_half_fused_sse_dot(const std::uint8_t* record,
                                    const float* query) {
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
    float sum = 0.0F;
    for (std::size_t block = 0; block != 3; ++block)
        sum += int5_sse_fused_block(record + block * 80U,
            query + block * 128U, std::make_index_sequence<32>{});
    return sum * amplitude / 225.0F;
#else
    return int5_power_half_dot_avx2(record, query);
#endif
}

float int5_power_half_fused_avx2_dot(const std::uint8_t* record,
                                     const float* query) {
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
    const auto sum = int5_avx_fused_block(record, query,
        std::make_index_sequence<32>{}) +
        int5_sse_fused_block(record + 160U, query + 256U,
            std::make_index_sequence<32>{});
    return sum * amplitude / 225.0F;
#else
    return int5_power_half_dot_avx2(record, query);
#endif
}

float int5_power_half_fused_avx2_integer_dot(
        const std::uint8_t* record, const std::int16_t* query,
        float query_scale) {
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
    const auto sum = int5_avx_fused_integer_block(record, query,
        std::make_index_sequence<32>{}) +
        int5_sse_fused_integer_block(record + 160U, query + 256U,
            std::make_index_sequence<32>{});
    return static_cast<float>(sum) * query_scale * amplitude / 225.0F;
#else
    (void)record;
    (void)query;
    (void)query_scale;
    throw std::runtime_error("R4 nonlinear INT5 fused integer dot requires AVX2");
#endif
}

float int5_power_half_dot_pshufb(const std::uint8_t* record,
                                 const std::array<float, 32>& table,
                                 const float* query) {
    alignas(32) std::array<std::uint32_t, dimensions> unpacked{};
    for (std::size_t block = 0; block != 3; ++block) {
        simdcomp_unpack_block(record + block * 80U, 5,
                              unpacked.data() + block * 128U);
    }
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    (void)table;
    const auto center = _mm256_set1_epi32(15);
    const auto zero = _mm256_setzero_si256();
    const auto square_lut = _mm_setr_epi8(
        0, 1, 4, 9, 16, 25, 36, 49,
        64, 81, 100, 121, static_cast<char>(144), static_cast<char>(169),
        static_cast<char>(196), static_cast<char>(225));
    const auto scale8 = _mm256_set1_ps(amplitude / 225.0F);
    __m256 sum0 = _mm256_setzero_ps();
    __m256 sum1 = _mm256_setzero_ps();
    for (std::size_t dimension = 0; dimension != dimensions; dimension += 16) {
        const auto score8 = [&](std::size_t offset) {
            const auto signed_codes = _mm256_sub_epi32(_mm256_load_si256(
                reinterpret_cast<const __m256i*>(unpacked.data() + offset)),
                center);
            const auto magnitude = _mm256_abs_epi32(signed_codes);
            const auto low = _mm256_castsi256_si128(magnitude);
            const auto high = _mm256_extracti128_si256(magnitude, 1);
            const auto magnitude16 = _mm_packs_epi32(low, high);
            const auto magnitude8 = _mm_packus_epi16(magnitude16,
                                                     _mm_setzero_si128());
            const auto squared8 = _mm_shuffle_epi8(square_lut, magnitude8);
            auto squared = _mm256_cvtepu8_epi32(squared8);
            const auto negative = _mm256_cmpgt_epi32(zero, signed_codes);
            squared = _mm256_sub_epi32(_mm256_xor_si256(squared, negative),
                                       negative);
            return _mm256_mul_ps(_mm256_cvtepi32_ps(squared), scale8);
        };
        sum0 = _mm256_add_ps(sum0, _mm256_mul_ps(score8(dimension),
            _mm256_loadu_ps(query + dimension)));
        sum1 = _mm256_add_ps(sum1, _mm256_mul_ps(score8(dimension + 8),
            _mm256_loadu_ps(query + dimension + 8)));
    }
    alignas(32) std::array<float, 8> lanes{};
    _mm256_store_ps(lanes.data(), _mm256_add_ps(sum0, sum1));
    return std::accumulate(lanes.begin(), lanes.end(), 0.0F);
#else
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
        result += table[unpacked[dimension]] * amplitude * query[dimension];
    return result;
#endif
}

struct PowerHalfQueryLuts {
    std::array<std::array<float, 16>, dimensions / 4> fp32;
    std::array<std::array<std::int32_t, 16>, dimensions / 4> int8;
    alignas(32) std::array<std::int16_t, dimensions> direct_int8;
    alignas(32) std::array<std::int16_t, dimensions> direct_int16;
    float int8_scale = 1.0F;
    float int16_scale = 1.0F;
};

PowerHalfQueryLuts power_half_query_luts(
        const float* query, bool fp32_lut, bool int8_lut,
        bool direct_int8, bool direct_int16) {
    PowerHalfQueryLuts result;
    const bool needs_int8 = int8_lut || direct_int8;
    if (needs_int8 || direct_int16) {
        float maximum = 0.0F;
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
            maximum = std::max(maximum, std::abs(query[dimension]));
        if (needs_int8)
            result.int8_scale = maximum == 0.0F ? 1.0F : maximum / 127.0F;
        if (direct_int16)
            result.int16_scale = maximum == 0.0F ? 1.0F : maximum / 32767.0F;
    }
    if (direct_int8) {
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
            result.direct_int8[dimension] = static_cast<std::int16_t>(
                std::nearbyint(query[dimension] / result.int8_scale));
    }
    if (direct_int16) {
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
            result.direct_int16[dimension] = static_cast<std::int16_t>(
                std::nearbyint(query[dimension] / result.int16_scale));
    }
    if (fp32_lut) result.fp32 = {};
    if (int8_lut) result.int8 = {};
    if (fp32_lut || int8_lut) {
        for (std::size_t group = 0; group != dimensions / 4; ++group) {
            std::array<std::int32_t, 4> quantized{};
            if (int8_lut) {
                for (std::size_t lane = 0; lane != 4; ++lane) {
                    quantized[lane] = static_cast<std::int32_t>(std::nearbyint(
                        query[group * 4 + lane] / result.int8_scale));
                }
            }
            for (std::size_t mask = 0; mask != 16; ++mask) {
                for (std::size_t lane = 0; lane != 4; ++lane) {
                    if ((mask & (1U << lane)) == 0) continue;
                    if (fp32_lut)
                        result.fp32[group][mask] += query[group * 4 + lane];
                    if (int8_lut)
                        result.int8[group][mask] += quantized[lane];
                }
            }
        }
    }
    return result;
}

void int5_power_half_bitslice_record(const std::uint8_t* source,
                                     std::uint8_t* destination) {
    alignas(32) std::array<std::uint32_t, dimensions> unpacked{};
    for (std::size_t block = 0; block != 3; ++block)
        simdcomp_unpack_block(source + block * 80U, 5,
                              unpacked.data() + block * 128U);
    for (std::size_t block = 0; block != dimensions / 32; ++block) {
        std::array<std::uint32_t, 5> planes{};
        for (std::size_t lane = 0; lane != 32; ++lane) {
            const auto signed_code = static_cast<std::int32_t>(
                unpacked[block * 32 + lane]) - 15;
            const auto magnitude = static_cast<std::uint32_t>(
                std::abs(signed_code));
            const auto bit = std::uint32_t{1} << lane;
            if (signed_code < 0) planes[0] |= bit;
            for (std::size_t plane = 0; plane != 4; ++plane)
                if ((magnitude & (1U << plane)) != 0)
                    planes[plane + 1] |= bit;
        }
        std::memcpy(destination + block * 20U, planes.data(), 20U);
    }
    std::memcpy(destination + 240U, source + 240U, sizeof(float));
}

void int5_power_half_avx2_record(const std::uint8_t* source,
                                 std::uint8_t* destination) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP_AVX2
    (void)source;
    (void)destination;
    throw std::runtime_error("R4 nonlinear INT5 AVX2 layout requires SIMDComp AVX2");
#else
    alignas(32) std::array<std::uint32_t, dimensions> unpacked;
    for (std::size_t block = 0; block != 3; ++block)
        simdcomp_unpack_block(source + block * 80U, 5,
                              unpacked.data() + block * 128U);
    alignas(32) std::array<__m256i, 5> leading;
    avxpackwithoutmask(unpacked.data(), leading.data(), 5);
    std::memcpy(destination, leading.data(), 160U);
    alignas(16) std::array<__m128i, 5> tail;
    simdpackwithoutmask(unpacked.data() + 256U, tail.data(), 5);
    std::memcpy(destination + 160U, tail.data(), 80U);
    std::memcpy(destination + 240U, source + 240U, sizeof(float));
#endif
}

template <typename Value, typename Lut>
Value int5_power_half_bitsliced_sum(const std::uint8_t* record,
                                    const Lut& luts) {
    constexpr std::array<std::int32_t, 10> weights = {
        1, 4, 16, 64, 4, 8, 16, 16, 32, 64};
    Value result = 0;
    for (std::size_t block = 0; block != dimensions / 32; ++block) {
        std::array<std::uint32_t, 5> planes{};
        std::memcpy(planes.data(), record + block * 20U, 20U);
        for (std::size_t local = 0; local != 8; ++local) {
            const auto shift = static_cast<unsigned>(local * 4);
            const auto sign = (planes[0] >> shift) & 15U;
            const auto b0 = (planes[1] >> shift) & 15U;
            const auto b1 = (planes[2] >> shift) & 15U;
            const auto b2 = (planes[3] >> shift) & 15U;
            const auto b3 = (planes[4] >> shift) & 15U;
            const std::array<std::uint32_t, 10> terms = {b0, b1, b2, b3,
                b0 & b1, b0 & b2, b0 & b3, b1 & b2, b1 & b3, b2 & b3};
            const auto& lut = luts[block * 8 + local];
            for (std::size_t term = 0; term != terms.size(); ++term) {
                result += static_cast<Value>(weights[term]) *
                    (static_cast<Value>(lut[terms[term]]) -
                     static_cast<Value>(2) *
                     static_cast<Value>(lut[terms[term] & sign]));
            }
        }
    }
    return result;
}

float int5_power_half_bitsliced_dot_fp32(
        const std::uint8_t* record, const PowerHalfQueryLuts& query) {
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
    return int5_power_half_bitsliced_sum<float>(record, query.fp32) *
           amplitude / 225.0F;
}

float int5_power_half_bitsliced_dot_q8(
        const std::uint8_t* record, const PowerHalfQueryLuts& query) {
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
    return static_cast<float>(int5_power_half_bitsliced_sum<std::int64_t>(
        record, query.int8)) * query.int8_scale * amplitude / 225.0F;
}

float int5_power_half_integer_dot(
        const std::uint8_t* record, const std::int16_t* query,
        float query_scale) {
    alignas(32) std::array<std::uint32_t, dimensions> unpacked;
    for (std::size_t block = 0; block != 3; ++block)
        simdcomp_unpack_block(record + block * 80U, 5,
                              unpacked.data() + block * 128U);
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + 240U, sizeof(amplitude));
#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    const auto center = _mm256_set1_epi32(15);
    auto sum = _mm256_setzero_si256();
    for (std::size_t dimension = 0; dimension != dimensions;
         dimension += 16) {
        const auto signed0 = _mm256_sub_epi32(_mm256_load_si256(
            reinterpret_cast<const __m256i*>(unpacked.data() + dimension)),
            center);
        const auto signed1 = _mm256_sub_epi32(_mm256_load_si256(
            reinterpret_cast<const __m256i*>(
                unpacked.data() + dimension + 8)), center);
        const auto squared0 = _mm256_mullo_epi32(
            signed0, _mm256_abs_epi32(signed0));
        const auto squared1 = _mm256_mullo_epi32(
            signed1, _mm256_abs_epi32(signed1));
        auto coefficients = _mm256_packs_epi32(squared0, squared1);
        coefficients = _mm256_permute4x64_epi64(coefficients, 0xD8);
        sum = _mm256_add_epi32(sum, _mm256_madd_epi16(coefficients,
            _mm256_load_si256(reinterpret_cast<const __m256i*>(
                query + dimension))));
    }
    alignas(32) std::array<std::int32_t, 8> lanes{};
    _mm256_store_si256(reinterpret_cast<__m256i*>(lanes.data()), sum);
    const auto integer_sum = std::accumulate(lanes.begin(), lanes.end(),
        std::int64_t{0});
#else
    std::int64_t integer_sum = 0;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
        const auto code = static_cast<std::int32_t>(unpacked[dimension]) - 15;
        integer_sum += static_cast<std::int64_t>(code * std::abs(code)) *
                       query[dimension];
    }
#endif
    return static_cast<float>(integer_sum) * query_scale * amplitude / 225.0F;
}

Maximums fused_int8_maximums(const std::vector<std::uint8_t>& bytes,
                             const std::vector<std::size_t>& starts,
                             std::size_t record_bytes, const float* query,
                             const std::string& kernel) {
    Maximums result{{}, {}};
    result.values.assign(addresses_per_query,
                         -std::numeric_limits<float>::infinity());
    result.winners.assign(addresses_per_query, 0);
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        std::uint8_t winner = 0;
        for (std::size_t offset = starts[address], slot = 0;
             offset != starts[address + 1]; offset += record_bytes, ++slot) {
            float scale = 0.0F;
            std::memcpy(&scale, bytes.data() + offset + dimensions, sizeof(float));
            const auto score = kernel == "fused_int8_avx2"
                ? int8_dot_avx2(bytes.data() + offset, scale, query)
                : kernel == "fused_int8_avx2_ordered"
                    ? int8_dot_avx2_ordered(bytes.data() + offset, scale, query)
                    : int8_dot_scalar(bytes.data() + offset, scale, query);
            if (score > result.values[address]) {
                result.values[address] = score;
                winner = static_cast<std::uint8_t>(slot);
            }
        }
        result.winners[address] = winner;
    }
    return result;
}

double max_abs_error(const std::vector<float>& left,
                     const std::vector<float>& right) {
    require(left.size() == right.size(), "R4 kernel comparison size differs");
    double result = 0.0;
    for (std::size_t index = 0; index != left.size(); ++index) {
        result = std::max(result, static_cast<double>(
            std::abs(left[index] - right[index])));
    }
    return result;
}

double agreement(const std::vector<std::uint8_t>& left,
                 const std::vector<std::uint8_t>& right) {
    require(left.size() == right.size(), "R4 kernel winner size differs");
    std::size_t equal = 0;
    for (std::size_t index = 0; index != left.size(); ++index) {
        equal += left[index] == right[index] ? 1U : 0U;
    }
    return static_cast<double>(equal) / static_cast<double>(left.size());
}

double top_overlap(const std::vector<float>& left,
                   const std::vector<float>& right, std::size_t count) {
    const auto top = [&](const std::vector<float>& values) {
        std::vector<std::size_t> order(values.size());
        for (std::size_t index = 0; index != order.size(); ++index) order[index] = index;
        std::partial_sort(order.begin(), order.begin() + count, order.end(),
            [&](std::size_t a, std::size_t b) {
                if (values[a] != values[b]) return values[a] > values[b];
                return a < b;
            });
        order.resize(count);
        std::sort(order.begin(), order.end());
        return order;
    };
    const auto a = top(left);
    const auto b = top(right);
    std::vector<std::size_t> common;
    std::set_intersection(a.begin(), a.end(), b.begin(), b.end(),
                          std::back_inserter(common));
    return static_cast<double>(common.size()) / static_cast<double>(count);
}

unsigned required_bits(std::uint32_t value) {
    unsigned bits = 0;
    while (value != 0) {
        ++bits;
        value >>= 1U;
    }
    return bits;
}

void simdcomp_pack_block(const std::uint32_t* values, unsigned bits,
                         std::ostream& output) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    (void)values;
    (void)bits;
    (void)output;
    throw std::runtime_error("R4 INT8 compression requires SIMDComp");
#else
    if (bits == 0) return;
    alignas(16) std::array<__m128i, 8> packed;
    simdpackwithoutmask(values, packed.data(), bits);
    output.write(reinterpret_cast<const char*>(packed.data()),
                 static_cast<std::streamsize>(bits * 16U));
    require(static_cast<bool>(output), "R4 SIMDComp pack output failed");
#endif
}

void simdcomp_unpack_block(const std::uint8_t* bytes, unsigned bits,
                           std::uint32_t* output) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    (void)bytes;
    (void)bits;
    (void)output;
    throw std::runtime_error("R4 INT8 compression requires SIMDComp");
#else
    if (bits == 0) {
        std::fill_n(output, 128, 0U);
        return;
    }
    alignas(16) std::array<__m128i, 8> packed;
    std::memcpy(packed.data(), bytes, bits * 16U);
    simdunpack(packed.data(), output, bits);
#endif
}

void compress_int8_store(const std::filesystem::path& raw_path,
                         const std::filesystem::path& counts_path,
                         std::size_t rows,
                         const std::filesystem::path& fixed_path,
                         const std::filesystem::path& for_path,
                         const std::filesystem::path& for_offsets_path,
                         const std::filesystem::path& zigzag_path,
                         const std::filesystem::path& zigzag_offsets_path,
                         const std::filesystem::path& receipt_path) {
    const auto counts = read_values<std::uint32_t>(counts_path);
    require(!counts.empty() && counts.size() <= 65536 &&
            std::accumulate(counts.begin(), counts.end(), std::uint64_t{0}) == rows,
            "R4 compression address counts differ");
    std::ifstream raw(raw_path, std::ios::binary);
    std::ofstream fixed(fixed_path, std::ios::binary);
    std::ofstream adaptive(for_path, std::ios::binary);
    std::ofstream for_offsets(for_offsets_path, std::ios::binary);
    std::ofstream zigzag(zigzag_path, std::ios::binary);
    std::ofstream zigzag_offsets(zigzag_offsets_path, std::ios::binary);
    require(raw && fixed && adaptive && for_offsets && zigzag && zigzag_offsets,
            "R4 compression file open failed");
    std::array<std::uint8_t, dimensions> codes{};
    std::array<std::uint32_t, dimensions> values{};
    std::array<std::uint32_t, dimensions> residuals{};
    std::array<std::uint64_t, 9> fixed_hist{}, for_hist{}, zigzag_hist{};
    std::uint64_t for_position = 0, zigzag_position = 0;
    std::uint64_t for_record_min = std::numeric_limits<std::uint64_t>::max();
    std::uint64_t for_record_max = 0, zigzag_record_min = for_record_min;
    std::uint64_t zigzag_record_max = 0;
    for (const auto count : counts) {
        for_offsets.write(reinterpret_cast<const char*>(&for_position),
                          sizeof(for_position));
        zigzag_offsets.write(reinterpret_cast<const char*>(&zigzag_position),
                             sizeof(zigzag_position));
        for (std::size_t row = 0; row != count; ++row) {
            float scale = 0.0F;
            raw.read(reinterpret_cast<char*>(codes.data()), codes.size());
            raw.read(reinterpret_cast<char*>(&scale), sizeof(scale));
            require(static_cast<bool>(raw), "R4 compression raw record truncated");
            std::copy(codes.begin(), codes.end(), values.begin());
            for (std::size_t block = 0; block != 3; ++block) {
                simdcomp_pack_block(values.data() + block * 128, 8, fixed);
                ++fixed_hist[8];
            }
            fixed.write(reinterpret_cast<const char*>(&scale), sizeof(scale));

            std::array<std::uint8_t, 3> minima{}, for_bits{}, zigzag_bits{};
            for (std::size_t block = 0; block != 3; ++block) {
                const auto begin = codes.begin() + static_cast<std::ptrdiff_t>(block * 128);
                const auto [minimum, maximum] = std::minmax_element(begin, begin + 128);
                minima[block] = *minimum;
                for_bits[block] = static_cast<std::uint8_t>(
                    required_bits(static_cast<std::uint32_t>(*maximum - *minimum)));
                std::uint32_t zigzag_max = 0;
                for (std::size_t lane = 0; lane != 128; ++lane) {
                    const auto index = block * 128 + lane;
                    residuals[index] = static_cast<std::uint32_t>(
                        codes[index] - minima[block]);
                    const auto centered = static_cast<int>(codes[index]) - 127;
                    values[index] = static_cast<std::uint32_t>(centered >= 0
                        ? centered * 2 : -centered * 2 - 1);
                    zigzag_max = std::max(zigzag_max, values[index]);
                }
                zigzag_bits[block] = static_cast<std::uint8_t>(required_bits(zigzag_max));
                ++for_hist[for_bits[block]];
                ++zigzag_hist[zigzag_bits[block]];
            }
            adaptive.write(reinterpret_cast<const char*>(&scale), sizeof(scale));
            adaptive.write(reinterpret_cast<const char*>(minima.data()), minima.size());
            adaptive.write(reinterpret_cast<const char*>(for_bits.data()), for_bits.size());
            zigzag.write(reinterpret_cast<const char*>(&scale), sizeof(scale));
            zigzag.write(reinterpret_cast<const char*>(zigzag_bits.data()),
                          zigzag_bits.size());
            for (std::size_t block = 0; block != 3; ++block) {
                simdcomp_pack_block(residuals.data() + block * 128,
                                    for_bits[block], adaptive);
                simdcomp_pack_block(values.data() + block * 128,
                                    zigzag_bits[block], zigzag);
            }
            const auto for_record = 10U + 16U * (for_bits[0] + for_bits[1] +
                                                  for_bits[2]);
            const auto zigzag_record = 7U + 16U * (zigzag_bits[0] +
                                                    zigzag_bits[1] +
                                                    zigzag_bits[2]);
            for_position += for_record;
            zigzag_position += zigzag_record;
            for_record_min = std::min(for_record_min,
                                      static_cast<std::uint64_t>(for_record));
            for_record_max = std::max(for_record_max,
                                      static_cast<std::uint64_t>(for_record));
            zigzag_record_min = std::min(zigzag_record_min,
                                         static_cast<std::uint64_t>(zigzag_record));
            zigzag_record_max = std::max(zigzag_record_max,
                                         static_cast<std::uint64_t>(zigzag_record));
        }
    }
    require(raw.peek() == std::ifstream::traits_type::eof(),
            "R4 compression raw store has trailing bytes");
    fixed.close();
    adaptive.close();
    for_offsets.close();
    zigzag.close();
    zigzag_offsets.close();
    const auto histogram = [](const auto& values) {
        nlohmann::json result = nlohmann::json::array();
        for (std::size_t bits = 0; bits != values.size(); ++bits)
            result.push_back({{"bits", bits}, {"blocks", values[bits]}});
        return result;
    };
    write_json(receipt_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_compression_pack_receipt"},
        {"rows", rows}, {"raw_bytes", rows * 388U},
        {"fixed8_bytes", std::filesystem::file_size(fixed_path)},
        {"adaptive_for_bytes", std::filesystem::file_size(for_path)},
        {"adaptive_zigzag_bytes", std::filesystem::file_size(zigzag_path)},
        {"adaptive_for_record_min", for_record_min},
        {"adaptive_for_record_max", for_record_max},
        {"adaptive_zigzag_record_min", zigzag_record_min},
        {"adaptive_zigzag_record_max", zigzag_record_max},
        {"fixed8_bit_histogram", histogram(fixed_hist)},
        {"adaptive_for_bit_histogram", histogram(for_hist)},
        {"adaptive_zigzag_bit_histogram", histogram(zigzag_hist)}});
}

std::uint64_t splitmix64(std::uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}

void write_u64_offsets(const std::filesystem::path& path,
                       const std::vector<std::uint64_t>& values) {
    std::ofstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "R4 lossless offset output open failed");
    stream.write(reinterpret_cast<const char*>(values.data()),
                 static_cast<std::streamsize>(values.size() * sizeof(values[0])));
    require(static_cast<bool>(stream), "R4 lossless offset output failed");
}

void append_vbyte(std::uint32_t value, std::vector<std::uint8_t>& output) {
    while (value >= 128U) {
        output.push_back(static_cast<std::uint8_t>((value & 127U) | 128U));
        value >>= 7U;
    }
    output.push_back(static_cast<std::uint8_t>(value));
}

void pack_lossless_int8_blocks(const std::filesystem::path& raw_path,
                               const std::filesystem::path& counts_path,
                               std::size_t rows, int zstd_level,
                               std::size_t dictionary_capacity,
                               std::size_t dictionary_training_blocks,
                               const std::filesystem::path& zstd_path,
                               const std::filesystem::path& zstd_offsets_path,
                               const std::filesystem::path& dictionary_path,
                               const std::filesystem::path& zstd_dictionary_path,
                               const std::filesystem::path& zstd_dictionary_offsets_path,
                               const std::filesystem::path& vbyte_path,
                               const std::filesystem::path& vbyte_offsets_path,
                               const std::filesystem::path& receipt_path) {
#if !AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
    (void)raw_path; (void)counts_path; (void)rows; (void)zstd_level;
    (void)dictionary_capacity; (void)dictionary_training_blocks;
    (void)zstd_path; (void)zstd_offsets_path; (void)dictionary_path;
    (void)zstd_dictionary_path; (void)zstd_dictionary_offsets_path;
    (void)vbyte_path; (void)vbyte_offsets_path; (void)receipt_path;
    throw std::runtime_error("R4 lossless block codec requires Zstd");
#else
    const auto counts = read_values<std::uint32_t>(counts_path);
    require(!counts.empty() && counts.size() <= 65536 && zstd_level >= 1 &&
            dictionary_capacity >= 1024 && dictionary_training_blocks >= 8 &&
            std::accumulate(counts.begin(), counts.end(), std::uint64_t{0}) == rows,
            "R4 lossless block codec contract differs");
    MappedFile raw(raw_path);
    require(raw.size() == rows * 388U, "R4 lossless raw store size differs");
    std::vector<std::uint64_t> raw_offsets(counts.size() + 1, 0);
    for (std::size_t row = 0; row != counts.size(); ++row)
        raw_offsets[row + 1] = raw_offsets[row] + counts[row] * 388ULL;

    std::vector<std::size_t> sample_rows(counts.size());
    std::iota(sample_rows.begin(), sample_rows.end(), 0U);
    std::sort(sample_rows.begin(), sample_rows.end(), [](const auto left,
                                                         const auto right) {
        const auto left_hash = splitmix64(left ^ 0x52345f7a9d31ULL);
        const auto right_hash = splitmix64(right ^ 0x52345f7a9d31ULL);
        return std::tie(left_hash, left) < std::tie(right_hash, right);
    });
    sample_rows.resize(std::min(dictionary_training_blocks, sample_rows.size()));
    std::vector<std::size_t> sample_sizes;
    std::vector<std::uint8_t> sample_bytes;
    for (const auto row : sample_rows) {
        const auto size = static_cast<std::size_t>(raw_offsets[row + 1] -
                                                   raw_offsets[row]);
        sample_sizes.push_back(size);
        sample_bytes.insert(sample_bytes.end(), raw.data() + raw_offsets[row],
                            raw.data() + raw_offsets[row + 1]);
    }
    std::vector<std::uint8_t> dictionary(dictionary_capacity);
    const auto dictionary_size = ZDICT_trainFromBuffer(
        dictionary.data(), dictionary.size(), sample_bytes.data(),
        sample_sizes.data(), static_cast<unsigned>(sample_sizes.size()));
    require(ZDICT_isError(dictionary_size) == 0,
            ZDICT_getErrorName(dictionary_size));
    dictionary.resize(dictionary_size);
    {
        std::ofstream stream(dictionary_path, std::ios::binary);
        stream.write(reinterpret_cast<const char*>(dictionary.data()),
                     static_cast<std::streamsize>(dictionary.size()));
        require(static_cast<bool>(stream), "R4 Zstd dictionary output failed");
    }

    std::ofstream zstd_output(zstd_path, std::ios::binary);
    std::ofstream dictionary_output(zstd_dictionary_path, std::ios::binary);
    std::ofstream vbyte_output(vbyte_path, std::ios::binary);
    require(zstd_output && dictionary_output && vbyte_output,
            "R4 lossless payload output open failed");
    std::unique_ptr<ZSTD_CCtx, decltype(&ZSTD_freeCCtx)> context(
        ZSTD_createCCtx(), &ZSTD_freeCCtx);
    std::unique_ptr<ZSTD_CDict, decltype(&ZSTD_freeCDict)> compiled_dictionary(
        ZSTD_createCDict(dictionary.data(), dictionary.size(), zstd_level),
        &ZSTD_freeCDict);
    require(context != nullptr && compiled_dictionary != nullptr,
            "R4 Zstd compression context failed");
    std::vector<std::uint64_t> zstd_offsets{0}, dictionary_offsets{0},
        vbyte_offsets{0};
    std::vector<std::uint8_t> compressed;
    std::vector<std::uint8_t> vbyte;
    std::uint64_t raw_block_min = std::numeric_limits<std::uint64_t>::max();
    std::uint64_t raw_block_max = 0, zstd_block_min = raw_block_min;
    std::uint64_t zstd_block_max = 0, dictionary_block_min = raw_block_min;
    std::uint64_t dictionary_block_max = 0, vbyte_block_min = raw_block_min;
    std::uint64_t vbyte_block_max = 0;
    for (std::size_t row = 0; row != counts.size(); ++row) {
        const auto* source = raw.data() + raw_offsets[row];
        const auto source_size = static_cast<std::size_t>(raw_offsets[row + 1] -
                                                          raw_offsets[row]);
        compressed.resize(ZSTD_compressBound(source_size));
        auto compressed_size = ZSTD_compressCCtx(
            context.get(), compressed.data(), compressed.size(), source,
            source_size, zstd_level);
        require(ZSTD_isError(compressed_size) == 0,
                ZSTD_getErrorName(compressed_size));
        zstd_output.write(reinterpret_cast<const char*>(compressed.data()),
                          static_cast<std::streamsize>(compressed_size));
        zstd_offsets.push_back(zstd_offsets.back() + compressed_size);

        compressed_size = ZSTD_compress_usingCDict(
            context.get(), compressed.data(), compressed.size(), source,
            source_size, compiled_dictionary.get());
        require(ZSTD_isError(compressed_size) == 0,
                ZSTD_getErrorName(compressed_size));
        dictionary_output.write(reinterpret_cast<const char*>(compressed.data()),
                                static_cast<std::streamsize>(compressed_size));
        dictionary_offsets.push_back(dictionary_offsets.back() + compressed_size);

        vbyte.clear();
        vbyte.reserve(source_size + source_size / 2);
        for (std::size_t record = 0; record != counts[row]; ++record) {
            const auto* current = source + record * 388U;
            vbyte.insert(vbyte.end(), current + dimensions, current + 388U);
            for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                const auto centered = static_cast<int>(current[dimension]) - 127;
                const auto zigzag = static_cast<std::uint32_t>(centered >= 0
                    ? centered * 2 : -centered * 2 - 1);
                append_vbyte(zigzag, vbyte);
            }
        }
        vbyte_output.write(reinterpret_cast<const char*>(vbyte.data()),
                           static_cast<std::streamsize>(vbyte.size()));
        vbyte_offsets.push_back(vbyte_offsets.back() + vbyte.size());

        const auto update = [](std::uint64_t value, std::uint64_t& minimum,
                               std::uint64_t& maximum) {
            minimum = std::min(minimum, value);
            maximum = std::max(maximum, value);
        };
        update(source_size, raw_block_min, raw_block_max);
        update(zstd_offsets.back() - zstd_offsets[zstd_offsets.size() - 2],
               zstd_block_min, zstd_block_max);
        update(dictionary_offsets.back() -
                   dictionary_offsets[dictionary_offsets.size() - 2],
               dictionary_block_min, dictionary_block_max);
        update(vbyte.size(), vbyte_block_min, vbyte_block_max);
    }
    require(zstd_output && dictionary_output && vbyte_output,
            "R4 lossless payload output failed");
    zstd_output.close();
    dictionary_output.close();
    vbyte_output.close();
    write_u64_offsets(zstd_offsets_path, zstd_offsets);
    write_u64_offsets(zstd_dictionary_offsets_path, dictionary_offsets);
    write_u64_offsets(vbyte_offsets_path, vbyte_offsets);
    write_json(receipt_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_lossless_block_pack_receipt"},
        {"rows", rows}, {"addresses", counts.size()},
        {"zstd_version", ZSTD_versionString()}, {"zstd_level", zstd_level},
        {"dictionary_capacity", dictionary_capacity},
        {"dictionary_bytes", dictionary.size()},
        {"dictionary_training_blocks", sample_rows.size()},
        {"dictionary_training_bytes", sample_bytes.size()},
        {"raw_bytes", raw.size()},
        {"zstd_bytes", std::filesystem::file_size(zstd_path)},
        {"zstd_dictionary_bytes", std::filesystem::file_size(zstd_dictionary_path)},
        {"vbyte_bytes", std::filesystem::file_size(vbyte_path)},
        {"offset_bytes", zstd_offsets.size() * sizeof(std::uint64_t)},
        {"raw_block_min", raw_block_min}, {"raw_block_max", raw_block_max},
        {"zstd_block_min", zstd_block_min}, {"zstd_block_max", zstd_block_max},
        {"zstd_dictionary_block_min", dictionary_block_min},
        {"zstd_dictionary_block_max", dictionary_block_max},
        {"vbyte_block_min", vbyte_block_min},
        {"vbyte_block_max", vbyte_block_max}});
#endif
}

std::vector<float> address_scores(const SeedContext& seed, std::size_t request,
                                  const std::vector<float>& maximums) {
    const auto& model = seed.model;
    const float* query = seed.queries.data() + request * dimensions;
    const float* features = seed.features.data() + request * addresses_per_query * scalar_features;
    std::array<float, local_hidden> query_hidden{};
    for (std::size_t column = 0; column != local_hidden; ++column) {
        float value = model.query_bias[column];
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            value += query[dimension] * model.query_weight[dimension * local_hidden + column];
        }
        query_hidden[column] = std::tanh(value);
    }
    std::vector<float> local(addresses_per_query * local_hidden);
    std::array<float, local_hidden> mean{};
    std::array<float, local_hidden> maximum_context{};
    maximum_context.fill(-std::numeric_limits<float>::infinity());
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        std::array<float, 23> input{};
        for (std::size_t feature = 0; feature != scalar_features; ++feature) {
            input[feature] = (features[address * scalar_features + feature] -
                              model.feature_mean[feature]) /
                             model.feature_deviation[feature];
        }
        input[22] = (maximums[address] - model.aggregate_mean[0]) /
                    model.aggregate_deviation[0];
        for (std::size_t column = 0; column != local_hidden; ++column) {
            float value = model.local_bias[column];
            for (std::size_t feature = 0; feature != input.size(); ++feature) {
                value += input[feature] * model.local_weight[feature * local_hidden + column];
            }
            value = std::tanh(value);
            local[address * local_hidden + column] = value;
            mean[column] += value;
            maximum_context[column] = std::max(maximum_context[column], value);
        }
    }
    for (auto& value : mean) value /= static_cast<float>(addresses_per_query);
    std::vector<float> output(addresses_per_query);
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        std::array<float, joined_dimensions> joined{};
        for (std::size_t column = 0; column != local_hidden; ++column) {
            const auto value = local[address * local_hidden + column];
            joined[column] = value;
            joined[32 + column] = query_hidden[column];
            joined[64 + column] = value * query_hidden[column];
            joined[96 + column] = mean[column];
            joined[128 + column] = maximum_context[column];
        }
        float score = model.score_bias2[0];
        for (std::size_t hidden = 0; hidden != score_hidden; ++hidden) {
            float value = model.score_bias1[hidden];
            for (std::size_t input = 0; input != joined_dimensions; ++input) {
                value += joined[input] * model.score_weight1[input * score_hidden + hidden];
            }
            score += std::tanh(value) * model.score_weight2[hidden];
        }
        output[address] = score;
    }
    return output;
}

std::vector<float> address_scores_batched_avx2(
        const SeedContext& seed, std::size_t request,
        const std::vector<float>& maximums) {
#if !AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
    return address_scores(seed, request, maximums);
#else
    const auto& model = seed.model;
    const float* query = seed.queries.data() + request * dimensions;
    const float* features = seed.features.data() +
                            request * addresses_per_query * scalar_features;
    std::array<float, local_hidden> query_hidden{};
    for (std::size_t column = 0; column != local_hidden; ++column) {
        float value = model.query_bias[column];
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            value += query[dimension] * model.query_weight[
                dimension * local_hidden + column];
        }
        query_hidden[column] = std::tanh(value);
    }

    std::vector<float> normalized(23 * addresses_per_query);
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        for (std::size_t feature = 0; feature != scalar_features; ++feature) {
            normalized[feature * addresses_per_query + address] =
                (features[address * scalar_features + feature] -
                 model.feature_mean[feature]) / model.feature_deviation[feature];
        }
        normalized[22 * addresses_per_query + address] =
            (maximums[address] - model.aggregate_mean[0]) /
            model.aggregate_deviation[0];
    }

    std::vector<float> local(addresses_per_query * local_hidden);
    alignas(32) std::array<float, 8> lanes{};
    for (std::size_t address = 0; address != addresses_per_query; address += 8) {
        for (std::size_t column = 0; column != local_hidden; ++column) {
            auto value = _mm256_set1_ps(model.local_bias[column]);
            for (std::size_t feature = 0; feature != 23; ++feature) {
                value = _mm256_add_ps(value, _mm256_mul_ps(
                    _mm256_loadu_ps(normalized.data() +
                                    feature * addresses_per_query + address),
                    _mm256_set1_ps(model.local_weight[
                        feature * local_hidden + column])));
            }
            _mm256_store_ps(lanes.data(), value);
            for (std::size_t lane = 0; lane != 8; ++lane) {
                local[(address + lane) * local_hidden + column] =
                    std::tanh(lanes[lane]);
            }
        }
    }

    std::array<float, local_hidden> mean{};
    std::array<float, local_hidden> maximum_context{};
    maximum_context.fill(-std::numeric_limits<float>::infinity());
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        for (std::size_t column = 0; column != local_hidden; ++column) {
            const auto value = local[address * local_hidden + column];
            mean[column] += value;
            maximum_context[column] = std::max(maximum_context[column], value);
        }
    }
    for (auto& value : mean) value /= static_cast<float>(addresses_per_query);

    std::vector<float> joined(joined_dimensions * addresses_per_query);
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        for (std::size_t column = 0; column != local_hidden; ++column) {
            const auto value = local[address * local_hidden + column];
            joined[column * addresses_per_query + address] = value;
            joined[(32 + column) * addresses_per_query + address] =
                query_hidden[column];
            joined[(64 + column) * addresses_per_query + address] =
                value * query_hidden[column];
            joined[(96 + column) * addresses_per_query + address] = mean[column];
            joined[(128 + column) * addresses_per_query + address] =
                maximum_context[column];
        }
    }

    std::vector<float> output(addresses_per_query, model.score_bias2[0]);
    for (std::size_t address = 0; address != addresses_per_query; address += 8) {
        for (std::size_t hidden = 0; hidden != score_hidden; ++hidden) {
            auto value = _mm256_set1_ps(model.score_bias1[hidden]);
            for (std::size_t input = 0; input != joined_dimensions; ++input) {
                value = _mm256_add_ps(value, _mm256_mul_ps(
                    _mm256_loadu_ps(joined.data() +
                                    input * addresses_per_query + address),
                    _mm256_set1_ps(model.score_weight1[
                        input * score_hidden + hidden])));
            }
            _mm256_store_ps(lanes.data(), value);
            for (std::size_t lane = 0; lane != 8; ++lane) {
                output[address + lane] += std::tanh(lanes[lane]) *
                                          model.score_weight2[hidden];
            }
        }
    }
    return output;
#endif
}

Sample measure(const SeedContext& seed, const std::string& layout,
               std::size_t request,
               const std::string& kernel = "decode_fp32_scalar_dot",
               const std::string& scorer = "scalar_r0") {
    const auto& descriptor = layout_row(seed, layout);
    const auto file = payload_path(seed.root, descriptor);
    const auto record_bytes = descriptor.at("record_bytes").get<std::size_t>();
    const bool fp32 = layout == "address_major_fp32";
    const bool indirect = layout == "document_major_int8_indirect";
    const bool fused = kernel != "decode_fp32_scalar_dot";
    require(!fused || (!fp32 && !indirect),
            "R4 fused kernel requires address-major INT8");
    require(kernel == "decode_fp32_scalar_dot" ||
            kernel == "fused_int8_scalar" || kernel == "fused_int8_avx2" ||
            kernel == "fused_int8_avx2_ordered",
            "R4 representative kernel differs");
    require(scorer == "scalar_r0" || scorer == "batched_avx2_r0",
            "R4 address scorer differs");
    std::ifstream stream(file, std::ios::binary);
    require(static_cast<bool>(stream), "R4 layout physical file open failed");
    std::vector<std::size_t> starts(addresses_per_query + 1);
    std::vector<std::uint8_t> bytes;
    const auto state_begin = process_state();
    const auto total_begin = Clock::now();
    const auto fetch_begin = Clock::now();
    std::uint64_t reads = 0, representatives = 0;
    for (std::size_t local = 0; local != addresses_per_query; ++local) {
        const auto row = seed.shortlists[request * addresses_per_query + local];
        const auto count = seed.representative_counts[row];
        starts[local] = bytes.size();
        representatives += count;
        if (!indirect) {
            const auto position = static_cast<std::uint64_t>(seed.address_offsets[row]);
            const auto old = bytes.size();
            bytes.resize(old + static_cast<std::size_t>(count) * record_bytes);
            stream.seekg(static_cast<std::streamoff>(position * record_bytes));
            stream.read(reinterpret_cast<char*>(bytes.data() + old),
                        static_cast<std::streamsize>(count * record_bytes));
            require(static_cast<bool>(stream), "R4 layout address block fetch failed");
            ++reads;
        } else {
            const auto selected_offset = seed.representative_offsets[row];
            for (std::size_t slot = 0; slot != count; ++slot) {
                const auto document = seed.representative_documents[selected_offset + slot];
                const auto old = bytes.size();
                bytes.resize(old + record_bytes);
                stream.seekg(static_cast<std::streamoff>(document * record_bytes));
                stream.read(reinterpret_cast<char*>(bytes.data() + old),
                            static_cast<std::streamsize>(record_bytes));
                require(static_cast<bool>(stream), "R4 layout indirect fetch failed");
                ++reads;
            }
        }
    }
    starts[addresses_per_query] = bytes.size();
    const auto fetch_end = Clock::now();
    const auto decode_begin = Clock::now();
    std::vector<float> decoded(fused ? 0 : representatives * dimensions);
    std::size_t vector_index = 0;
    for (std::size_t address = 0; !fused && address != addresses_per_query; ++address) {
        for (std::size_t offset = starts[address]; offset != starts[address + 1];
             offset += record_bytes, ++vector_index) {
            float* output = decoded.data() + vector_index * dimensions;
            if (fp32) {
                std::memcpy(output, bytes.data() + offset, dimensions * sizeof(float));
            } else {
                float scale = 0.0F;
                std::memcpy(&scale, bytes.data() + offset + dimensions, sizeof(float));
                for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                    output[dimension] = static_cast<float>(
                        static_cast<int>(bytes[offset + dimension]) - 127) * scale;
                }
            }
        }
    }
    const auto decode_end = Clock::now();
    const auto dot_begin = Clock::now();
    const float* query = seed.queries.data() + request * dimensions;
    Maximums measured;
    if (fused) {
        measured = fused_int8_maximums(bytes, starts, record_bytes, query, kernel);
    } else {
        measured.values.assign(addresses_per_query, -1.0F);
        measured.winners.assign(addresses_per_query, 0);
        vector_index = 0;
        for (std::size_t address = 0; address != addresses_per_query; ++address) {
            const auto count = (starts[address + 1] - starts[address]) / record_bytes;
            float maximum = -std::numeric_limits<float>::infinity();
            std::uint8_t winner = 0;
            for (std::size_t slot = 0; slot != count; ++slot, ++vector_index) {
                const float* value = decoded.data() + vector_index * dimensions;
                float score = 0.0F;
                for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                    score += value[dimension] * query[dimension];
                }
                if (score > maximum) {
                    maximum = score;
                    winner = static_cast<std::uint8_t>(slot);
                }
            }
            measured.values[address] = maximum;
            measured.winners[address] = winner;
        }
    }
    const auto dot_end = Clock::now();
    const auto score_begin = Clock::now();
    const auto scores = scorer == "batched_avx2_r0"
        ? address_scores_batched_avx2(seed, request, measured.values)
        : address_scores(seed, request, measured.values);
    const auto score_end = Clock::now();
    const auto state_end = process_state();
    std::vector<std::uint8_t> digest(scores.size() * sizeof(float));
    std::memcpy(digest.data(), scores.data(), digest.size());
    Sample sample;
    sample.fetch_ms = milliseconds(fetch_begin, fetch_end);
    sample.decode_ms = milliseconds(decode_begin, decode_end);
    sample.dot_ms = milliseconds(dot_begin, dot_end);
    sample.score_ms = milliseconds(score_begin, score_end);
    sample.total_ms = milliseconds(total_begin, score_end);
    sample.logical_bytes = bytes.size();
    sample.random_reads = reads;
    sample.address_spans = addresses_per_query;
    sample.representatives = representatives;
    sample.page_faults = state_end.faults - state_begin.faults;
    sample.rss_delta = static_cast<std::int64_t>(state_end.rss) -
                       static_cast<std::int64_t>(state_begin.rss);
    sample.score_sha256 = agent_memory::sha256_bytes_hex(digest);
    if (fused) {
        const auto reference = fused_int8_maximums(bytes, starts, record_bytes,
                                                   query, "fused_int8_scalar");
        const auto reference_scores = address_scores(seed, request, reference.values);
        sample.maximum_max_abs_error = max_abs_error(reference.values, measured.values);
        sample.address_score_max_abs_error = max_abs_error(reference_scores, scores);
        sample.representative_winner_agreement = agreement(reference.winners,
                                                            measured.winners);
        sample.address_top128_overlap = top_overlap(reference_scores, scores, 128);
    }
    return sample;
}

struct AddressSpan {
    std::size_t local = 0;
    std::size_t byte_offset = 0;
    std::size_t byte_count = 0;
    std::size_t destination = 0;
};

Sample measure_mapped(const SeedContext& seed, const MappedFile& mapped,
                      const std::string& treatment, std::size_t request) {
    require(treatment == "mmap_copy_staging" ||
            treatment == "mmap_direct_shortlist" ||
            treatment == "mmap_direct_offset_order",
            "R4 mapped access treatment differs");
    const auto& descriptor = layout_row(seed, "address_major_int8");
    const auto record_bytes = descriptor.at("record_bytes").get<std::size_t>();
    require(mapped.size() == descriptor.at("bytes").get<std::size_t>(),
            "R4 mapped access file size differs");
    const auto state_begin = process_state();
    const auto total_begin = Clock::now();
    const auto access_begin = Clock::now();
    std::vector<AddressSpan> spans;
    spans.reserve(addresses_per_query);
    std::vector<std::size_t> starts(addresses_per_query + 1);
    std::uint64_t representatives = 0;
    for (std::size_t local = 0; local != addresses_per_query; ++local) {
        const auto row = seed.shortlists[request * addresses_per_query + local];
        const auto count = static_cast<std::size_t>(seed.representative_counts[row]);
        starts[local] = static_cast<std::size_t>(representatives) * record_bytes;
        spans.push_back({local,
            static_cast<std::size_t>(seed.address_offsets[row]) * record_bytes,
            count * record_bytes, starts[local]});
        representatives += count;
    }
    starts[addresses_per_query] = static_cast<std::size_t>(representatives) *
                                  record_bytes;
    if (treatment == "mmap_direct_offset_order") {
        std::sort(spans.begin(), spans.end(), [](const auto& left, const auto& right) {
            return left.byte_offset < right.byte_offset;
        });
    }
    std::vector<std::uint8_t> staged;
    if (treatment == "mmap_copy_staging") {
        staged.resize(static_cast<std::size_t>(representatives) * record_bytes);
        for (const auto& span : spans) {
            std::memcpy(staged.data() + span.destination,
                        mapped.data() + span.byte_offset, span.byte_count);
        }
    }
    const auto access_end = Clock::now();
    const auto dot_begin = Clock::now();
    const float* query = seed.queries.data() + request * dimensions;
    Maximums maximums;
    if (treatment == "mmap_copy_staging") {
        maximums = fused_int8_maximums(staged, starts, record_bytes, query,
                                       "fused_int8_scalar");
    } else {
        maximums.values.assign(addresses_per_query,
            -std::numeric_limits<float>::infinity());
        maximums.winners.assign(addresses_per_query, 0);
        for (const auto& span : spans) {
            const auto count = span.byte_count / record_bytes;
            for (std::size_t slot = 0; slot != count; ++slot) {
                const auto* record = mapped.data() + span.byte_offset +
                                     slot * record_bytes;
                float scale = 0.0F;
                std::memcpy(&scale, record + dimensions, sizeof(float));
                const auto score = int8_dot_scalar(record, scale, query);
                if (score > maximums.values[span.local]) {
                    maximums.values[span.local] = score;
                    maximums.winners[span.local] = static_cast<std::uint8_t>(slot);
                }
            }
        }
    }
    const auto dot_end = Clock::now();
    const auto score_begin = Clock::now();
    const auto scores = address_scores_batched_avx2(seed, request, maximums.values);
    const auto score_end = Clock::now();
    const auto state_end = process_state();
    std::vector<std::uint8_t> digest(scores.size() * sizeof(float));
    std::memcpy(digest.data(), scores.data(), digest.size());
    Sample sample;
    sample.fetch_ms = milliseconds(access_begin, access_end);
    sample.decode_ms = 0.0;
    sample.dot_ms = milliseconds(dot_begin, dot_end);
    sample.score_ms = milliseconds(score_begin, score_end);
    sample.total_ms = milliseconds(total_begin, score_end);
    sample.logical_bytes = representatives * record_bytes;
    sample.random_reads = 0;
    sample.address_spans = spans.size();
    sample.representatives = representatives;
    sample.page_faults = state_end.faults - state_begin.faults;
    sample.rss_delta = static_cast<std::int64_t>(state_end.rss) -
                       static_cast<std::int64_t>(state_begin.rss);
    sample.score_sha256 = agent_memory::sha256_bytes_hex(digest);
    return sample;
}

constexpr std::size_t binary_code_bytes = 32;
constexpr std::size_t binary_code_words = binary_code_bytes / sizeof(std::uint64_t);

struct NativeCascadeInput {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::unique_ptr<MappedFile> document_codes;
    std::unique_ptr<MappedFile> document_vectors;
    std::vector<std::uint64_t> query_codes;
    std::vector<float> query_projections;
    std::vector<float> adc_centroids;
    std::vector<float> query_vectors;
    std::vector<std::uint32_t> document_id_rank;
};

std::filesystem::path native_payload(const std::filesystem::path& manifest_path,
                                     const nlohmann::json& manifest,
                                     const std::string& name) {
    return manifest_path.parent_path() /
           manifest.at(name + "_file").get<std::string>();
}

NativeCascadeInput load_native_input(const std::filesystem::path& manifest_path,
                                     const std::filesystem::path& rank_path) {
    const auto manifest = read_json(manifest_path);
    require(manifest.value("family", "") == "mih_storage_benchmark_input_v1" &&
            manifest.at("code_bits").get<std::size_t>() == 256 &&
            manifest.at("embedding_dimension").get<std::size_t>() == dimensions,
            "R4 end-to-end native input identity differs");
    NativeCascadeInput value;
    value.document_count = manifest.at("document_count");
    value.query_count = manifest.at("query_count");
    require(value.document_count == 1000000 && value.query_count == 305,
            "R4 end-to-end native input shape differs");
    value.document_codes = std::make_unique<MappedFile>(
        native_payload(manifest_path, manifest, "document_codes"));
    value.document_vectors = std::make_unique<MappedFile>(
        native_payload(manifest_path, manifest, "document_vectors"));
    const auto query_code_bytes = read_values<std::uint8_t>(
        native_payload(manifest_path, manifest, "query_codes"));
    require(query_code_bytes.size() % sizeof(std::uint64_t) == 0,
            "R4 end-to-end query-code alignment differs");
    value.query_codes.resize(query_code_bytes.size() / sizeof(std::uint64_t));
    std::memcpy(value.query_codes.data(), query_code_bytes.data(),
                query_code_bytes.size());
    value.query_projections = read_values<float>(
        native_payload(manifest_path, manifest, "query_itq_projections"));
    value.adc_centroids = read_values<float>(
        native_payload(manifest_path, manifest, "binary_adc_centroids"));
    value.query_vectors = read_values<float>(
        native_payload(manifest_path, manifest, "query_vectors"));
    value.document_id_rank = read_values<std::uint32_t>(rank_path);
    require(value.document_codes->size() == value.document_count * binary_code_bytes &&
            value.document_vectors->size() == value.document_count * dimensions * sizeof(float) &&
            value.query_codes.size() == value.query_count * binary_code_words &&
            value.query_projections.size() == value.query_count * 256 &&
            value.adc_centroids.size() == 512 &&
            value.query_vectors.size() == value.query_count * dimensions &&
            value.document_id_rank.size() == value.document_count,
            "R4 end-to-end native payload shape differs");
    return value;
}

std::string u32_sequence_sha256(const std::vector<std::uint32_t>& values) {
    std::vector<std::uint8_t> bytes(values.size() * sizeof(std::uint32_t));
    for (std::size_t index = 0; index != values.size(); ++index) {
        for (std::size_t byte = 0; byte != sizeof(std::uint32_t); ++byte) {
            bytes[index * sizeof(std::uint32_t) + byte] = static_cast<std::uint8_t>(
                values[index] >> (8U * byte));
        }
    }
    return agent_memory::sha256_bytes_hex(bytes);
}

double pairwise_sum(const float* values, std::size_t count) {
    if (count < 8) {
        float result = -0.0F;
        for (std::size_t index = 0; index != count; ++index) result += values[index];
        return result;
    }
    if (count <= 128) {
        std::array<float, 8> accumulators{values[0], values[1], values[2], values[3],
                                          values[4], values[5], values[6], values[7]};
        std::size_t index = 8;
        for (; index + 7 < count - (count % 8); index += 8) {
            for (std::size_t lane = 0; lane != 8; ++lane)
                accumulators[lane] += values[index + lane];
        }
        float result = ((accumulators[0] + accumulators[1]) +
                        (accumulators[2] + accumulators[3])) +
                       ((accumulators[4] + accumulators[5]) +
                        (accumulators[6] + accumulators[7]));
        for (; index != count; ++index) result += values[index];
        return result;
    }
    auto first = count / 2;
    first -= first % 8;
    return pairwise_sum(values, first) + pairwise_sum(values + first, count - first);
}

template <typename Score>
struct E2eScored {
    Score score;
    std::uint32_t document;
    std::uint32_t rank;
};

template <typename Score>
bool lower_e2e_score(const E2eScored<Score>& left,
                     const E2eScored<Score>& right) {
    return left.score == right.score ? left.rank < right.rank
                                     : left.score < right.score;
}

struct EndToEndTiming {
    double representative_fetch = 0.0;
    double representative_decode = 0.0;
    double representative_dot = 0.0;
    double address_score = 0.0;
    double address_order_and_boundary = 0.0;
    double candidate_materialization = 0.0;
    double hamming_and_top768 = 0.0;
    double adc_and_top64 = 0.0;
    double exact_e5_and_top10 = 0.0;
    double total = 0.0;
};

struct EndToEndResult {
    EndToEndTiming timing;
    std::vector<std::uint32_t> selected_addresses;
    std::vector<std::uint32_t> candidates;
    std::vector<std::uint32_t> hamming;
    std::vector<std::uint32_t> adc;
    std::vector<std::uint32_t> exact;
    std::string score_sha256;
    std::size_t representatives = 0;
    std::uint64_t logical_bytes = 0;
    std::uint64_t page_faults = 0;
    std::uint64_t address_spans = 0;
    std::int64_t rss_delta = 0;
};

struct RoutingValues {
    std::vector<float> scores;
    EndToEndTiming timing;
    std::size_t representatives = 0;
    std::uint64_t logical_bytes = 0;
    std::uint64_t page_faults = 0;
    std::uint64_t address_spans = 0;
    std::int64_t rss_delta = 0;
};

RoutingValues end_to_end_route(const SeedContext& seed, const MappedFile& mapped,
                               const std::string& treatment,
                               std::size_t request) {
    const bool baseline = treatment == "baseline_seek_decode_scalar";
    const bool strict = treatment == "strict_mmap_fused_scalar_batched";
    const bool fast = treatment == "fast_mmap_fused_avx2_batched";
    require(baseline || strict || fast, "R4 end-to-end treatment differs");
    const auto& descriptor = layout_row(seed, "address_major_int8");
    const auto record_bytes = descriptor.at("record_bytes").get<std::size_t>();
    std::vector<std::size_t> starts(addresses_per_query + 1);
    std::vector<AddressSpan> spans;
    spans.reserve(addresses_per_query);
    std::size_t representatives = 0;
    for (std::size_t local = 0; local != addresses_per_query; ++local) {
        const auto row = seed.shortlists[request * addresses_per_query + local];
        const auto count = static_cast<std::size_t>(seed.representative_counts[row]);
        starts[local] = representatives * record_bytes;
        spans.push_back({local,
            static_cast<std::size_t>(seed.address_offsets[row]) * record_bytes,
            count * record_bytes, starts[local]});
        representatives += count;
    }
    starts[addresses_per_query] = representatives * record_bytes;
    std::sort(spans.begin(), spans.end(), [](const auto& left, const auto& right) {
        return left.byte_offset < right.byte_offset;
    });
    RoutingValues result;
    result.representatives = representatives;
    result.logical_bytes = representatives * record_bytes;
    result.address_spans = addresses_per_query;
    std::vector<std::uint8_t> staged;
    auto begin = Clock::now();
    if (baseline) {
        std::ifstream stream(payload_path(seed.root, descriptor), std::ios::binary);
        require(static_cast<bool>(stream), "R4 end-to-end baseline store open failed");
        staged.resize(representatives * record_bytes);
        for (const auto& span : spans) {
            stream.seekg(static_cast<std::streamoff>(span.byte_offset));
            stream.read(reinterpret_cast<char*>(staged.data() + span.destination),
                        static_cast<std::streamsize>(span.byte_count));
            require(static_cast<bool>(stream), "R4 end-to-end baseline fetch failed");
        }
    }
    result.timing.representative_fetch = milliseconds(begin, Clock::now());
    const float* query = seed.queries.data() + request * dimensions;
    Maximums maximums;
    if (baseline) {
        begin = Clock::now();
        std::vector<float> decoded(representatives * dimensions);
        std::size_t vector = 0;
        for (std::size_t local = 0; local != addresses_per_query; ++local) {
            for (std::size_t offset = starts[local]; offset != starts[local + 1];
                 offset += record_bytes, ++vector) {
                float scale = 0.0F;
                std::memcpy(&scale, staged.data() + offset + dimensions, sizeof(scale));
                for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                    decoded[vector * dimensions + dimension] = static_cast<float>(
                        static_cast<int>(staged[offset + dimension]) - 127) * scale;
                }
            }
        }
        result.timing.representative_decode = milliseconds(begin, Clock::now());
        begin = Clock::now();
        maximums.values.assign(addresses_per_query,
                               -std::numeric_limits<float>::infinity());
        maximums.winners.assign(addresses_per_query, 0);
        vector = 0;
        for (std::size_t local = 0; local != addresses_per_query; ++local) {
            const auto count = (starts[local + 1] - starts[local]) / record_bytes;
            for (std::size_t slot = 0; slot != count; ++slot, ++vector) {
                float score = 0.0F;
                for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
                    score += decoded[vector * dimensions + dimension] * query[dimension];
                if (score > maximums.values[local]) {
                    maximums.values[local] = score;
                    maximums.winners[local] = static_cast<std::uint8_t>(slot);
                }
            }
        }
    } else {
        begin = Clock::now();
        maximums.values.assign(addresses_per_query,
                               -std::numeric_limits<float>::infinity());
        maximums.winners.assign(addresses_per_query, 0);
        for (const auto& span : spans) {
            const auto count = span.byte_count / record_bytes;
            for (std::size_t slot = 0; slot != count; ++slot) {
                const auto* record = mapped.data() + span.byte_offset + slot * record_bytes;
                float scale = 0.0F;
                std::memcpy(&scale, record + dimensions, sizeof(scale));
                const auto score = fast ? int8_dot_avx2(record, scale, query)
                                        : int8_dot_scalar(record, scale, query);
                if (score > maximums.values[span.local]) {
                    maximums.values[span.local] = score;
                    maximums.winners[span.local] = static_cast<std::uint8_t>(slot);
                }
            }
        }
    }
    result.timing.representative_dot = milliseconds(begin, Clock::now());
    begin = Clock::now();
    result.scores = baseline ? address_scores(seed, request, maximums.values)
                             : address_scores_batched_avx2(seed, request,
                                                          maximums.values);
    result.timing.address_score = milliseconds(begin, Clock::now());
    return result;
}

EndToEndResult finish_end_to_end_query(
        const SeedContext& seed, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        RoutingValues route, std::size_t request, std::size_t native_query,
        Clock::time_point total_begin) {
    require(request < 152 && native_query < input.query_count,
            "R4 end-to-end query row differs");
    EndToEndResult result;
    result.timing = route.timing;
    result.representatives = route.representatives;
    result.logical_bytes = route.logical_bytes;
    result.page_faults = route.page_faults;
    result.address_spans = route.address_spans;
    result.rss_delta = route.rss_delta;
    std::vector<std::uint8_t> score_bytes(route.scores.size() * sizeof(float));
    std::memcpy(score_bytes.data(), route.scores.data(), score_bytes.size());
    result.score_sha256 = agent_memory::sha256_bytes_hex(score_bytes);

    auto begin = Clock::now();
    std::vector<std::size_t> order(addresses_per_query);
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](std::size_t left, std::size_t right) {
        if (route.scores[left] != route.scores[right])
            return route.scores[left] > route.scores[right];
        const auto left_row = seed.shortlists[request * addresses_per_query + left];
        const auto right_row = seed.shortlists[request * addresses_per_query + right];
        return seed.occupied_addresses[left_row] < seed.occupied_addresses[right_row];
    });
    std::size_t candidate_count = 0;
    for (const auto local : order) {
        const auto row = seed.shortlists[request * addresses_per_query + local];
        const auto count = static_cast<std::size_t>(seed.address_counts[row]);
        if (candidate_count + count > 5000) break;
        candidate_count += count;
        result.selected_addresses.push_back(seed.occupied_addresses[row]);
    }
    result.timing.address_order_and_boundary = milliseconds(begin, Clock::now());

    begin = Clock::now();
    result.candidates.reserve(candidate_count);
    for (const auto address : result.selected_addresses) {
        const auto found = std::lower_bound(seed.occupied_addresses.begin(),
                                            seed.occupied_addresses.end(), address);
        require(found != seed.occupied_addresses.end() && *found == address,
                "R4 end-to-end selected address differs");
        const auto row = static_cast<std::size_t>(found - seed.occupied_addresses.begin());
        const auto first = seed.address_offsets[row];
        const auto count = seed.address_counts[row];
        for (std::size_t offset = 0; offset != count; ++offset) {
            const auto document = seed.physical_to_document[first + offset];
            require(document >= 0, "R4 end-to-end physical document differs");
            result.candidates.push_back(static_cast<std::uint32_t>(document));
        }
    }
    std::sort(result.candidates.begin(), result.candidates.end());
    result.timing.candidate_materialization = milliseconds(begin, Clock::now());

    begin = Clock::now();
    std::vector<E2eScored<std::uint16_t>> hamming_rows;
    hamming_rows.reserve(result.candidates.size());
    const auto* query_code = input.query_codes.data() + native_query * binary_code_words;
    for (const auto document : result.candidates) {
        const auto* document_code = reinterpret_cast<const std::uint64_t*>(
            input.document_codes->data() + static_cast<std::size_t>(document) *
                                             binary_code_bytes);
        hamming_rows.push_back({static_cast<std::uint16_t>(
            hamming.distance_words(document_code, query_code)), document,
            input.document_id_rank[document]});
    }
    const auto hamming_count = std::min<std::size_t>(768, hamming_rows.size());
    if (hamming_count < hamming_rows.size()) {
        std::nth_element(hamming_rows.begin(), hamming_rows.begin() + hamming_count,
                         hamming_rows.end(), lower_e2e_score<std::uint16_t>);
        hamming_rows.resize(hamming_count);
    }
    std::sort(hamming_rows.begin(), hamming_rows.end(),
              lower_e2e_score<std::uint16_t>);
    for (const auto& row : hamming_rows) result.hamming.push_back(row.document);
    result.timing.hamming_and_top768 = milliseconds(begin, Clock::now());

    begin = Clock::now();
    std::vector<E2eScored<float>> adc_rows;
    adc_rows.reserve(result.hamming.size());
    const auto* projection = input.query_projections.data() + native_query * 256;
    for (const auto document : result.hamming) {
        std::array<float, 256> components{};
        const auto* code = input.document_codes->data() +
                           static_cast<std::size_t>(document) * binary_code_bytes;
        for (std::size_t bit = 0; bit != 256; ++bit) {
            const auto symbol = (code[bit / 8] >> (bit % 8)) & 1U;
            const auto delta = projection[bit] - input.adc_centroids[bit * 2 + symbol];
            components[bit] = delta * delta;
        }
        adc_rows.push_back({static_cast<float>(pairwise_sum(components.data(),
                                                            components.size())),
                            document, input.document_id_rank[document]});
    }
    const auto adc_count = std::min<std::size_t>(64, adc_rows.size());
    if (adc_count < adc_rows.size()) {
        std::nth_element(adc_rows.begin(), adc_rows.begin() + adc_count,
                         adc_rows.end(), lower_e2e_score<float>);
        adc_rows.resize(adc_count);
    }
    std::sort(adc_rows.begin(), adc_rows.end(), lower_e2e_score<float>);
    for (const auto& row : adc_rows) result.adc.push_back(row.document);
    result.timing.adc_and_top64 = milliseconds(begin, Clock::now());

    begin = Clock::now();
    std::vector<E2eScored<float>> exact_rows;
    exact_rows.reserve(result.adc.size());
    const auto* query_vector = input.query_vectors.data() + native_query * dimensions;
    for (const auto document : result.adc) {
        std::array<float, dimensions> components{};
        const auto* document_vector = reinterpret_cast<const float*>(
            input.document_vectors->data() + static_cast<std::size_t>(document) *
                                               dimensions * sizeof(float));
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
            components[dimension] = document_vector[dimension] * query_vector[dimension];
        exact_rows.push_back({-static_cast<float>(pairwise_sum(components.data(),
                                                               components.size())),
                              document, input.document_id_rank[document]});
    }
    const auto exact_count = std::min<std::size_t>(10, exact_rows.size());
    if (exact_count < exact_rows.size()) {
        std::nth_element(exact_rows.begin(), exact_rows.begin() + exact_count,
                         exact_rows.end(), lower_e2e_score<float>);
        exact_rows.resize(exact_count);
    }
    std::sort(exact_rows.begin(), exact_rows.end(), lower_e2e_score<float>);
    for (const auto& row : exact_rows) result.exact.push_back(row.document);
    result.timing.exact_e5_and_top10 = milliseconds(begin, Clock::now());
    result.timing.total = milliseconds(total_begin, Clock::now());
    return result;
}

EndToEndResult end_to_end_query(const SeedContext& seed, const MappedFile& mapped,
                                const NativeCascadeInput& input,
                                const agent_memory::HammingDistanceComputer& hamming,
                                const std::string& treatment, std::size_t request,
                                std::size_t native_query) {
    const auto total_begin = Clock::now();
    return finish_end_to_end_query(seed, input, hamming,
        end_to_end_route(seed, mapped, treatment, request), request,
        native_query, total_begin);
}

nlohmann::json end_to_end_json(const EndToEndResult& value, std::uint64_t seed,
                               const std::string& treatment, std::size_t request,
                               std::size_t native_query, std::size_t pass) {
    return {{"seed", seed}, {"treatment", treatment}, {"request", request},
        {"native_query", native_query}, {"pass", pass},
        {"representatives_scored", value.representatives},
        {"logical_bytes_touched", value.logical_bytes},
        {"address_spans", value.address_spans},
        {"page_faults", value.page_faults},
        {"rss_delta_bytes", value.rss_delta},
        {"selected_address_count", value.selected_addresses.size()},
        {"candidate_count", value.candidates.size()},
        {"score_sha256", value.score_sha256},
        {"selected_address_sha256", u32_sequence_sha256(value.selected_addresses)},
        {"candidate_sha256", u32_sequence_sha256(value.candidates)},
        {"hamming_sha256", u32_sequence_sha256(value.hamming)},
        {"adc_sha256", u32_sequence_sha256(value.adc)},
        {"exact_sha256", u32_sequence_sha256(value.exact)},
        {"exact_documents", value.exact},
        {"timing_ms", {
            {"representative_fetch", value.timing.representative_fetch},
            {"representative_decode", value.timing.representative_decode},
            {"representative_dot", value.timing.representative_dot},
            {"address_score", value.timing.address_score},
            {"address_order_and_boundary", value.timing.address_order_and_boundary},
            {"candidate_materialization", value.timing.candidate_materialization},
            {"hamming_and_top768", value.timing.hamming_and_top768},
            {"adc_and_top64", value.timing.adc_and_top64},
            {"exact_e5_and_top10", value.timing.exact_e5_and_top10},
            {"total", value.timing.total}}}};
}

float compressed_int8_dot(const std::uint8_t* record,
                          const std::string& treatment, const float* query,
                          std::size_t& consumed) {
    std::array<std::uint32_t, dimensions> values{};
    float scale = 0.0F;
    if (treatment == "simdcomp_fixed8") {
        for (std::size_t block = 0; block != 3; ++block) {
            simdcomp_unpack_block(record + block * 128, 8,
                                  values.data() + block * 128);
        }
        std::memcpy(&scale, record + dimensions, sizeof(scale));
        consumed = 388;
    } else if (treatment == "simdcomp_adaptive_for") {
        std::memcpy(&scale, record, sizeof(scale));
        const auto* minima = record + 4;
        const auto* bits = record + 7;
        const auto* payload = record + 10;
        for (std::size_t block = 0; block != 3; ++block) {
            simdcomp_unpack_block(payload, bits[block],
                                  values.data() + block * 128);
            for (std::size_t lane = 0; lane != 128; ++lane)
                values[block * 128 + lane] += minima[block];
            payload += static_cast<std::size_t>(bits[block]) * 16;
        }
        consumed = static_cast<std::size_t>(payload - record);
    } else {
        require(treatment == "simdcomp_adaptive_zigzag",
                "R4 compressed dot treatment differs");
        std::memcpy(&scale, record, sizeof(scale));
        const auto* bits = record + 4;
        const auto* payload = record + 7;
        for (std::size_t block = 0; block != 3; ++block) {
            simdcomp_unpack_block(payload, bits[block],
                                  values.data() + block * 128);
            for (std::size_t lane = 0; lane != 128; ++lane) {
                const auto encoded = values[block * 128 + lane];
                const auto centered = (encoded & 1U) != 0
                    ? -static_cast<int>((encoded + 1U) / 2U)
                    : static_cast<int>(encoded / 2U);
                values[block * 128 + lane] = static_cast<std::uint32_t>(
                    centered + 127);
            }
            payload += static_cast<std::size_t>(bits[block]) * 16;
        }
        consumed = static_cast<std::size_t>(payload - record);
    }
    float score = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
        const float decoded = static_cast<float>(
            static_cast<int>(values[dimension]) - 127) * scale;
        score += decoded * query[dimension];
    }
    return score;
}

Sample measure_compressed(const SeedContext& seed, const MappedFile& mapped,
                          const std::vector<std::uint64_t>* byte_offsets,
                          const std::string& treatment, std::size_t request) {
    require(treatment == "simdcomp_fixed8" ||
            treatment == "simdcomp_adaptive_for" ||
            treatment == "simdcomp_adaptive_zigzag",
            "R4 compressed access treatment differs");
    const auto state_begin = process_state();
    const auto total_begin = Clock::now();
    const auto access_begin = Clock::now();
    std::vector<AddressSpan> spans;
    spans.reserve(addresses_per_query);
    std::uint64_t representatives = 0;
    for (std::size_t local = 0; local != addresses_per_query; ++local) {
        const auto row = seed.shortlists[request * addresses_per_query + local];
        const auto count = static_cast<std::size_t>(seed.representative_counts[row]);
        const auto offset = treatment == "simdcomp_fixed8"
            ? static_cast<std::size_t>(seed.address_offsets[row]) * 388U
            : static_cast<std::size_t>((*byte_offsets)[row]);
        spans.push_back({local, offset, count, 0});
        representatives += count;
    }
    std::sort(spans.begin(), spans.end(), [](const auto& left, const auto& right) {
        return left.byte_offset < right.byte_offset;
    });
    const auto access_end = Clock::now();
    const auto dot_begin = Clock::now();
    const float* query = seed.queries.data() + request * dimensions;
    Maximums maximums;
    maximums.values.assign(addresses_per_query,
                           -std::numeric_limits<float>::infinity());
    maximums.winners.assign(addresses_per_query, 0);
    std::uint64_t logical_bytes = 0;
    for (const auto& span : spans) {
        const auto* record = mapped.data() + span.byte_offset;
        for (std::size_t slot = 0; slot != span.byte_count; ++slot) {
            std::size_t consumed = 0;
            const auto score = compressed_int8_dot(record, treatment, query, consumed);
            require(consumed != 0 && record >= mapped.data() &&
                    record + consumed <= mapped.data() + mapped.size(),
                    "R4 compressed record differs");
            if (score > maximums.values[span.local]) {
                maximums.values[span.local] = score;
                maximums.winners[span.local] = static_cast<std::uint8_t>(slot);
            }
            record += consumed;
            logical_bytes += consumed;
        }
    }
    const auto dot_end = Clock::now();
    const auto score_begin = Clock::now();
    const auto scores = address_scores_batched_avx2(seed, request, maximums.values);
    const auto score_end = Clock::now();
    const auto state_end = process_state();
    std::vector<std::uint8_t> digest(scores.size() * sizeof(float));
    std::memcpy(digest.data(), scores.data(), digest.size());
    Sample sample;
    sample.fetch_ms = milliseconds(access_begin, access_end);
    sample.dot_ms = milliseconds(dot_begin, dot_end);
    sample.score_ms = milliseconds(score_begin, score_end);
    sample.total_ms = milliseconds(total_begin, score_end);
    sample.logical_bytes = logical_bytes;
    sample.random_reads = 0;
    sample.address_spans = spans.size();
    sample.representatives = representatives;
    sample.page_faults = state_end.faults - state_begin.faults;
    sample.rss_delta = static_cast<std::int64_t>(state_end.rss) -
                       static_cast<std::int64_t>(state_begin.rss);
    sample.score_sha256 = agent_memory::sha256_bytes_hex(digest);
    return sample;
}

struct LosslessBlockMaps {
    std::filesystem::path raw_path, zstd_path, zstd_dictionary_path, vbyte_path;
    std::unique_ptr<MappedFile> raw, zstd, zstd_dictionary, vbyte;
    std::vector<std::uint64_t> zstd_offsets, zstd_dictionary_offsets,
        vbyte_offsets;
    std::vector<std::uint8_t> dictionary;
#if AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
    ZSTD_DCtx* zstd_context = nullptr;
    ZSTD_DDict* compiled_dictionary = nullptr;
#endif

    ~LosslessBlockMaps() {
#if AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
        if (compiled_dictionary != nullptr) ZSTD_freeDDict(compiled_dictionary);
        if (zstd_context != nullptr) ZSTD_freeDCtx(zstd_context);
#endif
    }
};

std::unique_ptr<LosslessBlockMaps> load_lossless_block_maps(
        const SeedContext& seed, const std::filesystem::path& materialization_path,
        const nlohmann::json& row) {
    auto result = std::make_unique<LosslessBlockMaps>();
    result->raw_path = payload_path(seed.root,
        layout_row(seed, "address_major_int8"));
    result->raw = std::make_unique<MappedFile>(result->raw_path);
    const auto root = materialization_path.parent_path() /
        ("seed-" + std::to_string(seed.seed));
    const auto path = [&](const std::string& name) {
        return payload_path(root, role(row.at("files"), name));
    };
    result->zstd_path = path("zstd_block");
    result->zstd_dictionary_path = path("zstd_dictionary_block");
    result->vbyte_path = path("vbyte_zigzag");
    result->zstd = std::make_unique<MappedFile>(result->zstd_path);
    result->zstd_dictionary = std::make_unique<MappedFile>(
        result->zstd_dictionary_path);
    result->vbyte = std::make_unique<MappedFile>(result->vbyte_path);
    result->zstd_offsets = read_values<std::uint64_t>(path("zstd_block_offsets"));
    result->zstd_dictionary_offsets = read_values<std::uint64_t>(
        path("zstd_dictionary_block_offsets"));
    result->vbyte_offsets = read_values<std::uint64_t>(path("vbyte_offsets"));
    result->dictionary = read_values<std::uint8_t>(path("zstd_dictionary"));
    require(result->zstd_offsets.size() == seed.address_counts.size() + 1 &&
            result->zstd_dictionary_offsets.size() == result->zstd_offsets.size() &&
            result->vbyte_offsets.size() == result->zstd_offsets.size(),
            "R4 lossless block offset count differs");
#if !AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
    throw std::runtime_error("R4 lossless block codec requires Zstd");
#else
    result->zstd_context = ZSTD_createDCtx();
    result->compiled_dictionary = ZSTD_createDDict(
        result->dictionary.data(), result->dictionary.size());
    require(result->zstd_context != nullptr &&
            result->compiled_dictionary != nullptr,
            "R4 lossless Zstd decode context failed");
#endif
    return result;
}

std::uint32_t decode_vbyte(const std::uint8_t*& current,
                           const std::uint8_t* end) {
    std::uint32_t value = 0;
    unsigned shift = 0;
    while (true) {
        require(current != end && shift <= 28U, "R4 VByte payload truncated");
        const auto byte = *current++;
        value |= static_cast<std::uint32_t>(byte & 127U) << shift;
        if ((byte & 128U) == 0) return value;
        shift += 7U;
    }
}

Sample measure_lossless_block(const SeedContext& seed, LosslessBlockMaps& maps,
                              const std::string& treatment,
                              std::size_t request) {
    require(treatment == "raw_int8" || treatment == "zstd_block" ||
            treatment == "zstd_dictionary_block" ||
            treatment == "vbyte_zigzag", "R4 lossless treatment differs");
    const auto state_begin = process_state();
    const auto total_begin = Clock::now();
    const auto access_begin = Clock::now();
    const auto* offsets = treatment == "zstd_block" ? &maps.zstd_offsets
        : treatment == "zstd_dictionary_block" ? &maps.zstd_dictionary_offsets
        : treatment == "vbyte_zigzag" ? &maps.vbyte_offsets : nullptr;
    const auto* mapped = treatment == "raw_int8" ? maps.raw.get()
        : treatment == "zstd_block" ? maps.zstd.get()
        : treatment == "zstd_dictionary_block" ? maps.zstd_dictionary.get()
        : maps.vbyte.get();
    std::vector<AddressSpan> spans;
    spans.reserve(addresses_per_query);
    std::uint64_t representatives = 0, decoded_records = 0;
    for (std::size_t local = 0; local != addresses_per_query; ++local) {
        const auto row = seed.shortlists[request * addresses_per_query + local];
        const auto count = static_cast<std::size_t>(seed.representative_counts[row]);
        const auto offset = offsets == nullptr
            ? static_cast<std::size_t>(seed.address_offsets[row]) * 388U
            : static_cast<std::size_t>((*offsets)[row]);
        spans.push_back({local, offset, count, static_cast<std::size_t>(row)});
        representatives += count;
        decoded_records += seed.address_counts[row];
    }
    std::sort(spans.begin(), spans.end(), [](const auto& left, const auto& right) {
        return left.byte_offset < right.byte_offset;
    });
    const auto access_end = Clock::now();

    const auto decode_begin = Clock::now();
    std::vector<std::uint8_t> staged;
    std::vector<std::size_t> staged_offsets(addresses_per_query, 0);
    std::uint64_t logical_bytes = 0;
    if (treatment != "raw_int8") staged.resize(decoded_records * 388U);
    std::size_t staged_position = 0;
    for (const auto& span : spans) {
        staged_offsets[span.local] = staged_position;
        const auto raw_size = static_cast<std::size_t>(
            seed.address_counts[span.destination]) * 388U;
        if (treatment == "raw_int8") {
            logical_bytes += span.byte_count * 388U;
            continue;
        }
        const auto source_end = static_cast<std::size_t>(
            (*offsets)[span.destination + 1]);
        const auto source_size = source_end - span.byte_offset;
        require(source_end <= mapped->size(), "R4 lossless block range differs");
        const auto* source = mapped->data() + span.byte_offset;
        auto* destination = staged.data() + staged_position;
        if (treatment == "vbyte_zigzag") {
            const auto* current = source;
            const auto* end = source + source_size;
            for (std::size_t record = 0;
                 record != seed.address_counts[span.destination]; ++record) {
                auto* output = destination + record * 388U;
                require(static_cast<std::size_t>(end - current) >= sizeof(float),
                        "R4 VByte scale truncated");
                std::memcpy(output + dimensions, current, sizeof(float));
                current += sizeof(float);
                for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                    const auto encoded = decode_vbyte(current, end);
                    const auto centered = (encoded & 1U) != 0
                        ? -static_cast<int>((encoded + 1U) / 2U)
                        : static_cast<int>(encoded / 2U);
                    require(centered >= -127 && centered <= 127,
                            "R4 VByte code range differs");
                    output[dimension] = static_cast<std::uint8_t>(centered + 127);
                }
            }
            require(current == end, "R4 VByte address block has trailing bytes");
        } else {
#if !AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
            throw std::runtime_error("R4 lossless block codec requires Zstd");
#else
            const auto decoded = treatment == "zstd_block"
                ? ZSTD_decompressDCtx(maps.zstd_context, destination, raw_size,
                                      source, source_size)
                : ZSTD_decompress_usingDDict(
                    maps.zstd_context, destination, raw_size, source, source_size,
                    maps.compiled_dictionary);
            require(ZSTD_isError(decoded) == 0 && decoded == raw_size,
                    ZSTD_isError(decoded) != 0 ? ZSTD_getErrorName(decoded)
                                               : "R4 Zstd decoded size differs");
#endif
        }
        staged_position += raw_size;
        logical_bytes += source_size;
    }
    require(treatment == "raw_int8" || staged_position == staged.size(),
            "R4 lossless staged byte count differs");
    const auto decode_end = Clock::now();

    const auto dot_begin = Clock::now();
    const float* query = seed.queries.data() + request * dimensions;
    Maximums maximums;
    maximums.values.assign(addresses_per_query,
                           -std::numeric_limits<float>::infinity());
    maximums.winners.assign(addresses_per_query, 0);
    for (const auto& span : spans) {
        const auto* record = treatment == "raw_int8"
            ? mapped->data() + span.byte_offset
            : staged.data() + staged_offsets[span.local];
        for (std::size_t slot = 0; slot != span.byte_count; ++slot) {
            float scale = 0.0F;
            std::memcpy(&scale, record + dimensions, sizeof(scale));
            const auto score = int8_dot_scalar(record, scale, query);
            if (score > maximums.values[span.local]) {
                maximums.values[span.local] = score;
                maximums.winners[span.local] = static_cast<std::uint8_t>(slot);
            }
            record += 388U;
        }
    }
    const auto dot_end = Clock::now();
    const auto score_begin = Clock::now();
    const auto scores = address_scores_batched_avx2(seed, request, maximums.values);
    const auto score_end = Clock::now();
    const auto state_end = process_state();
    std::vector<std::uint8_t> digest(scores.size() * sizeof(float));
    std::memcpy(digest.data(), scores.data(), digest.size());
    Sample sample;
    sample.fetch_ms = milliseconds(access_begin, access_end);
    sample.decode_ms = milliseconds(decode_begin, decode_end);
    sample.dot_ms = milliseconds(dot_begin, dot_end);
    sample.score_ms = milliseconds(score_begin, score_end);
    sample.total_ms = milliseconds(total_begin, score_end);
    sample.logical_bytes = logical_bytes;
    sample.random_reads = addresses_per_query;
    sample.address_spans = spans.size();
    sample.representatives = representatives;
    sample.page_faults = state_end.faults - state_begin.faults;
    sample.rss_delta = static_cast<std::int64_t>(state_end.rss) -
                       static_cast<std::int64_t>(state_begin.rss);
    sample.score_sha256 = agent_memory::sha256_bytes_hex(digest);
    return sample;
}

nlohmann::json sample_json(const Sample& value, std::uint64_t seed,
                           const std::string& layout, std::size_t request,
                           std::size_t pass) {
    return {{"seed", seed}, {"layout", layout}, {"request", request}, {"pass", pass},
            {"fetch_ms", value.fetch_ms}, {"decode_ms", value.decode_ms},
            {"dot_and_max_ms", value.dot_ms}, {"address_score_ms", value.score_ms},
            {"total_ms", value.total_ms}, {"page_faults", value.page_faults},
            {"rss_delta_bytes", value.rss_delta}, {"logical_bytes", value.logical_bytes},
            {"random_reads", value.random_reads}, {"addresses_scored", 1024},
            {"address_spans", value.address_spans},
            {"representatives_scored", value.representatives},
            {"score_sha256", value.score_sha256},
            {"maximum_max_abs_error", value.maximum_max_abs_error},
            {"address_score_max_abs_error", value.address_score_max_abs_error},
            {"representative_winner_agreement", value.representative_winner_agreement},
            {"address_top128_overlap", value.address_top128_overlap}};
}

nlohmann::json access_sample_json(const Sample& value, std::uint64_t seed,
                                  const std::string& treatment,
                                  std::size_t request, std::size_t pass) {
    auto result = sample_json(value, seed, "address_major_int8", request, pass);
    result["kernel"] = "fused_int8_scalar";
    result["scorer"] = "batched_avx2_r0";
    result["access"] = treatment;
    return result;
}

nlohmann::json compression_sample_json(const Sample& value, std::uint64_t seed,
                                       const std::string& treatment,
                                       std::size_t request, std::size_t pass) {
    auto result = sample_json(value, seed, "address_major_int8", request, pass);
    result["kernel"] = "fused_int8_scalar_or_lossless_simdcomp_unpack";
    result["scorer"] = "batched_avx2_r0";
    result["access"] = "mmap_direct_offset_order";
    result["compression"] = treatment;
    return result;
}

nlohmann::json lossless_block_sample_json(const Sample& value,
                                           std::uint64_t seed,
                                           const std::string& treatment,
                                           std::size_t request,
                                           std::size_t pass) {
    auto result = sample_json(value, seed, "address_major_int8", request, pass);
    result["kernel"] = "lossless_decode_then_fused_int8_scalar";
    result["scorer"] = "batched_avx2_r0";
    result["access"] = "mmap_address_block_offset_order";
    result["compression"] = treatment;
    return result;
}

nlohmann::json kernel_sample_json(const Sample& value, std::uint64_t seed,
                                  const std::string& kernel,
                                  std::size_t request, std::size_t pass) {
    auto result = sample_json(value, seed, "address_major_int8", request, pass);
    result["kernel"] = kernel;
    return result;
}

nlohmann::json scorer_sample_json(const Sample& value, std::uint64_t seed,
                                  const std::string& scorer,
                                  std::size_t request, std::size_t pass) {
    auto result = sample_json(value, seed, "address_major_int8", request, pass);
    result["kernel"] = "fused_int8_scalar";
    result["scorer"] = scorer;
    return result;
}

void prefault(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    std::vector<char> buffer(8 * 1024 * 1024);
    while (stream.read(buffer.data(), static_cast<std::streamsize>(buffer.size())) ||
           stream.gcount() != 0) {}
}

void warm(const std::filesystem::path& manifest_path,
          const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    std::vector<SeedContext> seeds;
    for (const auto& row : manifest.at("seeds")) seeds.push_back(load_seed(manifest_path, row));
    const std::array<std::string, 3> layouts = {"address_major_fp32",
        "address_major_int8", "document_major_int8_indirect"};
    for (const auto& seed : seeds) {
        for (const auto& layout : layouts) prefault(payload_path(seed.root, layout_row(seed, layout)));
    }
    for (const auto& seed : seeds) {
        for (const auto& layout : layouts) {
            for (std::size_t request = 0; request != 152; ++request) {
                (void)measure(seed, layout, request);
            }
        }
    }
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>> schedule;
    for (std::size_t pass = 0; pass != 3; ++pass)
        for (std::size_t seed = 0; seed != seeds.size(); ++seed)
            for (std::size_t layout = 0; layout != layouts.size(); ++layout)
                for (std::size_t request = 0; request != 152; ++request)
                    schedule.emplace_back(pass, seed, layout, request);
    std::mt19937_64 random(2026083101);
    std::shuffle(schedule.begin(), schedule.end(), random);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& [pass, seed, layout, request] : schedule) {
        samples.push_back(sample_json(measure(seeds[seed], layouts[layout], request),
                                      seeds[seed].seed, layouts[layout], request, pass));
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_layout_warm_samples"},
        {"manifest_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"cache_state", "sequential_prefault_warm_page_cache"},
        {"samples", samples}});
}

void kernel_warm(const std::filesystem::path& manifest_path,
                 const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    std::vector<SeedContext> seeds;
    for (const auto& row : manifest.at("seeds")) seeds.push_back(load_seed(manifest_path, row));
    const std::array<std::string, 4> kernels = {"decode_fp32_scalar_dot",
        "fused_int8_scalar", "fused_int8_avx2", "fused_int8_avx2_ordered"};
    for (const auto& seed : seeds) {
        prefault(payload_path(seed.root, layout_row(seed, "address_major_int8")));
        for (const auto& kernel : kernels)
            for (std::size_t request = 0; request != 152; ++request)
                (void)measure(seed, "address_major_int8", request, kernel);
    }
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>> schedule;
    for (std::size_t pass = 0; pass != 3; ++pass)
        for (std::size_t seed = 0; seed != seeds.size(); ++seed)
            for (std::size_t kernel = 0; kernel != kernels.size(); ++kernel)
                for (std::size_t request = 0; request != 152; ++request)
                    schedule.emplace_back(pass, seed, kernel, request);
    std::mt19937_64 random(2026083102);
    std::shuffle(schedule.begin(), schedule.end(), random);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& [pass, seed, kernel, request] : schedule) {
        samples.push_back(kernel_sample_json(measure(seeds[seed],
            "address_major_int8", request, kernels[kernel]), seeds[seed].seed,
            kernels[kernel], request, pass));
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_kernel_warm_samples"},
        {"manifest_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"simd_available", static_cast<bool>(AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2)},
        {"samples", samples}});
}

void scorer_warm(const std::filesystem::path& manifest_path,
                 const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    std::vector<SeedContext> seeds;
    for (const auto& row : manifest.at("seeds")) seeds.push_back(load_seed(manifest_path, row));
    const std::array<std::string, 2> scorers = {"scalar_r0", "batched_avx2_r0"};
    for (const auto& seed : seeds) {
        prefault(payload_path(seed.root, layout_row(seed, "address_major_int8")));
        for (const auto& scorer : scorers)
            for (std::size_t request = 0; request != 152; ++request)
                (void)measure(seed, "address_major_int8", request,
                              "fused_int8_scalar", scorer);
    }
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>> schedule;
    for (std::size_t pass = 0; pass != 3; ++pass)
        for (std::size_t seed = 0; seed != seeds.size(); ++seed)
            for (std::size_t scorer = 0; scorer != scorers.size(); ++scorer)
                for (std::size_t request = 0; request != 152; ++request)
                    schedule.emplace_back(pass, seed, scorer, request);
    std::mt19937_64 random(2026083103);
    std::shuffle(schedule.begin(), schedule.end(), random);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& [pass, seed, scorer, request] : schedule) {
        samples.push_back(scorer_sample_json(measure(seeds[seed],
            "address_major_int8", request, "fused_int8_scalar", scorers[scorer]),
            seeds[seed].seed, scorers[scorer], request, pass));
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_batched_scorer_warm_samples"},
        {"manifest_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"simd_available", static_cast<bool>(AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2)},
        {"samples", samples}});
}

void access_warm(const std::filesystem::path& manifest_path,
                 const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    std::vector<SeedContext> seeds;
    std::vector<std::unique_ptr<MappedFile>> mappings;
    for (const auto& row : manifest.at("seeds")) {
        seeds.push_back(load_seed(manifest_path, row));
        const auto& seed = seeds.back();
        const auto path = payload_path(seed.root,
                                       layout_row(seed, "address_major_int8"));
        prefault(path);
        mappings.push_back(std::make_unique<MappedFile>(path));
    }
    const std::array<std::string, 4> treatments = {"seek_read_staging",
        "mmap_copy_staging", "mmap_direct_shortlist", "mmap_direct_offset_order"};
    const auto invoke = [&](std::size_t seed, const std::string& treatment,
                            std::size_t request) {
        return treatment == "seek_read_staging"
            ? measure(seeds[seed], "address_major_int8", request,
                      "fused_int8_scalar", "batched_avx2_r0")
            : measure_mapped(seeds[seed], *mappings[seed], treatment, request);
    };
    for (std::size_t seed = 0; seed != seeds.size(); ++seed)
        for (const auto& treatment : treatments)
            for (std::size_t request = 0; request != 152; ++request)
                (void)invoke(seed, treatment, request);
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>> schedule;
    for (std::size_t pass = 0; pass != 3; ++pass)
        for (std::size_t seed = 0; seed != seeds.size(); ++seed)
            for (std::size_t treatment = 0; treatment != treatments.size(); ++treatment)
                for (std::size_t request = 0; request != 152; ++request)
                    schedule.emplace_back(pass, seed, treatment, request);
    std::mt19937_64 random(2026083104);
    std::shuffle(schedule.begin(), schedule.end(), random);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& [pass, seed, treatment, request] : schedule) {
        samples.push_back(access_sample_json(invoke(seed, treatments[treatment], request),
            seeds[seed].seed, treatments[treatment], request, pass));
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_mapped_access_warm_samples"},
        {"manifest_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"samples", samples}});
}

void access_cold(const std::filesystem::path& manifest_path,
                 std::uint64_t wanted_seed, const std::string& treatment,
                 std::size_t request, const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto found = std::find_if(manifest.at("seeds").begin(), manifest.at("seeds").end(),
        [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(found != manifest.at("seeds").end(), "R4 access cold seed differs");
    const auto seed = load_seed(manifest_path, *found);
    const auto path = payload_path(seed.root, layout_row(seed, "address_major_int8"));
    const auto sample = treatment == "seek_read_staging"
        ? measure(seed, "address_major_int8", request, "fused_int8_scalar",
                  "batched_avx2_r0")
        : measure_mapped(seed, MappedFile(path), treatment, request);
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_mapped_access_process_cold_sample"},
        {"definition", "fresh_process_first_request_os_page_cache_uncontrolled"},
        {"sample", access_sample_json(sample, wanted_seed, treatment, request, 0)}});
}

struct CompressionMaps {
    std::filesystem::path raw_path, fixed_path, for_path, zigzag_path;
    std::unique_ptr<MappedFile> raw, fixed, adaptive_for, adaptive_zigzag;
    std::vector<std::uint64_t> for_offsets, zigzag_offsets;
};

std::unique_ptr<CompressionMaps> load_compression_maps(
        const SeedContext& seed, const std::filesystem::path& compression_manifest_path,
        const nlohmann::json& compression_seed) {
    const auto root = compression_manifest_path.parent_path() /
                      ("seed-" + std::to_string(seed.seed));
    const auto& files = compression_seed.at("files");
    const auto path = [&](const std::string& name) {
        return payload_path(root, role(files, name));
    };
    auto result = std::make_unique<CompressionMaps>();
    result->raw_path = payload_path(seed.root,
                                    layout_row(seed, "address_major_int8"));
    result->fixed_path = path("simdcomp_fixed8");
    result->for_path = path("simdcomp_adaptive_for");
    result->zigzag_path = path("simdcomp_adaptive_zigzag");
    result->raw = std::make_unique<MappedFile>(result->raw_path);
    result->fixed = std::make_unique<MappedFile>(result->fixed_path);
    result->adaptive_for = std::make_unique<MappedFile>(result->for_path);
    result->adaptive_zigzag = std::make_unique<MappedFile>(result->zigzag_path);
    result->for_offsets = read_values<std::uint64_t>(path("adaptive_for_offsets"));
    result->zigzag_offsets = read_values<std::uint64_t>(
        path("adaptive_zigzag_offsets"));
    require(result->for_offsets.size() == seed.address_counts.size() &&
            result->zigzag_offsets.size() == seed.address_counts.size(),
            "R4 compression offset count differs");
    return result;
}

Sample invoke_compression(const SeedContext& seed, const CompressionMaps& maps,
                          const std::string& treatment, std::size_t request) {
    if (treatment == "raw_int8")
        return measure_mapped(seed, *maps.raw, "mmap_direct_offset_order", request);
    if (treatment == "simdcomp_fixed8")
        return measure_compressed(seed, *maps.fixed, nullptr, treatment, request);
    if (treatment == "simdcomp_adaptive_for")
        return measure_compressed(seed, *maps.adaptive_for, &maps.for_offsets,
                                  treatment, request);
    require(treatment == "simdcomp_adaptive_zigzag",
            "R4 compression treatment differs");
    return measure_compressed(seed, *maps.adaptive_zigzag, &maps.zigzag_offsets,
                              treatment, request);
}

void compression_warm(const std::filesystem::path& manifest_path,
                      const std::filesystem::path& compression_manifest_path,
                      const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto compression_manifest = read_json(compression_manifest_path);
    std::vector<SeedContext> seeds;
    std::vector<std::unique_ptr<CompressionMaps>> maps;
    for (const auto& row : manifest.at("seeds")) {
        seeds.push_back(load_seed(manifest_path, row));
        const auto seed_value = seeds.back().seed;
        const auto found = std::find_if(compression_manifest.at("seeds").begin(),
            compression_manifest.at("seeds").end(),
            [&](const nlohmann::json& value) {
                return value.at("seed").get<std::uint64_t>() ==
                       seed_value;
            });
        require(found != compression_manifest.at("seeds").end(),
                "R4 compression manifest seed differs");
        maps.push_back(load_compression_maps(seeds.back(), compression_manifest_path,
                                             *found));
        prefault(maps.back()->raw_path);
        prefault(maps.back()->fixed_path);
        prefault(maps.back()->for_path);
        prefault(maps.back()->zigzag_path);
    }
    const std::array<std::string, 4> treatments = {"raw_int8", "simdcomp_fixed8",
        "simdcomp_adaptive_for", "simdcomp_adaptive_zigzag"};
    for (std::size_t seed = 0; seed != seeds.size(); ++seed)
        for (const auto& treatment : treatments)
            for (std::size_t request = 0; request != 152; ++request)
                (void)invoke_compression(seeds[seed], *maps[seed], treatment, request);
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>> schedule;
    for (std::size_t pass = 0; pass != 3; ++pass)
        for (std::size_t seed = 0; seed != seeds.size(); ++seed)
            for (std::size_t treatment = 0; treatment != treatments.size(); ++treatment)
                for (std::size_t request = 0; request != 152; ++request)
                    schedule.emplace_back(pass, seed, treatment, request);
    std::mt19937_64 random(2026083105);
    std::shuffle(schedule.begin(), schedule.end(), random);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& [pass, seed, treatment, request] : schedule) {
        samples.push_back(compression_sample_json(invoke_compression(seeds[seed],
            *maps[seed], treatments[treatment], request), seeds[seed].seed,
            treatments[treatment], request, pass));
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_compression_warm_samples"},
        {"manifest_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"compression_manifest_sha256",
         agent_memory::sha256_file_hex(compression_manifest_path)},
        {"simdcomp_available", static_cast<bool>(AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP)},
        {"samples", samples}});
}

void compression_cold(const std::filesystem::path& manifest_path,
                      const std::filesystem::path& compression_manifest_path,
                      std::uint64_t wanted_seed, const std::string& treatment,
                      std::size_t request, const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto compression_manifest = read_json(compression_manifest_path);
    const auto found = std::find_if(manifest.at("seeds").begin(), manifest.at("seeds").end(),
        [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    const auto compression_found = std::find_if(compression_manifest.at("seeds").begin(),
        compression_manifest.at("seeds").end(),
        [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(found != manifest.at("seeds").end() &&
            compression_found != compression_manifest.at("seeds").end(),
            "R4 compression cold seed differs");
    const auto seed = load_seed(manifest_path, *found);
    const auto maps = load_compression_maps(seed, compression_manifest_path,
                                            *compression_found);
    const auto sample = invoke_compression(seed, *maps, treatment, request);
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_compression_process_cold_sample"},
        {"definition", "fresh_process_first_request_os_page_cache_uncontrolled"},
        {"sample", compression_sample_json(sample, wanted_seed, treatment,
                                             request, 0)}});
}

const nlohmann::json& seed_row(const nlohmann::json& manifest,
                               std::uint64_t wanted_seed,
                               const char* message) {
    const auto found = std::find_if(manifest.at("seeds").begin(),
        manifest.at("seeds").end(), [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(found != manifest.at("seeds").end(), message);
    return *found;
}

void lossless_block_warm(const std::filesystem::path& manifest_path,
                         const std::filesystem::path& block_manifest_path,
                         const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto block_manifest = read_json(block_manifest_path);
    std::vector<SeedContext> seeds;
    std::vector<std::unique_ptr<LosslessBlockMaps>> maps;
    for (const auto& row : manifest.at("seeds")) {
        seeds.push_back(load_seed(manifest_path, row));
        const auto& block_row = seed_row(block_manifest, seeds.back().seed,
                                          "R4 lossless manifest seed differs");
        maps.push_back(load_lossless_block_maps(seeds.back(), block_manifest_path,
                                                block_row));
        prefault(maps.back()->raw_path);
        prefault(maps.back()->zstd_path);
        prefault(maps.back()->zstd_dictionary_path);
        prefault(maps.back()->vbyte_path);
    }
    const std::array<std::string, 4> treatments = {"raw_int8", "zstd_block",
        "zstd_dictionary_block", "vbyte_zigzag"};
    for (std::size_t seed = 0; seed != seeds.size(); ++seed)
        for (const auto& treatment : treatments)
            for (std::size_t request = 0; request != 152; ++request)
                (void)measure_lossless_block(seeds[seed], *maps[seed], treatment,
                                             request);
    std::vector<std::tuple<std::size_t, std::size_t, std::size_t, std::size_t>>
        schedule;
    for (std::size_t pass = 0; pass != 3; ++pass)
        for (std::size_t seed = 0; seed != seeds.size(); ++seed)
            for (std::size_t treatment = 0; treatment != treatments.size(); ++treatment)
                for (std::size_t request = 0; request != 152; ++request)
                    schedule.emplace_back(pass, seed, treatment, request);
    std::mt19937_64 random(2026083111);
    std::shuffle(schedule.begin(), schedule.end(), random);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& [pass, seed, treatment, request] : schedule) {
        samples.push_back(lossless_block_sample_json(measure_lossless_block(
            seeds[seed], *maps[seed], treatments[treatment], request),
            seeds[seed].seed, treatments[treatment], request, pass));
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_lossless_block_warm_samples"},
        {"manifest_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"block_manifest_sha256", agent_memory::sha256_file_hex(block_manifest_path)},
        {"zstd_available", static_cast<bool>(AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD)},
        {"samples", samples}});
}

void lossless_block_cold(const std::filesystem::path& manifest_path,
                         const std::filesystem::path& block_manifest_path,
                         std::uint64_t wanted_seed,
                         const std::string& treatment,
                         std::size_t request,
                         const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto block_manifest = read_json(block_manifest_path);
    const auto seed = load_seed(manifest_path, seed_row(
        manifest, wanted_seed, "R4 lossless cold seed differs"));
    const auto maps = load_lossless_block_maps(seed, block_manifest_path,
        seed_row(block_manifest, wanted_seed,
                 "R4 lossless cold materialization seed differs"));
    const auto sample = measure_lossless_block(seed, *maps, treatment, request);
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int8_lossless_block_process_cold_sample"},
        {"definition", "fresh_process_first_request_os_page_cache_uncontrolled"},
        {"sample", lossless_block_sample_json(sample, wanted_seed, treatment,
                                                request, 0)}});
}

void cold(const std::filesystem::path& manifest_path, std::uint64_t wanted_seed,
          const std::string& layout, std::size_t request,
          const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto found = std::find_if(manifest.at("seeds").begin(), manifest.at("seeds").end(),
        [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(found != manifest.at("seeds").end(), "R4 layout cold seed differs");
    const auto seed = load_seed(manifest_path, *found);
    require(request < 152, "R4 layout cold request differs");
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_layout_process_cold_sample"},
        {"definition", "fresh_process_first_request_os_page_cache_uncontrolled"},
        {"sample", sample_json(measure(seed, layout, request), wanted_seed,
                                layout, request, 0)}});
}

struct EndToEndContext {
    SeedContext seed;
    std::unique_ptr<MappedFile> representatives;
};

void validate_end_to_end_protocol(const nlohmann::json& protocol) {
    require(protocol.value("schema_version", 0) == 1 &&
            protocol.value("family", "") == "neuroute_r4_native_end_to_end_protocol",
            "R4 end-to-end protocol identity differs");
    require(agent_memory::sha256_file_hex(
                protocol.at("layout_manifest").get<std::string>()) ==
                protocol.at("activation").at("layout_manifest_sha256").get<std::string>() &&
            agent_memory::sha256_file_hex(
                protocol.at("native_input_manifest").get<std::string>()) ==
                protocol.at("activation").at("native_input_manifest_sha256").get<std::string>() &&
            agent_memory::sha256_file_hex(
                protocol.at("document_id_rank_file").get<std::string>()) ==
                protocol.at("document_id_rank_sha256").get<std::string>(),
            "R4 end-to-end protocol activation differs");
    require(protocol.at("candidate_limit").get<std::size_t>() == 5000 &&
            protocol.at("hamming_limit").get<std::size_t>() == 768 &&
            protocol.at("adc_limit").get<std::size_t>() == 64 &&
            protocol.at("exact_limit").get<std::size_t>() == 10 &&
            protocol.at("requests").size() == 76,
            "R4 end-to-end frozen cascade differs");
}

EndToEndContext load_end_to_end_seed(const nlohmann::json& protocol,
                                     std::uint64_t wanted_seed) {
    const auto manifest_path = std::filesystem::path(
        protocol.at("layout_manifest").get<std::string>());
    const auto manifest = read_json(manifest_path);
    const auto found = std::find_if(manifest.at("seeds").begin(),
        manifest.at("seeds").end(), [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(found != manifest.at("seeds").end(), "R4 end-to-end seed differs");
    auto seed = load_seed(manifest_path, *found);
    const auto store = payload_path(seed.root, layout_row(seed, "address_major_int8"));
    return {std::move(seed), std::make_unique<MappedFile>(store)};
}

std::pair<std::size_t, std::size_t> end_to_end_request(
        const nlohmann::json& protocol, std::size_t request) {
    const auto found = std::find_if(protocol.at("requests").begin(),
        protocol.at("requests").end(), [&](const nlohmann::json& row) {
            return row.at("request").get<std::size_t>() == request;
        });
    require(found != protocol.at("requests").end(),
            "R4 end-to-end requested query differs");
    return {found->at("request").get<std::size_t>(),
            found->at("native_query").get<std::size_t>()};
}

std::vector<EndToEndResult> end_to_end_batch(
        const EndToEndContext& context, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        const nlohmann::json& protocol, const std::string& treatment,
        std::size_t workers) {
    const auto& requests = protocol.at("requests");
    std::vector<EndToEndResult> values(requests.size());
    std::atomic<std::size_t> following{0};
    std::vector<std::thread> threads;
    std::vector<std::exception_ptr> failures(workers);
    threads.reserve(workers);
    for (std::size_t worker = 0; worker != workers; ++worker) {
        threads.emplace_back([&, worker] {
            try {
                for (;;) {
                    const auto index = following.fetch_add(1);
                    if (index >= requests.size()) break;
                    const auto request = requests.at(index).at("request").get<std::size_t>();
                    const auto native_query = requests.at(index).at("native_query").get<std::size_t>();
                    values[index] = end_to_end_query(context.seed,
                        *context.representatives, input, hamming, treatment,
                        request, native_query);
                }
            } catch (...) {
                failures[worker] = std::current_exception();
            }
        });
    }
    for (auto& thread : threads) thread.join();
    for (const auto& failure : failures) if (failure) std::rethrow_exception(failure);
    return values;
}

void end_to_end_warm(const std::filesystem::path& protocol_path,
                     const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    validate_end_to_end_protocol(protocol);
    const auto input = load_native_input(
        protocol.at("native_input_manifest").get<std::string>(),
        protocol.at("document_id_rank_file").get<std::string>());
    const agent_memory::HammingDistanceComputer hamming(binary_code_words);
    nlohmann::json samples = nlohmann::json::array();
    nlohmann::json concurrency = nlohmann::json::array();
    for (const auto& seed_value : protocol.at("seeds")) {
        const auto seed = seed_value.get<std::uint64_t>();
        const auto context = load_end_to_end_seed(protocol, seed);
        for (const auto& treatment_value : protocol.at("treatments")) {
            const auto treatment = treatment_value.get<std::string>();
            for (std::size_t pass = 0;
                 pass != protocol.at("warmup_passes").get<std::size_t>(); ++pass) {
                static_cast<void>(end_to_end_batch(context, input, hamming,
                                                   protocol, treatment, 1));
            }
            for (std::size_t pass = 0;
                 pass != protocol.at("measured_passes").get<std::size_t>(); ++pass) {
                const auto values = end_to_end_batch(context, input, hamming,
                                                     protocol, treatment, 1);
                for (std::size_t index = 0; index != values.size(); ++index) {
                    const auto request = protocol.at("requests").at(index)
                        .at("request").get<std::size_t>();
                    const auto native_query = protocol.at("requests").at(index)
                        .at("native_query").get<std::size_t>();
                    samples.push_back(end_to_end_json(values[index], seed, treatment,
                                                      request, native_query, pass));
                }
            }
        }
        for (const auto& treatment_value : protocol.at("concurrency_treatments")) {
            const auto treatment = treatment_value.get<std::string>();
            for (const auto worker_value : protocol.at("workers")) {
                const auto workers = worker_value.get<std::size_t>();
                static_cast<void>(end_to_end_batch(context, input, hamming,
                                                   protocol, treatment, workers));
                for (std::size_t pass = 0;
                     pass != protocol.at("concurrency_passes").get<std::size_t>(); ++pass) {
                    const auto begin = Clock::now();
                    const auto values = end_to_end_batch(context, input, hamming,
                                                         protocol, treatment, workers);
                    const auto wall_ms = milliseconds(begin, Clock::now());
                    std::vector<double> query_ms;
                    std::vector<std::uint8_t> exact_digest;
                    query_ms.reserve(values.size());
                    for (std::size_t index = 0; index != values.size(); ++index) {
                        query_ms.push_back(values[index].timing.total);
                        const auto digest = u32_sequence_sha256(values[index].exact);
                        exact_digest.insert(exact_digest.end(), digest.begin(), digest.end());
                    }
                    concurrency.push_back({{"seed", seed}, {"treatment", treatment},
                        {"workers", workers}, {"pass", pass},
                        {"query_count", values.size()}, {"wall_ms", wall_ms},
                        {"throughput_queries_per_second",
                            1000.0 * static_cast<double>(values.size()) / wall_ms},
                        {"per_query_total_ms", query_ms},
                        {"exact_batch_sha256",
                            agent_memory::sha256_bytes_hex(exact_digest)}});
                }
            }
        }
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_native_end_to_end_warm_samples"},
        {"protocol_sha256", agent_memory::sha256_file_hex(protocol_path)},
        {"hamming_backend", agent_memory::hamming_distance_backend_name(
            hamming.backend())}, {"samples", samples},
        {"concurrency_samples", concurrency}});
}

void end_to_end_cold(const std::filesystem::path& protocol_path,
                     std::uint64_t seed, const std::string& treatment,
                     std::size_t request,
                     const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    validate_end_to_end_protocol(protocol);
    const auto [local_request, native_query] = end_to_end_request(protocol, request);
    const auto input = load_native_input(
        protocol.at("native_input_manifest").get<std::string>(),
        protocol.at("document_id_rank_file").get<std::string>());
    const auto context = load_end_to_end_seed(protocol, seed);
    const agent_memory::HammingDistanceComputer hamming(binary_code_words);
    const auto value = end_to_end_query(context.seed, *context.representatives,
                                        input, hamming, treatment, local_request,
                                        native_query);
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_native_end_to_end_process_cold_sample"},
        {"definition", "fresh_process_first_query_os_page_cache_uncontrolled"},
        {"protocol_sha256", agent_memory::sha256_file_hex(protocol_path)},
        {"sample", end_to_end_json(value, seed, treatment, local_request,
                                    native_query, 0)}});
}

struct Int5IntegrationContext {
    SeedContext seed;
    std::unique_ptr<MappedFile> representatives;
    std::vector<std::uint64_t> mixed_address_byte_offsets;
    std::string treatment;
    std::size_t record_bytes = 0;
    std::vector<std::vector<AddressSpan>> routing_spans;
    std::vector<std::size_t> routing_representatives;
};

void validate_int5_integration_protocol(const nlohmann::json& protocol) {
    require(protocol.value("schema_version", 0) == 1 &&
            protocol.value("family", "") ==
                "neuroute_r4_int5_physical_integration_protocol",
            "R4 INT5 integration protocol identity differs");
    require(agent_memory::sha256_file_hex(
                protocol.at("layout_manifest").get<std::string>()) ==
                protocol.at("activation").at("layout_manifest_sha256").get<std::string>() &&
            agent_memory::sha256_file_hex(
                protocol.at("integration_manifest").get<std::string>()) ==
                protocol.at("integration_manifest_sha256").get<std::string>() &&
            agent_memory::sha256_file_hex(
                protocol.at("native_input_manifest").get<std::string>()) ==
                protocol.at("activation").at("native_input_manifest_sha256").get<std::string>() &&
            agent_memory::sha256_file_hex(
                protocol.at("document_id_rank_file").get<std::string>()) ==
                protocol.at("document_id_rank_sha256").get<std::string>(),
            "R4 INT5 integration activation differs");
    require(protocol.at("treatments") == nlohmann::json::array({
                "homogeneous_int8", "int5_side_store", "int5_mixed"}) &&
            protocol.at("requests").size() == 76 &&
            protocol.at("candidate_limit").get<std::size_t>() == 5000 &&
            protocol.at("hamming_limit").get<std::size_t>() == 768 &&
            protocol.at("adc_limit").get<std::size_t>() == 64 &&
            protocol.at("exact_limit").get<std::size_t>() == 10,
            "R4 INT5 integration frozen matrix differs");
}

std::filesystem::path absolute_payload_path(const nlohmann::json& row) {
    const auto path = std::filesystem::path(row.at("path").get<std::string>());
    require(path.is_absolute(), "R4 INT5 integration payload is not absolute");
    return path;
}

Int5IntegrationContext load_int5_integration_context(
        const nlohmann::json& protocol, std::uint64_t wanted_seed,
        const std::string& treatment) {
    const auto layout_manifest_path = std::filesystem::path(
        protocol.at("layout_manifest").get<std::string>());
    const auto layout_manifest = read_json(layout_manifest_path);
    const auto layout_seed = std::find_if(layout_manifest.at("seeds").begin(),
        layout_manifest.at("seeds").end(), [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(layout_seed != layout_manifest.at("seeds").end(),
            "R4 INT5 integration layout seed differs");
    auto seed = load_seed(layout_manifest_path, *layout_seed);
    const auto integration_manifest = read_json(
        protocol.at("integration_manifest").get<std::string>());
    const auto integration_seed = std::find_if(
        integration_manifest.at("seeds").begin(),
        integration_manifest.at("seeds").end(),
        [&](const nlohmann::json& row) {
            return row.at("seed").get<std::uint64_t>() == wanted_seed;
        });
    require(integration_seed != integration_manifest.at("seeds").end(),
            "R4 INT5 integration materialized seed differs");
    const auto& layout = role(integration_seed->at("layouts"), treatment);
    const auto record_bytes = layout.at("record_bytes").get<std::size_t>();
    require((treatment == "homogeneous_int8" && record_bytes == 388) ||
            ((treatment == "int5_side_store" || treatment == "int5_mixed") &&
             record_bytes == 244), "R4 INT5 integration record bytes differ");
    std::vector<std::uint64_t> offsets;
    if (treatment == "int5_mixed") {
        const auto& mapping = role(integration_seed->at("mappings"),
                                   "mixed_address_byte_offsets");
        offsets = read_values<std::uint64_t>(absolute_payload_path(mapping));
        require(offsets.size() == seed.occupied_addresses.size(),
                "R4 INT5 mixed offset count differs");
    }
    return {std::move(seed),
            std::make_unique<MappedFile>(absolute_payload_path(layout)),
            std::move(offsets), treatment, record_bytes};
}

void materialize_int5_bitsliced_mixed(
        const std::filesystem::path& parent_protocol_path,
        std::uint64_t seed, const std::filesystem::path& output_path,
        const std::filesystem::path& receipt_path) {
    const auto protocol = read_json(parent_protocol_path);
    validate_int5_integration_protocol(protocol);
    const auto context = load_int5_integration_context(protocol, seed,
                                                        "int5_mixed");
    require(context.record_bytes == 244 &&
            context.mixed_address_byte_offsets.size() ==
                context.seed.representative_counts.size(),
            "R4 INT5 bitsliced materialization shape differs");
    std::ofstream output(output_path, std::ios::binary);
    require(static_cast<bool>(output),
            "R4 INT5 bitsliced materialization output open failed");
    std::array<std::uint8_t, 244> converted{};
    std::size_t cursor = 0;
    std::uint64_t representatives = 0;
    for (std::size_t row = 0;
         row != context.mixed_address_byte_offsets.size(); ++row) {
        const auto begin = static_cast<std::size_t>(
            context.mixed_address_byte_offsets[row]);
        const auto end = row + 1 == context.mixed_address_byte_offsets.size()
            ? context.representatives->size()
            : static_cast<std::size_t>(
                context.mixed_address_byte_offsets[row + 1]);
        require(begin == cursor && begin <= end,
                "R4 INT5 bitsliced address offsets differ");
        const auto count = static_cast<std::size_t>(
            context.seed.representative_counts[row]);
        require(begin + count * 244U <= end,
                "R4 INT5 bitsliced representative prefix differs");
        for (std::size_t slot = 0; slot != count; ++slot) {
            int5_power_half_bitslice_record(context.representatives->data() +
                begin + slot * 244U, converted.data());
            output.write(reinterpret_cast<const char*>(converted.data()),
                         static_cast<std::streamsize>(converted.size()));
            ++representatives;
        }
        const auto remainder = begin + count * 244U;
        output.write(reinterpret_cast<const char*>(
            context.representatives->data() + remainder),
            static_cast<std::streamsize>(end - remainder));
        require(static_cast<bool>(output),
                "R4 INT5 bitsliced materialization write failed");
        cursor = end;
    }
    output.close();
    require(std::filesystem::file_size(output_path) ==
            context.representatives->size(),
            "R4 INT5 bitsliced materialization size differs");
    write_json(receipt_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int5_bitsliced_materialization_receipt"},
        {"parent_protocol_sha256",
            agent_memory::sha256_file_hex(parent_protocol_path)},
        {"seed", seed}, {"representatives", representatives},
        {"bytes", std::filesystem::file_size(output_path)},
        {"record_bytes", 244}, {"bitplanes_per_32_dimensions", 5},
        {"output_sha256", agent_memory::sha256_file_hex(output_path)}});
}

void materialize_int5_avx2_mixed(
        const std::filesystem::path& parent_protocol_path,
        std::uint64_t seed, const std::filesystem::path& output_path,
        const std::filesystem::path& receipt_path) {
    const auto protocol = read_json(parent_protocol_path);
    validate_int5_integration_protocol(protocol);
    const auto context = load_int5_integration_context(protocol, seed,
                                                        "int5_mixed");
    require(context.record_bytes == 244 &&
            context.mixed_address_byte_offsets.size() ==
                context.seed.representative_counts.size(),
            "R4 INT5 AVX2 materialization shape differs");
    std::ofstream output(output_path, std::ios::binary);
    require(static_cast<bool>(output),
            "R4 INT5 AVX2 materialization output open failed");
    std::array<std::uint8_t, 244> converted;
    std::size_t cursor = 0;
    std::uint64_t representatives = 0;
    for (std::size_t row = 0;
         row != context.mixed_address_byte_offsets.size(); ++row) {
        const auto begin = static_cast<std::size_t>(
            context.mixed_address_byte_offsets[row]);
        const auto end = row + 1 == context.mixed_address_byte_offsets.size()
            ? context.representatives->size()
            : static_cast<std::size_t>(
                context.mixed_address_byte_offsets[row + 1]);
        require(begin == cursor && begin <= end,
                "R4 INT5 AVX2 address offsets differ");
        const auto count = static_cast<std::size_t>(
            context.seed.representative_counts[row]);
        require(begin + count * 244U <= end,
                "R4 INT5 AVX2 representative prefix differs");
        for (std::size_t slot = 0; slot != count; ++slot) {
            int5_power_half_avx2_record(context.representatives->data() +
                begin + slot * 244U, converted.data());
            output.write(reinterpret_cast<const char*>(converted.data()),
                         static_cast<std::streamsize>(converted.size()));
            ++representatives;
        }
        const auto remainder = begin + count * 244U;
        output.write(reinterpret_cast<const char*>(
            context.representatives->data() + remainder),
            static_cast<std::streamsize>(end - remainder));
        require(static_cast<bool>(output),
                "R4 INT5 AVX2 materialization write failed");
        cursor = end;
    }
    output.close();
    require(std::filesystem::file_size(output_path) ==
            context.representatives->size(),
            "R4 INT5 AVX2 materialization size differs");
    write_json(receipt_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int5_avx2_materialization_receipt"},
        {"parent_protocol_sha256",
            agent_memory::sha256_file_hex(parent_protocol_path)},
        {"seed", seed}, {"representatives", representatives},
        {"bytes", std::filesystem::file_size(output_path)},
        {"record_bytes", 244}, {"leading_avx2_dimensions", 256},
        {"tail_sse_dimensions", 128},
        {"output_sha256", agent_memory::sha256_file_hex(output_path)}});
}

RoutingValues int5_kernel_route(const Int5IntegrationContext& context,
                                std::size_t request,
                                const std::string& kernel) {
    require(request < 152, "R4 INT5 kernel request differs");
    const bool baseline = kernel == "homogeneous_int8";
    const bool legacy = kernel == "int5_direct_square_legacy";
    const bool direct = kernel == "int5_direct_square";
    const bool fused_sse = kernel == "int5_fused_sse";
    const bool fused_avx2 = kernel == "int5_fused_avx2";
    const bool fused_avx2_q8 = kernel == "int5_fused_avx2_q8";
    const bool shuffle = kernel == "int5_pshufb_square";
    const bool bitsliced = kernel == "int5_bitsliced_fp32_lut";
    const bool quantized = kernel == "int5_bitsliced_q8_lut";
    const bool direct_q8 = kernel == "int5_direct_q8_integer";
    const bool direct_q16 = kernel == "int5_direct_q16_integer";
    require(baseline || legacy || direct || fused_sse || fused_avx2 ||
            fused_avx2_q8 || shuffle || bitsliced || quantized || direct_q8 ||
            direct_q16,
            "R4 INT5 kernel differs");
    require(context.routing_spans.size() == 152 &&
            context.routing_representatives.size() == 152,
            "R4 INT5 prepared routing context differs");
    const auto& spans = context.routing_spans[request];
    const auto representatives = context.routing_representatives[request];
    RoutingValues result;
    result.representatives = representatives;
    result.logical_bytes = representatives * context.record_bytes;
    result.address_spans = spans.size();
    const auto* query = context.seed.queries.data() + request * dimensions;
    std::optional<std::array<float, 32>> table;
    if (legacy || shuffle) table.emplace(int5_power_half_decode_table());
    std::optional<PowerHalfQueryLuts> query_luts;
    if (bitsliced || quantized || direct_q8 || direct_q16 || fused_avx2_q8)
        query_luts.emplace(power_half_query_luts(query, bitsliced, quantized,
            direct_q8 || fused_avx2_q8, direct_q16));
    thread_local Maximums maximums;
    maximums.values.resize(addresses_per_query);
    maximums.winners.resize(addresses_per_query);
    std::fill(maximums.values.begin(), maximums.values.end(),
              -std::numeric_limits<float>::infinity());
    std::fill(maximums.winners.begin(), maximums.winners.end(), 0);
    auto begin = Clock::now();
    for (const auto& span : spans) {
        const auto count = span.byte_count / context.record_bytes;
        for (std::size_t slot = 0; slot != count; ++slot) {
            const auto* record = context.representatives->data() +
                span.byte_offset + slot * context.record_bytes;
            float score = 0.0F;
            if (baseline) {
                float scale = 0.0F;
                std::memcpy(&scale, record + dimensions, sizeof(scale));
                score = int8_dot_avx2(record, scale, query);
            } else if (legacy) {
                score = int5_power_half_dot_avx2_legacy(record, *table, query);
            } else if (direct) {
                score = int5_power_half_dot_avx2(record, query);
            } else if (fused_sse) {
                score = int5_power_half_fused_sse_dot(record, query);
            } else if (fused_avx2) {
                score = int5_power_half_fused_avx2_dot(record, query);
            } else if (fused_avx2_q8) {
                score = int5_power_half_fused_avx2_integer_dot(record,
                    query_luts->direct_int8.data(), query_luts->int8_scale);
            } else if (shuffle) {
                score = int5_power_half_dot_pshufb(record, *table, query);
            } else if (bitsliced) {
                score = int5_power_half_bitsliced_dot_fp32(record, *query_luts);
            } else if (quantized) {
                score = int5_power_half_bitsliced_dot_q8(record, *query_luts);
            } else if (direct_q8) {
                score = int5_power_half_integer_dot(record,
                    query_luts->direct_int8.data(), query_luts->int8_scale);
            } else {
                score = int5_power_half_integer_dot(record,
                    query_luts->direct_int16.data(), query_luts->int16_scale);
            }
            if (score > maximums.values[span.local]) {
                maximums.values[span.local] = score;
                maximums.winners[span.local] = static_cast<std::uint8_t>(slot);
            }
        }
    }
    result.timing.representative_dot = milliseconds(begin, Clock::now());
    begin = Clock::now();
    result.scores = address_scores_batched_avx2(context.seed, request,
                                                maximums.values);
    result.timing.address_score = milliseconds(begin, Clock::now());
    return result;
}

RoutingValues int5_integration_route(const Int5IntegrationContext& context,
                                     std::size_t request) {
    require(request < 152, "R4 INT5 integration request differs");
    const auto before = process_state();
    std::vector<AddressSpan> spans;
    spans.reserve(addresses_per_query);
    std::size_t representatives = 0;
    for (std::size_t local = 0; local != addresses_per_query; ++local) {
        const auto row = context.seed.shortlists[
            request * addresses_per_query + local];
        const auto count = static_cast<std::size_t>(
            context.seed.representative_counts[row]);
        const auto byte_offset = context.treatment == "homogeneous_int8"
            ? static_cast<std::size_t>(context.seed.address_offsets[row]) * 388U
            : context.treatment == "int5_side_store"
                ? static_cast<std::size_t>(context.seed.representative_offsets[row]) *
                    244U
                : static_cast<std::size_t>(
                    context.mixed_address_byte_offsets[row]);
        spans.push_back({local, byte_offset, count * context.record_bytes, 0});
        representatives += count;
    }
    std::sort(spans.begin(), spans.end(), [](const auto& left, const auto& right) {
        return left.byte_offset < right.byte_offset;
    });
    RoutingValues result;
    result.representatives = representatives;
    result.logical_bytes = representatives * context.record_bytes;
    result.address_spans = spans.size();
    const auto* query = context.seed.queries.data() + request * dimensions;
    const auto table = int5_power_half_decode_table();
    Maximums maximums;
    maximums.values.assign(addresses_per_query,
                           -std::numeric_limits<float>::infinity());
    maximums.winners.assign(addresses_per_query, 0);
    auto begin = Clock::now();
    for (const auto& span : spans) {
        const auto count = span.byte_count / context.record_bytes;
        for (std::size_t slot = 0; slot != count; ++slot) {
            const auto* record = context.representatives->data() +
                span.byte_offset + slot * context.record_bytes;
            float score = 0.0F;
            if (context.treatment == "homogeneous_int8") {
                float scale = 0.0F;
                std::memcpy(&scale, record + dimensions, sizeof(scale));
                score = int8_dot_avx2(record, scale, query);
            } else {
                score = int5_power_half_dot_avx2_legacy(record, table, query);
            }
            if (score > maximums.values[span.local]) {
                maximums.values[span.local] = score;
                maximums.winners[span.local] = static_cast<std::uint8_t>(slot);
            }
        }
    }
    result.timing.representative_dot = milliseconds(begin, Clock::now());
    begin = Clock::now();
    result.scores = address_scores_batched_avx2(context.seed, request,
                                                maximums.values);
    result.timing.address_score = milliseconds(begin, Clock::now());
    const auto after = process_state();
    result.page_faults = after.faults - before.faults;
    result.rss_delta = static_cast<std::int64_t>(after.rss) -
                       static_cast<std::int64_t>(before.rss);
    return result;
}

EndToEndResult int5_integration_query(
        const Int5IntegrationContext& context, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        std::size_t request, std::size_t native_query) {
    const auto total_begin = Clock::now();
    return finish_end_to_end_query(context.seed, input, hamming,
        int5_integration_route(context, request), request, native_query,
        total_begin);
}

std::vector<EndToEndResult> int5_integration_batch(
        const Int5IntegrationContext& context, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        const nlohmann::json& protocol) {
    std::vector<EndToEndResult> values;
    values.reserve(protocol.at("requests").size());
    for (const auto& row : protocol.at("requests")) {
        values.push_back(int5_integration_query(context, input, hamming,
            row.at("request").get<std::size_t>(),
            row.at("native_query").get<std::size_t>()));
    }
    return values;
}

void int5_integration_warm(const std::filesystem::path& protocol_path,
                           const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    validate_int5_integration_protocol(protocol);
    const auto input = load_native_input(
        protocol.at("native_input_manifest").get<std::string>(),
        protocol.at("document_id_rank_file").get<std::string>());
    const agent_memory::HammingDistanceComputer hamming(binary_code_words);
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& seed_value : protocol.at("seeds")) {
        const auto seed = seed_value.get<std::uint64_t>();
        for (const auto& treatment_value : protocol.at("treatments")) {
            const auto treatment = treatment_value.get<std::string>();
            const auto context = load_int5_integration_context(
                protocol, seed, treatment);
            for (std::size_t pass = 0;
                 pass != protocol.at("warmup_passes").get<std::size_t>(); ++pass) {
                static_cast<void>(int5_integration_batch(context, input, hamming,
                                                         protocol));
            }
            for (std::size_t pass = 0;
                 pass != protocol.at("measured_passes").get<std::size_t>(); ++pass) {
                const auto values = int5_integration_batch(context, input, hamming,
                                                           protocol);
                for (std::size_t index = 0; index != values.size(); ++index) {
                    const auto& request = protocol.at("requests").at(index);
                    samples.push_back(end_to_end_json(values[index], seed, treatment,
                        request.at("request").get<std::size_t>(),
                        request.at("native_query").get<std::size_t>(), pass));
                }
            }
        }
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int5_physical_integration_warm_samples"},
        {"protocol_sha256", agent_memory::sha256_file_hex(protocol_path)},
        {"hamming_backend", agent_memory::hamming_distance_backend_name(
            hamming.backend())}, {"samples", samples}});
}

void int5_integration_cold(const std::filesystem::path& protocol_path,
                           std::uint64_t seed, const std::string& treatment,
                           std::size_t request,
                           const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    validate_int5_integration_protocol(protocol);
    const auto [local_request, native_query] = end_to_end_request(protocol, request);
    const auto input = load_native_input(
        protocol.at("native_input_manifest").get<std::string>(),
        protocol.at("document_id_rank_file").get<std::string>());
    const auto context = load_int5_integration_context(protocol, seed, treatment);
    const agent_memory::HammingDistanceComputer hamming(binary_code_words);
    const auto value = int5_integration_query(context, input, hamming,
                                              local_request, native_query);
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int5_physical_integration_process_cold_sample"},
        {"definition", "fresh_process_first_query_os_page_cache_uncontrolled"},
        {"protocol_sha256", agent_memory::sha256_file_hex(protocol_path)},
        {"sample", end_to_end_json(value, seed, treatment, local_request,
                                    native_query, 0)}});
}

void validate_int5_stress_protocol(const nlohmann::json& protocol) {
    require(protocol.value("schema_version", 0) == 1 &&
            protocol.value("family", "") ==
                "neuroute_r4_int5_layout_stress_protocol",
            "R4 INT5 stress protocol identity differs");
    require(agent_memory::sha256_file_hex(
                protocol.at("parent_protocol").get<std::string>()) ==
                protocol.at("activation").at(
                    "physical_integration_protocol_sha256").get<std::string>(),
            "R4 INT5 stress parent protocol differs");
    require(protocol.at("treatments") == nlohmann::json::array({
                "homogeneous_int8", "int5_mixed"}) &&
            protocol.at("conditions") == nlohmann::json::array({
                "resident", "working_set_cap"}) &&
            protocol.at("workers") == nlohmann::json::array({1, 2, 4, 8, 16}) &&
            protocol.at("trace_repetitions").get<std::size_t>() >= 2 &&
            protocol.at("measured_batches").get<std::size_t>() >= 1,
            "R4 INT5 stress matrix differs");
}

bool apply_working_set_condition(const std::string& condition,
                                 std::size_t cap_bytes) {
    if (condition == "resident") return false;
    require(condition == "working_set_cap",
            "R4 INT5 stress condition differs");
#if defined(_WIN32)
    constexpr std::size_t minimum_bytes = 64U * 1024U * 1024U;
    require(cap_bytes >= minimum_bytes,
            "R4 INT5 stress working-set cap differs");
    const auto flags = QUOTA_LIMITS_HARDWS_MIN_ENABLE |
                       QUOTA_LIMITS_HARDWS_MAX_ENABLE;
    require(SetProcessWorkingSetSizeEx(GetCurrentProcess(), minimum_bytes,
            cap_bytes, flags) != 0,
            "R4 INT5 stress working-set cap failed");
    require(EmptyWorkingSet(GetCurrentProcess()) != 0,
            "R4 INT5 stress working-set trim failed");
    return true;
#else
    (void)cap_bytes;
    throw std::runtime_error(
        "R4 INT5 working-set-cap treatment is currently Windows-only");
#endif
}

std::vector<std::size_t> stress_trace(const nlohmann::json& parent_protocol,
                                      std::size_t repetitions) {
    std::vector<std::size_t> result;
    const auto count = parent_protocol.at("requests").size();
    result.reserve(count * repetitions);
    std::vector<std::size_t> local(count);
    std::iota(local.begin(), local.end(), 0);
    for (std::size_t repetition = 0; repetition != repetitions; ++repetition) {
        std::rotate(local.begin(),
                    local.begin() + (repetition * 17U) % count, local.end());
        result.insert(result.end(), local.begin(), local.end());
    }
    return result;
}

std::vector<EndToEndResult> int5_stress_batch(
        const Int5IntegrationContext& context, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        const nlohmann::json& parent_protocol,
        const std::vector<std::size_t>& trace, std::size_t workers) {
    std::vector<EndToEndResult> values(trace.size());
    std::atomic<std::size_t> following{0};
    std::vector<std::thread> threads;
    std::vector<std::exception_ptr> failures(workers);
    threads.reserve(workers);
    for (std::size_t worker = 0; worker != workers; ++worker) {
        threads.emplace_back([&, worker] {
            try {
                for (;;) {
                    const auto index = following.fetch_add(1);
                    if (index >= trace.size()) break;
                    const auto& request = parent_protocol.at("requests").at(
                        trace[index]);
                    values[index] = int5_integration_query(context, input, hamming,
                        request.at("request").get<std::size_t>(),
                        request.at("native_query").get<std::size_t>());
                }
            } catch (...) {
                failures[worker] = std::current_exception();
            }
        });
    }
    for (auto& thread : threads) thread.join();
    for (const auto& failure : failures)
        if (failure) std::rethrow_exception(failure);
    return values;
}

std::string stress_result_sha256(const std::vector<EndToEndResult>& values) {
    std::vector<std::uint8_t> bytes;
    for (const auto& value : values) {
        const auto append = [&](const std::string& text) {
            bytes.insert(bytes.end(), text.begin(), text.end());
        };
        append(value.score_sha256);
        append(u32_sequence_sha256(value.selected_addresses));
        append(u32_sequence_sha256(value.candidates));
        append(u32_sequence_sha256(value.hamming));
        append(u32_sequence_sha256(value.adc));
        append(u32_sequence_sha256(value.exact));
    }
    return agent_memory::sha256_bytes_hex(bytes);
}

void int5_stress(const std::filesystem::path& protocol_path,
                 std::uint64_t seed, const std::string& treatment,
                 const std::string& condition, std::size_t workers,
                 const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    validate_int5_stress_protocol(protocol);
    const auto parent_protocol = read_json(
        protocol.at("parent_protocol").get<std::string>());
    validate_int5_integration_protocol(parent_protocol);
    const auto input = load_native_input(
        parent_protocol.at("native_input_manifest").get<std::string>(),
        parent_protocol.at("document_id_rank_file").get<std::string>());
    const auto context = load_int5_integration_context(
        parent_protocol, seed, treatment);
    const agent_memory::HammingDistanceComputer hamming(binary_code_words);
    const auto trace = stress_trace(parent_protocol,
        protocol.at("trace_repetitions").get<std::size_t>());
    const auto cap_applied = apply_working_set_condition(condition,
        protocol.at("working_set_cap_bytes").get<std::size_t>());
    for (std::size_t pass = 0;
         pass != protocol.at("warmup_batches").get<std::size_t>(); ++pass) {
        static_cast<void>(int5_stress_batch(context, input, hamming,
            parent_protocol, trace, workers));
    }
    nlohmann::json samples = nlohmann::json::array();
    for (std::size_t pass = 0;
         pass != protocol.at("measured_batches").get<std::size_t>(); ++pass) {
        const auto before = process_state();
        const auto begin = Clock::now();
        const auto values = int5_stress_batch(context, input, hamming,
            parent_protocol, trace, workers);
        const auto wall_ms = milliseconds(begin, Clock::now());
        const auto after = process_state();
        std::vector<double> query_ms;
        query_ms.reserve(values.size());
        std::uint64_t logical_bytes = 0;
        for (const auto& value : values) {
            query_ms.push_back(value.timing.total);
            logical_bytes += value.logical_bytes;
        }
        samples.push_back({{"pass", pass}, {"query_count", values.size()},
            {"wall_ms", wall_ms},
            {"throughput_queries_per_second",
                1000.0 * static_cast<double>(values.size()) / wall_ms},
            {"per_query_total_ms", query_ms},
            {"logical_bytes_touched", logical_bytes},
            {"page_faults", after.faults - before.faults},
            {"working_set_bytes_before", before.rss},
            {"working_set_bytes_after", after.rss},
            {"result_sha256", stress_result_sha256(values)}});
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_int5_layout_stress_native_samples"},
        {"protocol_sha256", agent_memory::sha256_file_hex(protocol_path)},
        {"seed", seed}, {"treatment", treatment}, {"condition", condition},
        {"workers", workers}, {"working_set_cap_applied", cap_applied},
        {"trace_queries", trace.size()},
        {"hamming_backend", agent_memory::hamming_distance_backend_name(
            hamming.backend())}, {"samples", samples}});
}

void validate_int5_kernel_protocol(const nlohmann::json& protocol) {
    require(protocol.value("schema_version", 0) == 1 &&
            protocol.value("family", "") ==
                "neuroute_r4_int5_kernel_frontier_protocol",
            "R4 INT5 kernel protocol identity differs");
    require(agent_memory::sha256_file_hex(
                protocol.at("parent_protocol").get<std::string>()) ==
                protocol.at("activation").at(
                    "physical_integration_protocol_sha256").get<std::string>(),
            "R4 INT5 kernel parent protocol differs");
    require(protocol.at("kernels") == nlohmann::json::array({
                "homogeneous_int8", "int5_direct_square_legacy",
                "int5_direct_square", "int5_fused_sse",
                "int5_fused_avx2", "int5_fused_avx2_q8",
                "int5_direct_q8_integer", "int5_direct_q16_integer"}) &&
            protocol.at("conditions") == nlohmann::json::array({
                "resident", "working_set_cap"}) &&
            protocol.at("workers") == nlohmann::json::array({1, 8, 16}) &&
            protocol.at("bitsliced_layouts").size() == 3 &&
            protocol.at("avx2_layouts").size() == 3 &&
            protocol.at("trace_repetitions").get<std::size_t>() >= 2 &&
            protocol.at("measured_batches").get<std::size_t>() >= 1 &&
            protocol.at("memory_crossover").at("caps_bytes").size() == 8 &&
            protocol.at("memory_crossover").at("include_resident") == true &&
            protocol.at("memory_crossover").at("workers") == 8 &&
            protocol.at("memory_crossover").at(
                "trace_repetitions").get<std::size_t>() >= 2 &&
            protocol.at("memory_crossover").at(
                "measured_batches").get<std::size_t>() >= 1,
            "R4 INT5 kernel matrix differs");
}

Int5IntegrationContext load_int5_kernel_context(
        const nlohmann::json& protocol, const nlohmann::json& parent,
        std::uint64_t seed, const std::string& kernel) {
    const bool baseline = kernel == "homogeneous_int8";
    auto context = load_int5_integration_context(parent, seed,
        baseline ? "homogeneous_int8" : "int5_mixed");
    const bool bitsliced = kernel == "int5_bitsliced_fp32_lut" ||
                           kernel == "int5_bitsliced_q8_lut";
    const bool avx2 = kernel == "int5_fused_avx2" ||
                      kernel == "int5_fused_avx2_q8";
    if (bitsliced || avx2) {
        const auto& layouts = protocol.at(bitsliced ? "bitsliced_layouts" :
                                                     "avx2_layouts");
        const auto found = std::find_if(layouts.begin(), layouts.end(),
            [&](const nlohmann::json& row) {
                return row.at("seed").get<std::uint64_t>() == seed;
            });
        require(found != layouts.end(), "R4 INT5 alternate kernel seed differs");
        const auto path = std::filesystem::path(
            found->at("path").get<std::string>());
        require(path.is_absolute() &&
                agent_memory::sha256_file_hex(path) ==
                    found->at("sha256").get<std::string>() &&
                std::filesystem::file_size(path) ==
                    found->at("bytes").get<std::uint64_t>(),
                "R4 INT5 alternate kernel layout differs");
        context.representatives = std::make_unique<MappedFile>(path);
    }
    context.routing_spans.resize(152);
    context.routing_representatives.resize(152);
    for (std::size_t request = 0; request != 152; ++request) {
        auto& spans = context.routing_spans[request];
        spans.reserve(addresses_per_query);
        std::size_t representatives = 0;
        for (std::size_t local = 0; local != addresses_per_query; ++local) {
            const auto row = context.seed.shortlists[
                request * addresses_per_query + local];
            const auto count = static_cast<std::size_t>(
                context.seed.representative_counts[row]);
            const auto byte_offset = baseline
                ? static_cast<std::size_t>(context.seed.address_offsets[row]) *
                    388U
                : static_cast<std::size_t>(
                    context.mixed_address_byte_offsets[row]);
            spans.push_back({local, byte_offset,
                count * context.record_bytes, 0});
            representatives += count;
        }
        std::sort(spans.begin(), spans.end(), [](const auto& left,
                                                 const auto& right) {
            return left.byte_offset < right.byte_offset;
        });
        context.routing_representatives[request] = representatives;
    }
    return context;
}

EndToEndResult int5_kernel_query(
        const Int5IntegrationContext& context, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        const std::string& kernel, std::size_t request,
        std::size_t native_query) {
    const auto total_begin = Clock::now();
    return finish_end_to_end_query(context.seed, input, hamming,
        int5_kernel_route(context, request, kernel), request, native_query,
        total_begin);
}

std::vector<EndToEndResult> int5_kernel_batch(
        const Int5IntegrationContext& context, const NativeCascadeInput& input,
        const agent_memory::HammingDistanceComputer& hamming,
        const nlohmann::json& parent_protocol,
        const std::vector<std::size_t>& trace, std::size_t workers,
        const std::string& kernel) {
    std::vector<EndToEndResult> values(trace.size());
    std::atomic<std::size_t> following{0};
    std::vector<std::thread> threads;
    std::vector<std::exception_ptr> failures(workers);
    threads.reserve(workers);
    for (std::size_t worker = 0; worker != workers; ++worker) {
        threads.emplace_back([&, worker] {
            try {
                for (;;) {
                    const auto index = following.fetch_add(1);
                    if (index >= trace.size()) break;
                    const auto& request = parent_protocol.at("requests").at(
                        trace[index]);
                    values[index] = int5_kernel_query(context, input, hamming,
                        kernel, request.at("request").get<std::size_t>(),
                        request.at("native_query").get<std::size_t>());
                }
            } catch (...) {
                failures[worker] = std::current_exception();
            }
        });
    }
    for (auto& thread : threads) thread.join();
    for (const auto& failure : failures)
        if (failure) std::rethrow_exception(failure);
    return values;
}

void int5_kernel_measure(const std::filesystem::path& protocol_path,
                         std::uint64_t seed, const std::string& kernel,
                         const std::string& condition, std::size_t workers,
                         std::size_t trace_repetitions,
                         std::size_t warmup_batches,
                         std::size_t measured_batches,
                         std::optional<std::size_t> working_set_cap,
                         const std::string& family,
                         const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    validate_int5_kernel_protocol(protocol);
    const auto parent = read_json(
        protocol.at("parent_protocol").get<std::string>());
    validate_int5_integration_protocol(parent);
    require(std::find(protocol.at("kernels").begin(),
                      protocol.at("kernels").end(), kernel) !=
                protocol.at("kernels").end() &&
            std::find(protocol.at("conditions").begin(),
                      protocol.at("conditions").end(), condition) !=
                protocol.at("conditions").end(),
            "R4 INT5 kernel invocation differs");
    const auto input = load_native_input(
        parent.at("native_input_manifest").get<std::string>(),
        parent.at("document_id_rank_file").get<std::string>());
    const auto context = load_int5_kernel_context(protocol, parent, seed,
                                                   kernel);
    const agent_memory::HammingDistanceComputer hamming(binary_code_words);
    const auto trace = stress_trace(parent, trace_repetitions);
    require((condition == "resident" && !working_set_cap.has_value()) ||
            (condition == "working_set_cap" && working_set_cap.has_value()),
            "R4 INT5 kernel working-set condition differs");
    const auto cap_applied = apply_working_set_condition(condition,
        working_set_cap.value_or(0));
    for (std::size_t pass = 0; pass != warmup_batches; ++pass) {
        static_cast<void>(int5_kernel_batch(context, input, hamming, parent,
            trace, workers, kernel));
    }
    nlohmann::json samples = nlohmann::json::array();
    for (std::size_t pass = 0; pass != measured_batches; ++pass) {
        const auto before = process_state();
        const auto begin = Clock::now();
        const auto values = int5_kernel_batch(context, input, hamming, parent,
            trace, workers, kernel);
        const auto wall_ms = milliseconds(begin, Clock::now());
        const auto after = process_state();
        std::vector<double> query_ms, representative_ms;
        std::uint64_t logical_bytes = 0;
        nlohmann::json queries = nlohmann::json::array();
        for (std::size_t index = 0; index != values.size(); ++index) {
            const auto& value = values[index];
            query_ms.push_back(value.timing.total);
            representative_ms.push_back(value.timing.representative_dot);
            logical_bytes += value.logical_bytes;
            const auto& request = parent.at("requests").at(trace[index]);
            queries.push_back(end_to_end_json(value, seed, kernel,
                request.at("request").get<std::size_t>(),
                request.at("native_query").get<std::size_t>(), pass));
        }
        samples.push_back({{"pass", pass}, {"query_count", values.size()},
            {"wall_ms", wall_ms},
            {"throughput_queries_per_second",
                1000.0 * static_cast<double>(values.size()) / wall_ms},
            {"per_query_total_ms", query_ms},
            {"per_query_representative_dot_ms", representative_ms},
            {"logical_bytes_touched", logical_bytes},
            {"page_faults", after.faults - before.faults},
            {"working_set_bytes_before", before.rss},
            {"working_set_bytes_after", after.rss},
            {"result_sha256", stress_result_sha256(values)},
            {"queries", queries}});
    }
    write_json(output_path, {{"schema_version", 1},
        {"family", family},
        {"protocol_sha256", agent_memory::sha256_file_hex(protocol_path)},
        {"seed", seed}, {"kernel", kernel}, {"condition", condition},
        {"workers", workers}, {"working_set_cap_applied", cap_applied},
        {"working_set_cap_bytes", working_set_cap.has_value()
            ? nlohmann::json(*working_set_cap) : nlohmann::json(nullptr)},
        {"trace_queries", trace.size()},
        {"quantized_query_sensitivity", kernel ==
            "int5_bitsliced_q8_lut" || kernel ==
            "int5_fused_avx2_q8" || kernel ==
            "int5_direct_q8_integer" || kernel ==
            "int5_direct_q16_integer"},
        {"hamming_backend", agent_memory::hamming_distance_backend_name(
            hamming.backend())}, {"samples", samples}});
}

void int5_kernel_frontier(const std::filesystem::path& protocol_path,
                          std::uint64_t seed, const std::string& kernel,
                          const std::string& condition, std::size_t workers,
                          const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    const auto cap = condition == "working_set_cap"
        ? std::optional<std::size_t>(protocol.at(
            "working_set_cap_bytes").get<std::size_t>())
        : std::nullopt;
    int5_kernel_measure(protocol_path, seed, kernel, condition, workers,
        protocol.at("trace_repetitions").get<std::size_t>(),
        protocol.at("warmup_batches").get<std::size_t>(),
        protocol.at("measured_batches").get<std::size_t>(), cap,
        "neuroute_r4_int5_kernel_frontier_native_samples", output_path);
}

void int5_kernel_crossover(const std::filesystem::path& protocol_path,
                           std::uint64_t seed, const std::string& kernel,
                           const std::string& cap_text,
                           const std::filesystem::path& output_path) {
    const auto protocol = read_json(protocol_path);
    const auto& crossover = protocol.at("memory_crossover");
    const bool resident = cap_text == "resident";
    const auto cap = resident ? std::nullopt :
        std::optional<std::size_t>(std::stoull(cap_text));
    if (cap.has_value()) {
        const auto& caps = crossover.at("caps_bytes");
        require(std::find(caps.begin(), caps.end(), *cap) != caps.end(),
                "R4 INT5 crossover cap differs");
    } else {
        require(crossover.at("include_resident").get<bool>(),
                "R4 INT5 crossover resident condition differs");
    }
    int5_kernel_measure(protocol_path, seed, kernel,
        resident ? "resident" : "working_set_cap",
        crossover.at("workers").get<std::size_t>(),
        crossover.at("trace_repetitions").get<std::size_t>(),
        crossover.at("warmup_batches").get<std::size_t>(),
        crossover.at("measured_batches").get<std::size_t>(), cap,
        "neuroute_r4_int5_kernel_crossover_native_samples", output_path);
}

void self_test() {
    require(std::tanh(0.0F) == 0.0F, "R4 layout math self-test differs");
    std::array<std::uint8_t, dimensions> codes{};
    std::array<float, dimensions> query{};
    for (std::size_t index = 0; index != dimensions; ++index) {
        codes[index] = static_cast<std::uint8_t>((index * 17U) % 255U);
        query[index] = static_cast<float>(static_cast<int>(index % 19U) - 9) / 19.0F;
    }
    require(std::abs(int8_dot_scalar(codes.data(), 0.007F, query.data()) -
                     int8_dot_avx2(codes.data(), 0.007F, query.data())) < 1.0e-4F,
            "R4 fused INT8 kernel differs");
    require(int8_dot_scalar(codes.data(), 0.007F, query.data()) ==
            int8_dot_avx2_ordered(codes.data(), 0.007F, query.data()),
            "R4 ordered AVX2 INT8 kernel differs");
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    std::array<std::uint32_t, 128> input{}, decoded{};
    for (std::size_t index = 0; index != input.size(); ++index)
        input[index] = static_cast<std::uint32_t>(index % 73U);
    std::ostringstream packed;
    simdcomp_pack_block(input.data(), 7, packed);
    const auto bytes = packed.str();
    require(bytes.size() == 112, "R4 adaptive SIMDComp size differs");
    simdcomp_unpack_block(reinterpret_cast<const std::uint8_t*>(bytes.data()),
                          7, decoded.data());
    require(input == decoded, "R4 adaptive SIMDComp round trip differs");
    std::array<std::uint8_t, 244> int5_record{}, bitsliced_record{},
        avx2_record{};
    std::ostringstream int5_packed;
    for (std::size_t block = 0; block != 3; ++block) {
        for (std::size_t lane = 0; lane != 128; ++lane)
            input[lane] = static_cast<std::uint32_t>(
                (block * 128 + lane) % 31U);
        simdcomp_pack_block(input.data(), 5, int5_packed);
    }
    const auto int5_bytes = int5_packed.str();
    require(int5_bytes.size() == 240,
            "R4 nonlinear INT5 packed self-test differs");
    std::memcpy(int5_record.data(), int5_bytes.data(), int5_bytes.size());
    const float amplitude = 0.73F;
    std::memcpy(int5_record.data() + 240, &amplitude, sizeof(amplitude));
    const auto power_table = int5_power_half_decode_table();
    const auto legacy_score = int5_power_half_dot_avx2_legacy(
        int5_record.data(), power_table, query.data());
    const auto direct_score = int5_power_half_dot_avx2(
        int5_record.data(), query.data());
    const auto fused_sse_score = int5_power_half_fused_sse_dot(
        int5_record.data(), query.data());
    const auto shuffle_score = int5_power_half_dot_pshufb(
        int5_record.data(), power_table, query.data());
    int5_power_half_bitslice_record(int5_record.data(),
                                    bitsliced_record.data());
    int5_power_half_avx2_record(int5_record.data(), avx2_record.data());
    const auto fused_avx2_score = int5_power_half_fused_avx2_dot(
        avx2_record.data(), query.data());
    const auto query_luts = power_half_query_luts(
        query.data(), true, true, true, true);
    const auto bitsliced_score = int5_power_half_bitsliced_dot_fp32(
        bitsliced_record.data(), query_luts);
    const auto q8_score = int5_power_half_bitsliced_dot_q8(
        bitsliced_record.data(), query_luts);
    const auto direct_q8_score = int5_power_half_integer_dot(
        int5_record.data(), query_luts.direct_int8.data(),
        query_luts.int8_scale);
    const auto fused_avx2_q8_score = int5_power_half_fused_avx2_integer_dot(
        avx2_record.data(), query_luts.direct_int8.data(),
        query_luts.int8_scale);
    const auto direct_q16_score = int5_power_half_integer_dot(
        int5_record.data(), query_luts.direct_int16.data(),
        query_luts.int16_scale);
    require(std::abs(legacy_score - direct_score) < 1.0e-4F &&
            std::abs(legacy_score - fused_sse_score) < 1.0e-4F &&
            std::abs(legacy_score - fused_avx2_score) < 1.0e-4F &&
            std::abs(legacy_score - shuffle_score) < 1.0e-5F &&
            std::abs(direct_score - bitsliced_score) < 1.0e-4F &&
            std::abs(direct_score - q8_score) < 0.02F &&
            std::abs(q8_score - direct_q8_score) < 1.0e-5F &&
            std::abs(direct_q8_score - fused_avx2_q8_score) < 1.0e-5F &&
            std::abs(direct_score - direct_q16_score) < 1.0e-4F,
            "R4 nonlinear INT5 kernel self-test differs");
#endif
    std::vector<std::uint8_t> vbyte;
    for (const auto value : {0U, 1U, 127U, 128U, 254U, 16384U})
        append_vbyte(value, vbyte);
    const auto* current = vbyte.data();
    const auto* end = current + vbyte.size();
    for (const auto value : {0U, 1U, 127U, 128U, 254U, 16384U})
        require(decode_vbyte(current, end) == value,
                "R4 VByte self-test differs");
    require(current == end, "R4 VByte self-test has trailing bytes");
#if AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD
    const std::array<std::uint8_t, 16> zstd_input = {
        1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4};
    std::array<std::uint8_t, 128> zstd_compressed{};
    std::array<std::uint8_t, 16> zstd_decoded{};
    const auto zstd_size = ZSTD_compress(zstd_compressed.data(),
        zstd_compressed.size(), zstd_input.data(), zstd_input.size(), 3);
    require(ZSTD_isError(zstd_size) == 0, "R4 Zstd self-test pack failed");
    const auto zstd_decoded_size = ZSTD_decompress(zstd_decoded.data(),
        zstd_decoded.size(), zstd_compressed.data(), zstd_size);
    require(zstd_decoded_size == zstd_input.size() && zstd_decoded == zstd_input,
            "R4 Zstd self-test round trip differs");
#endif
    const auto int5_table = int5_power_half_decode_table();
    require(std::abs(int5_table[0] + 1.0F) < 1.0e-6F &&
            int5_table[15] == 0.0F &&
            std::abs(int5_table[30] - 1.0F) < 1.0e-6F,
            "R4 nonlinear INT5 table differs");
    std::cout << "NeuRoute R4 layout native self-test passed\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") self_test();
        else if (argc == 4 && std::string(argv[1]) == "--warm") warm(argv[2], argv[3]);
        else if (argc == 4 && std::string(argv[1]) == "--kernel-warm")
            kernel_warm(argv[2], argv[3]);
        else if (argc == 4 && std::string(argv[1]) == "--scorer-warm")
            scorer_warm(argv[2], argv[3]);
        else if (argc == 4 && std::string(argv[1]) == "--access-warm")
            access_warm(argv[2], argv[3]);
        else if (argc == 7 && std::string(argv[1]) == "--access-cold")
            access_cold(argv[2], std::stoull(argv[3]), argv[4],
                        std::stoull(argv[5]), argv[6]);
        else if (argc == 11 && std::string(argv[1]) == "--compress-pack")
            compress_int8_store(argv[2], argv[3], std::stoull(argv[4]), argv[5],
                                argv[6], argv[7], argv[8], argv[9], argv[10]);
        else if (argc == 5 && std::string(argv[1]) == "--compression-warm")
            compression_warm(argv[2], argv[3], argv[4]);
        else if (argc == 8 && std::string(argv[1]) == "--compression-cold")
            compression_cold(argv[2], argv[3], std::stoull(argv[4]), argv[5],
                             std::stoull(argv[6]), argv[7]);
        else if (argc == 16 && std::string(argv[1]) == "--lossless-block-pack")
            pack_lossless_int8_blocks(argv[2], argv[3], std::stoull(argv[4]),
                std::stoi(argv[5]), std::stoull(argv[6]), std::stoull(argv[7]),
                argv[8], argv[9], argv[10], argv[11], argv[12], argv[13],
                argv[14], argv[15]);
        else if (argc == 5 && std::string(argv[1]) == "--lossless-block-warm")
            lossless_block_warm(argv[2], argv[3], argv[4]);
        else if (argc == 8 && std::string(argv[1]) == "--lossless-block-cold")
            lossless_block_cold(argv[2], argv[3], std::stoull(argv[4]), argv[5],
                                std::stoull(argv[6]), argv[7]);
        else if (argc == 7 && std::string(argv[1]) == "--cold")
            cold(argv[2], std::stoull(argv[3]), argv[4], std::stoull(argv[5]), argv[6]);
        else if (argc == 4 && std::string(argv[1]) == "--end-to-end-warm")
            end_to_end_warm(argv[2], argv[3]);
        else if (argc == 7 && std::string(argv[1]) == "--end-to-end-cold")
            end_to_end_cold(argv[2], std::stoull(argv[3]), argv[4],
                            std::stoull(argv[5]), argv[6]);
        else if (argc == 4 && std::string(argv[1]) == "--int5-integration-warm")
            int5_integration_warm(argv[2], argv[3]);
        else if (argc == 7 && std::string(argv[1]) == "--int5-integration-cold")
            int5_integration_cold(argv[2], std::stoull(argv[3]), argv[4],
                                  std::stoull(argv[5]), argv[6]);
        else if (argc == 8 && std::string(argv[1]) == "--int5-stress")
            int5_stress(argv[2], std::stoull(argv[3]), argv[4], argv[5],
                        std::stoull(argv[6]), argv[7]);
        else if (argc == 6 && std::string(argv[1]) ==
                  "--int5-bitslice-materialize")
            materialize_int5_bitsliced_mixed(argv[2], std::stoull(argv[3]),
                                              argv[4], argv[5]);
        else if (argc == 6 && std::string(argv[1]) ==
                  "--int5-avx2-materialize")
            materialize_int5_avx2_mixed(argv[2], std::stoull(argv[3]),
                                         argv[4], argv[5]);
        else if (argc == 8 && std::string(argv[1]) ==
                 "--int5-kernel-frontier")
            int5_kernel_frontier(argv[2], std::stoull(argv[3]), argv[4],
                                 argv[5], std::stoull(argv[6]), argv[7]);
        else if (argc == 7 && std::string(argv[1]) ==
                 "--int5-kernel-crossover")
            int5_kernel_crossover(argv[2], std::stoull(argv[3]), argv[4],
                                  argv[5], argv[6]);
        else throw std::runtime_error("usage: --self-test | --compress-pack ... | --lossless-block-pack RAW COUNTS ROWS LEVEL DICT_BYTES TRAIN_BLOCKS ZSTD ZSTD_OFFSETS DICT ZSTD_DICT ZSTD_DICT_OFFSETS VBYTE VBYTE_OFFSETS RECEIPT | --lossless-block-warm MANIFEST BLOCK_MANIFEST OUTPUT | --lossless-block-cold MANIFEST BLOCK_MANIFEST SEED TREATMENT REQUEST OUTPUT | other R4 layout modes");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-layout-benchmark: " << error.what() << '\n';
        return 1;
    }
}
