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
#include <stdexcept>
#include <string>
#include <vector>

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
#include <simdbitpacking.h>
#endif

namespace {

constexpr std::size_t dimensions = 384;
constexpr std::size_t addresses_per_query = 1024;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

std::vector<std::uint8_t> simdcomp_pack(const std::uint32_t* values, unsigned bits) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    (void)values;
    (void)bits;
    throw std::runtime_error("R4 representative codec requires SIMDComp");
#else
    std::vector<__m128i> words(3 * bits);
    for (std::size_t block = 0; block != 3; ++block) {
        simdpackwithoutmask(values + block * 128,
                            words.data() + block * bits, bits);
    }
    std::vector<std::uint8_t> output(dimensions * bits / 8);
    std::memcpy(output.data(), words.data(), output.size());
    return output;
#endif
}

void simdcomp_unpack(const std::uint8_t* bytes, unsigned bits,
                     std::uint32_t* output) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    (void)bytes;
    (void)bits;
    (void)output;
    throw std::runtime_error("R4 representative codec requires SIMDComp");
#else
    alignas(16) std::array<__m128i, 36> words{};
    std::memcpy(words.data(), bytes, dimensions * bits / 8);
    for (std::size_t block = 0; block != 3; ++block) {
        simdunpack(words.data() + block * bits, output + block * 128, bits);
    }
#endif
}

void pack_file(unsigned bits, const std::filesystem::path& codes_path,
               const std::filesystem::path& scales_path, std::size_t rows,
               const std::filesystem::path& output_path) {
    require(bits >= 4 && bits <= 12, "R4 representative codec bits differ");
    std::ifstream codes(codes_path, std::ios::binary);
    std::ifstream scales(scales_path, std::ios::binary);
    std::ofstream output(output_path, std::ios::binary);
    require(codes && scales && output, "R4 representative codec file open failed");
    std::array<std::uint32_t, dimensions> values{};
    std::array<std::uint8_t, dimensions> values8{};
    std::array<std::uint16_t, dimensions> values16{};
    float scale = 0.0F;
    for (std::size_t row = 0; row != rows; ++row) {
        if (bits <= 8) {
            codes.read(reinterpret_cast<char*>(values8.data()), values8.size());
            std::copy(values8.begin(), values8.end(), values.begin());
        } else {
            codes.read(reinterpret_cast<char*>(values16.data()),
                       static_cast<std::streamsize>(values16.size() *
                                                    sizeof(std::uint16_t)));
            std::copy(values16.begin(), values16.end(), values.begin());
        }
        scales.read(reinterpret_cast<char*>(&scale), sizeof(scale));
        require(codes && scales, "R4 representative codec input truncated");
        const auto packed = simdcomp_pack(values.data(), bits);
        output.write(reinterpret_cast<const char*>(packed.data()),
                     static_cast<std::streamsize>(packed.size()));
        output.write(reinterpret_cast<const char*>(&scale), sizeof(scale));
        require(static_cast<bool>(output), "R4 representative codec output failed");
    }
    require(codes.peek() == std::ifstream::traits_type::eof() &&
            scales.peek() == std::ifstream::traits_type::eof(),
            "R4 representative codec input has trailing bytes");
    output.close();
    require(std::filesystem::file_size(output_path) == rows * (dimensions * bits / 8 + 4),
            "R4 representative codec physical size differs");
}

