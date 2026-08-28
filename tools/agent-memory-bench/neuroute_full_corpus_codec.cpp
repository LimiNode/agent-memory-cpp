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
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
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

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
#include <simdbitpacking.h>
#endif

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t dimensions = 384;
constexpr std::size_t block_values = 128;
constexpr std::size_t pool_size = 64;
constexpr std::size_t result_size = 10;

double milliseconds(Clock::time_point begin, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - begin).count();
}

void require(bool value, const std::string& message) {
    if (!value) throw std::runtime_error(message);
}

nlohmann::json read_json(const std::filesystem::path& path) {
    std::ifstream stream(path);
    require(static_cast<bool>(stream), "full-corpus codec JSON is absent");
    nlohmann::json value;
    stream >> value;
    return value;
}

void write_json(const std::filesystem::path& path, const nlohmann::json& value) {
    std::filesystem::create_directories(path.parent_path());
    std::ofstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "cannot write full-corpus codec JSON");
    stream << value.dump(2) << '\n';
}

std::size_t element_count(const nlohmann::json& payload) {
    std::size_t result = 1;
    for (const auto& value : payload.at("shape")) result *= value.get<std::size_t>();
    return result;
}

template <class T>
std::vector<T> read_payload(const nlohmann::json& payload, bool verify = true) {
    const auto path = std::filesystem::path(payload.at("file").get<std::string>());
    if (verify) {
        require(agent_memory::sha256_file_hex(path) == payload.at("sha256"),
                "full-corpus codec payload hash differs");
    }
    std::ifstream stream(path, std::ios::binary);
    std::vector<T> values(element_count(payload));
    stream.read(reinterpret_cast<char*>(values.data()),
                static_cast<std::streamsize>(values.size() * sizeof(T)));
    require(static_cast<bool>(stream) && stream.peek() == std::ifstream::traits_type::eof(),
            "full-corpus codec payload size differs");
    return values;
}

void append_u32(std::vector<std::uint8_t>& bytes, std::uint32_t value) {
    for (int shift = 0; shift != 32; shift += 8) {
        bytes.push_back(static_cast<std::uint8_t>((value >> shift) & 255U));
    }
}

std::string sequence_sha256(const std::vector<std::uint32_t>& values) {
    std::vector<std::uint8_t> bytes;
    bytes.reserve(values.size() * 4);
    for (const auto value : values) append_u32(bytes, value);
    return agent_memory::sha256_bytes_hex(bytes);
}

std::vector<std::uint8_t> scalar_pack(const std::uint32_t* values, unsigned bits) {
    std::vector<std::uint8_t> output(dimensions * bits / 8, 0);
    for (std::size_t index = 0; index != dimensions; ++index) {
        const auto bit = index * bits;
        const auto byte = bit / 8;
        const auto offset = static_cast<unsigned>(bit % 8);
        const auto value = static_cast<std::uint16_t>(values[index]);
        output[byte] |= static_cast<std::uint8_t>(value << offset);
        if (offset + bits > 8) {
            output[byte + 1] |= static_cast<std::uint8_t>(value >> (8 - offset));
        }
    }
    return output;
}

void scalar_unpack(const std::uint8_t* bytes, unsigned bits, std::uint32_t* output) {
    const auto mask = (1U << bits) - 1U;
    for (std::size_t index = 0; index != dimensions; ++index) {
        const auto bit = index * bits;
        const auto byte = bit / 8;
        const auto offset = static_cast<unsigned>(bit % 8);
        std::uint16_t value = static_cast<std::uint16_t>(bytes[byte] >> offset);
        if (offset + bits > 8) {
            value |= static_cast<std::uint16_t>(bytes[byte + 1]) << (8 - offset);
        }
        output[index] = value & mask;
    }
}

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
std::vector<std::uint8_t> simdcomp_pack(const std::uint32_t* values, unsigned bits) {
    std::vector<__m128i> packed(3 * bits);
    for (std::size_t block = 0; block != 3; ++block) {
        simdpackwithoutmask(values + block * block_values,
                            packed.data() + block * bits, bits);
    }
    const auto* begin = reinterpret_cast<const std::uint8_t*>(packed.data());
    return {begin, begin + dimensions * bits / 8};
}

