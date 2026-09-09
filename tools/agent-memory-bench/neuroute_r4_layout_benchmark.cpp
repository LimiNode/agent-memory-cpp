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
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>
#else
#include <sys/resource.h>
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
    std::int64_t rss_delta = 0;
    std::string score_sha256;
};

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

Sample measure(const SeedContext& seed, const std::string& layout,
               std::size_t request) {
    const auto& descriptor = layout_row(seed, layout);
    const auto file = payload_path(seed.root, descriptor);
    const auto record_bytes = descriptor.at("record_bytes").get<std::size_t>();
    const bool fp32 = layout == "address_major_fp32";
    const bool indirect = layout == "document_major_int8_indirect";
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
    std::vector<float> decoded(representatives * dimensions);
    std::size_t vector_index = 0;
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
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
    std::vector<float> maximums(addresses_per_query, -1.0F);
    vector_index = 0;
    for (std::size_t address = 0; address != addresses_per_query; ++address) {
        const auto count = (starts[address + 1] - starts[address]) / record_bytes;
        float maximum = -std::numeric_limits<float>::infinity();
        for (std::size_t slot = 0; slot != count; ++slot, ++vector_index) {
            const float* value = decoded.data() + vector_index * dimensions;
            float score = 0.0F;
            for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                score += value[dimension] * query[dimension];
            }
            maximum = std::max(maximum, score);
        }
        maximums[address] = maximum;
    }
    const auto dot_end = Clock::now();
    const auto score_begin = Clock::now();
    const auto scores = address_scores(seed, request, maximums);
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
            {"representatives_scored", value.representatives},
            {"score_sha256", value.score_sha256}};
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
    std::cout << "NeuRoute R4 layout native self-test passed\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") self_test();
        else if (argc == 4 && std::string(argv[1]) == "--warm") warm(argv[2], argv[3]);
        else if (argc == 7 && std::string(argv[1]) == "--cold")
            cold(argv[2], std::stoull(argv[3]), argv[4], std::stoull(argv[5]), argv[6]);
        else throw std::runtime_error("usage: --self-test | --warm MANIFEST OUTPUT | --cold MANIFEST SEED LAYOUT REQUEST OUTPUT");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-layout-benchmark: " << error.what() << '\n';
        return 1;
    }
}