void unpack_file(unsigned bits, const std::filesystem::path& input_path,
                 std::size_t rows, const std::filesystem::path& output_path) {
    require(bits >= 4 && bits <= 12, "R4 representative codec bits differ");
    const std::size_t packed_bytes = dimensions * bits / 8;
    std::ifstream input(input_path, std::ios::binary);
    std::ofstream output(output_path, std::ios::binary);
    require(input && output, "R4 representative codec decode file open failed");
    std::vector<std::uint8_t> packed(packed_bytes);
    std::array<std::uint32_t, dimensions> values{};
    std::array<float, dimensions> decoded{};
    const int maximum = static_cast<int>((1U << (bits - 1U)) - 1U);
    float scale = 0.0F;
    for (std::size_t row = 0; row != rows; ++row) {
        input.read(reinterpret_cast<char*>(packed.data()),
                   static_cast<std::streamsize>(packed.size()));
        input.read(reinterpret_cast<char*>(&scale), sizeof(scale));
        require(static_cast<bool>(input), "R4 representative codec record truncated");
        simdcomp_unpack(packed.data(), bits, values.data());
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            decoded[dimension] = static_cast<float>(
                static_cast<int>(values[dimension]) - maximum) * scale;
        }
        output.write(reinterpret_cast<const char*>(decoded.data()),
                     static_cast<std::streamsize>(decoded.size() * sizeof(float)));
        require(static_cast<bool>(output), "R4 representative codec decoded output failed");
    }
    require(input.peek() == std::ifstream::traits_type::eof(),
            "R4 representative codec records have trailing bytes");
}

std::vector<float> decode_table(unsigned bits, const std::string& compander,
                                float parameter) {
    require(bits >= 4 && bits <= 12,
            "R4 nonlinear codec bits differ");
    require(compander == "uniform" || compander == "power" ||
            compander == "mulaw", "R4 nonlinear compander differs");
    const int maximum = static_cast<int>((1U << (bits - 1U)) - 1U);
    require(compander == "uniform" || parameter > 0.0F,
            "R4 nonlinear parameter differs");
    std::vector<float> result(1U << bits, 0.0F);
    for (int code = 0; code <= 2 * maximum; ++code) {
        const int signed_code = code - maximum;
        if (compander == "uniform") {
            result[static_cast<std::size_t>(code)] =
                static_cast<float>(signed_code);
            continue;
        }
        const float sign = signed_code < 0 ? -1.0F : 1.0F;
        const float magnitude = static_cast<float>(std::abs(signed_code)) /
                                static_cast<float>(maximum);
        const float decoded = compander == "power"
            ? std::pow(magnitude, 1.0F / parameter)
            : std::expm1(magnitude * std::log1p(parameter)) / parameter;
        result[static_cast<std::size_t>(code)] = sign * decoded;
    }
    return result;
}

void unpack_nonlinear_file(unsigned bits, const std::string& compander,
                           float parameter,
                           const std::filesystem::path& input_path,
                           std::size_t rows,
                           const std::filesystem::path& output_path) {
    require(compander == "power" || compander == "mulaw",
            "R4 nonlinear unpack compander differs");
    const std::size_t packed_bytes = dimensions * bits / 8;
    std::ifstream input(input_path, std::ios::binary);
    std::ofstream output(output_path, std::ios::binary);
    require(input && output, "R4 nonlinear decode file open failed");
    std::vector<std::uint8_t> packed(packed_bytes);
    std::array<std::uint32_t, dimensions> values{};
    std::array<float, dimensions> decoded{};
    const auto table = decode_table(bits, compander, parameter);
    float amplitude = 0.0F;
    for (std::size_t row = 0; row != rows; ++row) {
        input.read(reinterpret_cast<char*>(packed.data()),
                   static_cast<std::streamsize>(packed.size()));
        input.read(reinterpret_cast<char*>(&amplitude), sizeof(amplitude));
        require(static_cast<bool>(input), "R4 nonlinear record truncated");
        if (bits == 8) {
            for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
                values[dimension] = packed[dimension];
        } else {
            simdcomp_unpack(packed.data(), bits, values.data());
        }
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            require(values[dimension] < table.size(),
                    "R4 nonlinear code range differs");
            decoded[dimension] = table[values[dimension]] * amplitude;
        }
        output.write(reinterpret_cast<const char*>(decoded.data()),
                     static_cast<std::streamsize>(decoded.size() * sizeof(float)));
        require(static_cast<bool>(output), "R4 nonlinear decoded output failed");
    }
    require(input.peek() == std::ifstream::traits_type::eof(),
            "R4 nonlinear records have trailing bytes");
}