void simdcomp_unpack(const std::uint8_t* bytes, unsigned bits, std::uint32_t* output) {
    std::vector<__m128i> packed(3 * bits);
    std::memcpy(packed.data(), bytes, dimensions * bits / 8);
    for (std::size_t block = 0; block != 3; ++block) {
        simdunpack(packed.data() + block * bits,
                   output + block * block_values, bits);
    }
}
#endif

struct Representation {
    std::string id;
    std::string layout;
    unsigned bits = 0;
    std::size_t record_bytes = 0;
    std::filesystem::path file;
    std::string sha256;
};

struct Faults {
    std::uint64_t minor = 0;
    std::uint64_t major = 0;
    std::uint64_t total = 0;
};

Faults faults() {
#if defined(_WIN32)
    PROCESS_MEMORY_COUNTERS counters{};
    counters.cb = sizeof(counters);
    require(GetProcessMemoryInfo(GetCurrentProcess(), &counters, sizeof(counters)) != 0,
            "full-corpus codec page-fault snapshot failed");
    return {0, 0, static_cast<std::uint64_t>(counters.PageFaultCount)};
#else
    rusage usage{};
    require(getrusage(RUSAGE_SELF, &usage) == 0,
            "full-corpus codec page-fault snapshot failed");
    return {static_cast<std::uint64_t>(usage.ru_minflt),
            static_cast<std::uint64_t>(usage.ru_majflt), 0};
#endif
}

nlohmann::json native_environment() {
#if defined(_MSC_VER)
    const std::string compiler = "msvc-" + std::to_string(_MSC_VER);
#elif defined(__clang__)
    const std::string compiler = std::string("clang-") + __clang_version__;
#elif defined(__GNUC__)
    const std::string compiler = std::string("gcc-") + __VERSION__;
#else
    const std::string compiler = "unknown";
#endif
#if defined(_WIN32)
    const std::string operating_system = "windows";
#elif defined(__APPLE__)
    const std::string operating_system = "macos";
#else
    const std::string operating_system = "linux";
#endif
    return {{"compiler", compiler}, {"operating_system", operating_system},
            {"hardware_threads", std::thread::hardware_concurrency()},
            {"simdcomp_available", AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP != 0}};
}

