#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#if AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2
#include <immintrin.h>
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
    const auto found = std::find_if(rows.begin(), rows.end(), [&](const auto& row) {
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
    value.queries = read_values<float>(path("query_vectors"));
    value.shortlists = read_values<std::uint32_t>(path("shortlist_rows"));
    value.features = read_values<float>(path("scalar_features"));
    require(value.queries.size() == 152 * dimensions &&
            value.shortlists.size() == 152 * addresses_per_query &&
            value.features.size() == 152 * addresses_per_query * scalar_features,
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
        [&](const auto& row) { return row.at("seed").get<std::uint64_t>() == wanted_seed; });
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

void cold(const std::filesystem::path& manifest_path, std::uint64_t wanted_seed,
          const std::string& layout, std::size_t request,
          const std::filesystem::path& output_path) {
    const auto manifest = read_json(manifest_path);
    const auto found = std::find_if(manifest.at("seeds").begin(), manifest.at("seeds").end(),
        [&](const auto& row) { return row.at("seed").get<std::uint64_t>() == wanted_seed; });
    require(found != manifest.at("seeds").end(), "R4 layout cold seed differs");
    const auto seed = load_seed(manifest_path, *found);
    require(request < 152, "R4 layout cold request differs");
    write_json(output_path, {{"schema_version", 1},
        {"family", "neuroute_r4_layout_process_cold_sample"},
        {"definition", "fresh_process_first_request_os_page_cache_uncontrolled"},
        {"sample", sample_json(measure(seed, layout, request), wanted_seed,
                                layout, request, 0)}});
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
        else if (argc == 7 && std::string(argv[1]) == "--cold")
            cold(argv[2], std::stoull(argv[3]), argv[4], std::stoull(argv[5]), argv[6]);
        else throw std::runtime_error("usage: --self-test | --warm MANIFEST OUTPUT | --kernel-warm MANIFEST OUTPUT | --scorer-warm MANIFEST OUTPUT | --access-warm MANIFEST OUTPUT | --access-cold MANIFEST SEED TREATMENT REQUEST OUTPUT | --cold MANIFEST SEED LAYOUT REQUEST OUTPUT");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-layout-benchmark: " << error.what() << '\n';
        return 1;
    }
}