template <typename T>
std::vector<T> read_values(const std::filesystem::path& path) {
    const auto bytes = std::filesystem::file_size(path);
    require(bytes % sizeof(T) == 0, "R4 nonlinear benchmark byte count differs");
    std::vector<T> result(bytes / sizeof(T));
    std::ifstream stream(path, std::ios::binary);
    stream.read(reinterpret_cast<char*>(result.data()),
                static_cast<std::streamsize>(bytes));
    require(static_cast<bool>(stream), "R4 nonlinear benchmark read failed");
    return result;
}

float record_dot(const std::uint8_t* record, unsigned bits,
                 const std::vector<float>& table, const float* query,
                 std::array<std::uint32_t, dimensions>& unpacked) {
    const auto packed_bytes = dimensions * bits / 8;
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + packed_bytes, sizeof(amplitude));
    if (bits == 8) {
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
            unpacked[dimension] = record[dimension];
    } else {
        simdcomp_unpack(record, bits, unpacked.data());
    }
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
        result += table[unpacked[dimension]] * amplitude * query[dimension];
    return result;
}

void benchmark_dot(unsigned bits, const std::string& compander, float parameter,
                   const std::filesystem::path& store_path, std::size_t rows,
                   const std::filesystem::path& offsets_path,
                   const std::filesystem::path& counts_path,
                   const std::filesystem::path& shortlists_path,
                   const std::filesystem::path& queries_path,
                   std::size_t measured_passes,
                   const std::filesystem::path& output_path) {
    const auto record_bytes = dimensions * bits / 8 + sizeof(float);
    const auto store = read_values<std::uint8_t>(store_path);
    const auto offsets = read_values<std::uint32_t>(offsets_path);
    const auto counts = read_values<std::uint8_t>(counts_path);
    const auto shortlists = read_values<std::uint32_t>(shortlists_path);
    const auto queries = read_values<float>(queries_path);
    require(store.size() == rows * record_bytes && offsets.size() == counts.size() &&
            shortlists.size() % addresses_per_query == 0 &&
            queries.size() / dimensions == shortlists.size() / addresses_per_query &&
            measured_passes >= 1, "R4 nonlinear benchmark shape differs");
    const auto table = decode_table(bits, compander, parameter);
    const auto query_count = queries.size() / dimensions;
    nlohmann::json samples = nlohmann::json::array();
    double checksum = 0.0;
    for (std::size_t pass = 0; pass != measured_passes + 1; ++pass) {
        for (std::size_t query_index = 0; query_index != query_count; ++query_index) {
            const auto begin = std::chrono::steady_clock::now();
            std::array<std::uint32_t, dimensions> unpacked{};
            std::uint64_t representatives = 0;
            double local_checksum = 0.0;
            for (std::size_t local = 0; local != addresses_per_query; ++local) {
                const auto row = shortlists[query_index * addresses_per_query + local];
                require(row < offsets.size(), "R4 nonlinear shortlist row differs");
                float maximum = -std::numeric_limits<float>::infinity();
                for (std::size_t slot = 0; slot != counts[row]; ++slot) {
                    const auto physical = static_cast<std::size_t>(offsets[row]) + slot;
                    maximum = std::max(maximum, record_dot(
                        store.data() + physical * record_bytes, bits, table,
                        queries.data() + query_index * dimensions, unpacked));
                    ++representatives;
                }
                local_checksum += maximum;
            }
            const auto end = std::chrono::steady_clock::now();
            checksum += local_checksum;
            if (pass != 0) samples.push_back({
                {"pass", pass - 1}, {"query", query_index},
                {"representatives_scored", representatives},
                {"decode_dot_max_ms", std::chrono::duration<double, std::milli>(
                    end - begin).count()}});
        }
    }
    std::ofstream output(output_path);
    require(static_cast<bool>(output), "R4 nonlinear benchmark output open failed");
    output << nlohmann::json{{"schema_version", 1},
        {"family", "neuroute_r4_nonlinear_codec_native_samples"},
        {"bits", bits}, {"compander", compander}, {"parameter", parameter},
        {"rows", rows}, {"queries", query_count},
        {"measured_passes", measured_passes}, {"checksum", checksum},
        {"store_sha256", agent_memory::sha256_file_hex(store_path)},
        {"samples", samples}}.dump(2) << '\n';
}