nlohmann::json evaluator_build_environment() {
    const std::string build_configuration = AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION;
    return {
        {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256},
        {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
        {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
        {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
        {"cxx_extensions", AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS != 0},
        {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
        {"build_configuration", build_configuration.empty() ? "unspecified" : build_configuration},
        {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
        {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
        {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS},
        {"base_cxx_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
        {"active_configuration_flags_sha256",
         AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
    };
}

nlohmann::json fault_delta(const Faults& begin, const Faults& end) {
    return {{"minor", end.minor - begin.minor}, {"major", end.major - begin.major},
            {"total", end.total - begin.total}};
}

std::vector<Representation> representations(const nlohmann::json& storage,
                                             const std::filesystem::path& root) {
    std::vector<Representation> result;
    for (const auto& row : storage.at("representations")) {
        result.push_back({row.at("id"), row.at("layout"), row.at("bits"),
                          row.at("record_bytes"), root / row.at("file").get<std::string>(),
                          row.at("sha256")});
    }
    return result;
}

const Representation& find_representation(const std::vector<Representation>& values,
                                          const std::string& id) {
    const auto found = std::find_if(values.begin(), values.end(),
                                    [&](const auto& row) { return row.id == id; });
    require(found != values.end(), "full-corpus codec representation differs");
    return *found;
}

void decode(const Representation& representation, const std::uint8_t* bytes,
            std::uint32_t* output) {
    if (representation.layout == "scalar_bp128") {
        scalar_unpack(bytes, representation.bits, output);
        return;
    }
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    if (representation.layout == "simdcomp_bp128") {
        simdcomp_unpack(bytes, representation.bits, output);
        return;
    }
#endif
    throw std::runtime_error("full-corpus codec layout is unavailable");
}

void build_storage(const std::filesystem::path& input_path,
                   const std::filesystem::path& output_root,
                   const std::filesystem::path& output_manifest) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    throw std::runtime_error("full-corpus codec measurement requires SIMDComp");
#else
    const auto total_begin = Clock::now();
    const auto input = read_json(input_path);
    require(input.at("family") == "neuroute_full_corpus_codec_native_input",
            "full-corpus codec input family differs");
    const auto documents = input.at("documents");
    require(documents.at("shape") == nlohmann::json::array({1000000, 384}) &&
            documents.at("dtype") == "<f4", "full-corpus codec document shape differs");
    const auto source = std::filesystem::path(documents.at("file").get<std::string>());
    const auto validation_begin = Clock::now();
    require(agent_memory::sha256_file_hex(source) == documents.at("sha256"),
            "full-corpus codec document bytes differ");
    const auto validation_end = Clock::now();
    std::ifstream stream(source, std::ios::binary);
    require(static_cast<bool>(stream), "full-corpus codec document source is absent");
    std::filesystem::create_directories(output_root);

    struct Writer {
        std::string id;
        std::string layout;
        unsigned bits;
        std::size_t record_bytes;
        std::filesystem::path path;
        std::ofstream stream;
    };
    std::vector<Writer> writers;
    for (const auto& row : input.at("representations")) {
        const auto filename = row.at("id").get<std::string>() + ".records";
        writers.push_back({row.at("id"), row.at("layout"), row.at("bits"),
                           row.at("record_bytes"), output_root / filename,
                           std::ofstream(output_root / filename, std::ios::binary)});
        require(static_cast<bool>(writers.back().stream),
                "cannot create full-corpus codec storage file");
    }
    std::array<float, dimensions> vector{};
    std::array<std::uint32_t, dimensions> codes5{};
    std::array<std::uint32_t, dimensions> codes6{};
    const auto materialization_begin = Clock::now();
    for (std::size_t document = 0; document != 1000000; ++document) {
        stream.read(reinterpret_cast<char*>(vector.data()),
                    static_cast<std::streamsize>(vector.size() * sizeof(float)));
        require(static_cast<bool>(stream), "full-corpus codec document read failed");
        float scales[2]{};
        for (unsigned bits : {5U, 6U}) {
            const auto maximum = static_cast<int>((1U << (bits - 1U)) - 1U);
            float absolute = 0.0F;
            for (const auto value : vector) absolute = std::max(absolute, std::abs(value));
            const float scale = absolute == 0.0F ? 1.0F : absolute / maximum;
            scales[bits - 5U] = scale;
            auto& codes = bits == 5 ? codes5 : codes6;
            for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
                const auto quantized = std::max(-maximum, std::min(maximum,
                    static_cast<int>(std::nearbyint(vector[dimension] / scale))));
                codes[dimension] = static_cast<std::uint32_t>(quantized + maximum);
            }
        }
        for (auto& writer : writers) {
            const auto& codes = writer.bits == 5 ? codes5 : codes6;
            const auto packed = writer.layout == "simdcomp_bp128"
                ? simdcomp_pack(codes.data(), writer.bits)
                : scalar_pack(codes.data(), writer.bits);
            require(packed.size() + sizeof(float) == writer.record_bytes,
                    "full-corpus codec record size differs");
            writer.stream.write(reinterpret_cast<const char*>(packed.data()),
                                static_cast<std::streamsize>(packed.size()));
            writer.stream.write(reinterpret_cast<const char*>(&scales[writer.bits - 5U]),
                                sizeof(float));
            require(static_cast<bool>(writer.stream), "full-corpus codec storage write failed");
        }
        if ((document + 1) % 100000 == 0) {
            std::cerr << "materialized " << (document + 1) << "/1000000 documents\n";
        }
    }
    require(stream.peek() == std::ifstream::traits_type::eof(),
            "full-corpus codec document source has trailing bytes");
    for (auto& writer : writers) writer.stream.close();
    const auto materialization_end = Clock::now();
    nlohmann::json rows = nlohmann::json::array();
    const auto output_hash_begin = Clock::now();
    for (const auto& writer : writers) {
        require(std::filesystem::file_size(writer.path) == 1000000 * writer.record_bytes,
                "full-corpus codec physical size differs");
        rows.push_back({{"id", writer.id}, {"layout", writer.layout},
                        {"bits", writer.bits}, {"record_bytes", writer.record_bytes},
                        {"file", writer.path.filename().string()},
                        {"bytes", std::filesystem::file_size(writer.path)},
                        {"sha256", agent_memory::sha256_file_hex(writer.path)}});
    }
    const auto output_hash_end = Clock::now();
    write_json(output_manifest, {
        {"schema_version", 1}, {"family", "neuroute_full_corpus_codec_storage"},
        {"input_manifest_sha256", agent_memory::sha256_file_hex(input_path)},
        {"document_source_sha256", documents.at("sha256")},
        {"documents", 1000000}, {"dimensions", dimensions},
        {"simdcomp_available", true}, {"representations", rows},
        {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"evaluator_build_environment", evaluator_build_environment()},
        {"build_timing_ms", {
            {"source_validation", milliseconds(validation_begin, validation_end)},
            {"materialization", milliseconds(materialization_begin, materialization_end)},
            {"output_hashing", milliseconds(output_hash_begin, output_hash_end)},
            {"total", milliseconds(total_begin, Clock::now())}}},
        {"environment", native_environment()},
    });
#endif
}

struct Context {
    std::vector<float> queries;
    std::vector<std::uint32_t> ranks;
    std::vector<std::vector<std::uint32_t>> pools;
    std::vector<std::uint32_t> seeds;
    nlohmann::json routes;
};

Context load_context(const nlohmann::json& input) {
    Context result;
    result.queries = read_payload<float>(input.at("queries"));
    result.ranks = read_payload<std::uint32_t>(input.at("document_id_rank"));
    result.routes = input.at("routes");
    for (const auto& route : input.at("routes")) {
        result.seeds.push_back(route.at("seed"));
        result.pools.push_back(read_payload<std::uint32_t>(route.at("pool")));
    }
    require(result.queries.size() == 76 * dimensions && result.ranks.size() == 1000000 &&
            result.pools.size() == 3 &&
            std::all_of(result.pools.begin(), result.pools.end(),
                        [](const auto& row) { return row.size() == 76 * pool_size; }),
            "full-corpus codec context shape differs");
    return result;
}

struct Scored {
    float score;
    std::uint32_t position;
    std::uint32_t rank;
};

bool better(const Scored& left, const Scored& right) {
    return left.score != right.score ? left.score > right.score : left.rank < right.rank;
}

struct Measurement {
    double fetch_ms = 0.0;
    double decode_ms = 0.0;
    double rank_ms = 0.0;
    double total_ms = 0.0;
    std::vector<std::uint32_t> top10;
};

Measurement measure_request(std::ifstream& stream, const Representation& representation,
                            const Context& context, std::size_t request) {
    const auto route = request / 76;
    const auto query = request % 76;
    const auto* positions = context.pools[route].data() + query * pool_size;
    std::vector<std::uint8_t> records(pool_size * representation.record_bytes);
    const auto total_begin = Clock::now();
    const auto fetch_begin = total_begin;
    for (std::size_t index = 0; index != pool_size; ++index) {
        stream.clear();
        stream.seekg(static_cast<std::streamoff>(positions[index]) *
                     static_cast<std::streamoff>(representation.record_bytes));
        stream.read(reinterpret_cast<char*>(records.data() + index * representation.record_bytes),
                    static_cast<std::streamsize>(representation.record_bytes));
        require(static_cast<bool>(stream), "full-corpus codec random fetch failed");
    }
    const auto fetch_end = Clock::now();
    std::array<std::uint32_t, dimensions> decoded{};
    std::vector<Scored> scored;
    scored.reserve(pool_size);
    const auto maximum = static_cast<int>((1U << (representation.bits - 1U)) - 1U);
    const auto* query_vector = context.queries.data() + query * dimensions;
    for (std::size_t index = 0; index != pool_size; ++index) {
        const auto* record = records.data() + index * representation.record_bytes;
        decode(representation, record, decoded.data());
        float scale = 0.0F;
        std::memcpy(&scale, record + representation.record_bytes - sizeof(float), sizeof(float));
        float score = 0.0F;
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            score += static_cast<float>(static_cast<int>(decoded[dimension]) - maximum) *
                     query_vector[dimension];
        }
        scored.push_back({score * scale, positions[index], context.ranks[positions[index]]});
    }
    const auto decode_end = Clock::now();
    std::nth_element(scored.begin(), scored.begin() + result_size, scored.end(), better);
    scored.resize(result_size);
    std::sort(scored.begin(), scored.end(), better);
    const auto rank_end = Clock::now();
    Measurement result;
    result.fetch_ms = milliseconds(fetch_begin, fetch_end);
    result.decode_ms = milliseconds(fetch_end, decode_end);
    result.rank_ms = milliseconds(decode_end, rank_end);
    result.total_ms = milliseconds(total_begin, rank_end);
    for (const auto& row : scored) result.top10.push_back(row.position);
    return result;
}

std::string expected_sha(const Context& context, const Representation& representation,
                         std::size_t request) {
    const auto route = request / 76;
    const auto query = request % 76;
    const auto key = representation.bits == 5 ? "int5" : "int6";
    const auto& rows = context.routes.at(route).at("expected").at(key);
    require(rows.at(query).at("query").get<std::size_t>() == query,
            "full-corpus codec expected query ordering differs");
    return rows.at(query).at("ranked_sha256");
}

void validate_file(const Representation& representation) {
    require(std::filesystem::file_size(representation.file) ==
            1000000 * representation.record_bytes,
            "full-corpus codec stored byte count differs");
    require(agent_memory::sha256_file_hex(representation.file) == representation.sha256,
            "full-corpus codec stored bytes differ");
}

void prefault(const Representation& representation) {
    std::ifstream stream(representation.file, std::ios::binary);
    std::vector<char> buffer(8 * 1024 * 1024);
    std::uint64_t bytes = 0;
    while (stream) {
        stream.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        bytes += static_cast<std::uint64_t>(stream.gcount());
    }
    require(bytes == std::filesystem::file_size(representation.file),
            "full-corpus codec warm-page-cache precondition differs");
}

double quantile(std::vector<double> values, double fraction) {
    std::sort(values.begin(), values.end());
    const auto position = fraction * static_cast<double>(values.size() - 1);
    const auto lower = static_cast<std::size_t>(std::floor(position));
    const auto upper = static_cast<std::size_t>(std::ceil(position));
    const auto weight = position - static_cast<double>(lower);
    return values[lower] * (1.0 - weight) + values[upper] * weight;
}

nlohmann::json summary(const std::vector<double>& values) {
    return {{"mean", std::accumulate(values.begin(), values.end(), 0.0) / values.size()},
            {"p50", quantile(values, 0.50)}, {"p95", quantile(values, 0.95)},
            {"p99", quantile(values, 0.99)}, {"samples", values.size()}};
}

std::string validate_quality(const Representation& representation, const Context& context) {
    std::ifstream stream(representation.file, std::ios::binary);
    std::vector<std::uint32_t> sequence;
    sequence.reserve(228 * result_size);
    for (std::size_t request = 0; request != 228; ++request) {
        const auto measured = measure_request(stream, representation, context, request);
        require(sequence_sha256(measured.top10) == expected_sha(context, representation, request),
                "full-corpus codec quality replay differs");
        sequence.insert(sequence.end(), measured.top10.begin(), measured.top10.end());
    }
    return sequence_sha256(sequence);
}

void benchmark_warm(const std::filesystem::path& storage_path,
                    const std::filesystem::path& input_path,
                    const std::filesystem::path& output_path) {
    const auto storage = read_json(storage_path);
    const auto input = read_json(input_path);
    require(storage.at("input_manifest_sha256") == agent_memory::sha256_file_hex(input_path),
            "full-corpus codec storage/input binding differs");
    const auto values = representations(storage, storage_path.parent_path());
    const auto context = load_context(input);
    nlohmann::json rows = nlohmann::json::array();
    for (const auto& representation : values) {
        validate_file(representation);
        const auto quality_sha = validate_quality(representation, context);
        prefault(representation);
        std::ifstream stream(representation.file, std::ios::binary);
        for (std::size_t pass = 0; pass != 2; ++pass) {
            for (std::size_t request = 0; request != 228; ++request) {
                (void)measure_request(stream, representation, context, request);
            }
        }
        std::vector<double> fetch, decode_values, rank, total;
        const auto fault_begin = faults();
        std::vector<std::size_t> order(228);
        std::iota(order.begin(), order.end(), 0);
        for (std::size_t pass = 0; pass != 15; ++pass) {
            std::mt19937_64 generator(2026082807ULL + pass);
            std::shuffle(order.begin(), order.end(), generator);
            for (const auto request : order) {
                const auto sample = measure_request(stream, representation, context, request);
                fetch.push_back(sample.fetch_ms);
                decode_values.push_back(sample.decode_ms);
                rank.push_back(sample.rank_ms);
                total.push_back(sample.total_ms);
            }
        }
        const auto fault_end = faults();
        rows.push_back({
            {"id", representation.id}, {"layout", representation.layout},
            {"bits", representation.bits}, {"record_bytes", representation.record_bytes},
            {"storage_bytes", std::filesystem::file_size(representation.file)},
            {"storage_sha256", representation.sha256},
            {"logical_fetch_bytes_per_request", pool_size * representation.record_bytes},
            {"random_reads_per_request", pool_size},
            {"quality_replay_sequence_sha256", quality_sha},
            {"timing", {{"fetch_ms", summary(fetch)},
                         {"decode_and_dot_ms", summary(decode_values)},
                         {"rank_top10_ms", summary(rank)}, {"total_ms", summary(total)}}},
            {"page_fault_delta", fault_delta(fault_begin, fault_end)},
        });
    }
    write_json(output_path, {
        {"schema_version", 1}, {"family", "neuroute_full_corpus_codec_warm_result"},
        {"storage_manifest_sha256", agent_memory::sha256_file_hex(storage_path)},
        {"input_manifest_sha256", agent_memory::sha256_file_hex(input_path)},
        {"cache_state", "sequentially_prefaulted_selected_file"},
        {"simdcomp_available", AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP != 0},
        {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"evaluator_build_environment", evaluator_build_environment()},
        {"environment", native_environment()},
        {"rows", rows},
    });
}

void cold_sample(const std::filesystem::path& storage_path,
                 const std::filesystem::path& input_path, const std::string& id,
                 std::size_t request, const std::filesystem::path& output_path) {
    require(request < 228, "full-corpus codec cold request differs");
    const auto storage = read_json(storage_path);
    const auto input = read_json(input_path);
    const auto values = representations(storage, storage_path.parent_path());
    const auto& representation = find_representation(values, id);
    const auto context = load_context(input);
    std::ifstream stream(representation.file, std::ios::binary);
    require(static_cast<bool>(stream), "full-corpus codec cold storage open failed");
    const auto begin = faults();
    const auto sample = measure_request(stream, representation, context, request);
    const auto end = faults();
    const auto ranked = sequence_sha256(sample.top10);
    require(ranked == expected_sha(context, representation, request),
            "full-corpus codec cold quality replay differs");
    write_json(output_path, {
        {"schema_version", 1}, {"family", "neuroute_full_corpus_codec_cold_sample"},
        {"representation", id}, {"request", request},
        {"seed", context.seeds[request / 76]}, {"query", request % 76},
        {"ranked_sha256", ranked}, {"storage_sha256_declared", representation.sha256},
        {"logical_fetch_bytes", pool_size * representation.record_bytes},
        {"random_reads", pool_size}, {"fetch_ms", sample.fetch_ms},
        {"decode_and_dot_ms", sample.decode_ms}, {"rank_top10_ms", sample.rank_ms},
        {"total_ms", sample.total_ms}, {"page_fault_delta", fault_delta(begin, end)},
        {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"evaluator_build_environment", evaluator_build_environment()},
        {"passed", true},
    });
}

void validate_report(const std::filesystem::path& storage_path,
                     const std::filesystem::path& input_path,
                     const std::filesystem::path& report_path) {
    const auto storage = read_json(storage_path);
    const auto input = read_json(input_path);
    const auto report = read_json(report_path);
    require(report.at("storage_manifest_sha256") == agent_memory::sha256_file_hex(storage_path) &&
            report.at("input_manifest_sha256") == agent_memory::sha256_file_hex(input_path),
            "full-corpus codec report binding differs");
    const auto values = representations(storage, storage_path.parent_path());
    const auto context = load_context(input);
    require(report.at("rows").size() == values.size(),
            "full-corpus codec report matrix differs");
    for (std::size_t index = 0; index != values.size(); ++index) {
        validate_file(values[index]);
        require(report.at("rows").at(index).at("id") == values[index].id &&
                report.at("rows").at(index).at("quality_replay_sequence_sha256") ==
                    validate_quality(values[index], context),
                "full-corpus codec report quality differs");
    }
}

void self_test() {
    const auto build = evaluator_build_environment();
    require(build.value("configured_environment_sha256", std::string{}).size() == 64 &&
            build.value("cxx_standard", 0) == 17 &&
            build.value("pointer_bits", 0) == static_cast<int>(sizeof(void*) * 8U),
            "full-corpus codec build-environment self-test differs");
    require(quantile({1.0, 2.0, 3.0}, 0.5) == 2.0,
            "full-corpus codec quantile self-test differs");
    std::array<std::uint32_t, dimensions> input{};
    std::array<std::uint32_t, dimensions> output{};
    for (std::size_t index = 0; index != dimensions; ++index) input[index] = index % 31;
    const auto scalar = scalar_pack(input.data(), 5);
    scalar_unpack(scalar.data(), 5, output.data());
    require(input == output && scalar.size() == 240,
            "full-corpus codec scalar self-test differs");
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    const auto simd = simdcomp_pack(input.data(), 5);
    output.fill(0);
    simdcomp_unpack(simd.data(), 5, output.data());
    require(input == output && simd.size() == 240,
            "full-corpus codec SIMDComp self-test differs");
#endif
    std::cout << "NeuRoute full-corpus codec self-test passed\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            self_test();
            return 0;
        }
        if (argc == 5 && std::string(argv[1]) == "--build") {
            build_storage(argv[2], argv[3], argv[4]);
            return 0;
        }
        if (argc == 5 && std::string(argv[1]) == "--benchmark-warm") {
            benchmark_warm(argv[2], argv[3], argv[4]);
            return 0;
        }
        if (argc == 7 && std::string(argv[1]) == "--cold-sample") {
            cold_sample(argv[2], argv[3], argv[4],
                        static_cast<std::size_t>(std::stoul(argv[5])), argv[6]);
            return 0;
        }
        if (argc == 5 && std::string(argv[1]) == "--validate") {
            validate_report(argv[2], argv[3], argv[4]);
            std::cout << "NeuRoute full-corpus codec validation passed\n";
            return 0;
        }
        std::cerr << "usage: agent-memory-neuroute-full-corpus-codec "
                     "--self-test | --build INPUT ROOT MANIFEST | "
                     "--benchmark-warm STORAGE INPUT OUTPUT | "
                     "--cold-sample STORAGE INPUT REPRESENTATION REQUEST OUTPUT | "
                     "--validate STORAGE INPUT REPORT\n";
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-full-corpus-codec: " << error.what() << '\n';
        return 1;
    }
}