void self_test() {
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    for (const unsigned bits : {5U, 8U, 9U, 10U, 12U}) {
        std::array<std::uint32_t, dimensions> values{};
        const auto mask = (1U << bits) - 1U;
        for (std::size_t index = 0; index != values.size(); ++index)
            values[index] = static_cast<std::uint32_t>(
                (index * 37U + bits) & mask);
        const auto packed = simdcomp_pack(values.data(), bits);
        std::array<std::uint32_t, dimensions> decoded{};
        simdcomp_unpack(packed.data(), bits, decoded.data());
        for (std::size_t index = 0; index != values.size(); ++index) {
            require(decoded[index] == values[index],
                    "R4 representative codec SIMDComp self-test differs");
        }
    }
#endif
    const auto power = decode_table(5, "power", 0.5F);
    const auto mulaw = decode_table(6, "mulaw", 15.0F);
    require(std::abs(power.front() + 1.0F) < 1.0e-6F &&
            std::abs(power[15]) < 1.0e-6F &&
            std::abs(power[30] - 1.0F) < 1.0e-6F &&
            std::abs(mulaw.front() + 1.0F) < 1.0e-6F &&
            std::abs(mulaw[62] - 1.0F) < 1.0e-6F,
            "R4 nonlinear decode table differs");
    std::cout << "NeuRoute R4 representative-codec native self-test passed\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            self_test();
            return 0;
        }
        if (argc == 7 && std::string(argv[1]) == "--pack") {
            pack_file(static_cast<unsigned>(std::stoul(argv[2])), argv[3], argv[4],
                      static_cast<std::size_t>(std::stoull(argv[5])), argv[6]);
            return 0;
        }
        if (argc == 6 && std::string(argv[1]) == "--unpack") {
            unpack_file(static_cast<unsigned>(std::stoul(argv[2])), argv[3],
                        static_cast<std::size_t>(std::stoull(argv[4])), argv[5]);
            return 0;
        }
        if (argc == 8 && std::string(argv[1]) == "--unpack-nonlinear") {
            unpack_nonlinear_file(static_cast<unsigned>(std::stoul(argv[2])), argv[3],
                std::stof(argv[4]), argv[5], static_cast<std::size_t>(
                    std::stoull(argv[6])), argv[7]);
            return 0;
        }
        if (argc == 13 && std::string(argv[1]) == "--benchmark-dot") {
            benchmark_dot(static_cast<unsigned>(std::stoul(argv[2])), argv[3],
                std::stof(argv[4]), argv[5], static_cast<std::size_t>(
                    std::stoull(argv[6])), argv[7], argv[8], argv[9], argv[10],
                static_cast<std::size_t>(std::stoull(argv[11])), argv[12]);
            return 0;
        }
        throw std::runtime_error("usage: --self-test | --pack BITS CODES SCALES ROWS OUTPUT | --unpack BITS INPUT ROWS OUTPUT | --unpack-nonlinear BITS KIND PARAM INPUT ROWS OUTPUT | --benchmark-dot BITS KIND PARAM STORE ROWS OFFSETS COUNTS SHORTLISTS QUERIES PASSES OUTPUT");
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-representative-codec: "
                  << error.what() << '\n';
        return 1;
    }
}
