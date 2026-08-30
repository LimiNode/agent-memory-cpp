#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
#include <simdbitpacking.h>
#endif

namespace {

constexpr std::size_t dimensions = 384;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

std::vector<std::uint8_t> simdcomp_pack(const std::uint8_t* values, unsigned bits) {
#if !AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    (void)values;
    (void)bits;
    throw std::runtime_error("R4 representative codec requires SIMDComp");
#else
    std::array<std::uint32_t, dimensions> input{};
    std::copy_n(values, dimensions, input.begin());
    std::vector<__m128i> words(3 * bits);
    for (std::size_t block = 0; block != 3; ++block) {
        simdpackwithoutmask(input.data() + block * 128,
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
    std::vector<__m128i> words(3 * bits);
    std::memcpy(words.data(), bytes, dimensions * bits / 8);
    for (std::size_t block = 0; block != 3; ++block) {
        simdunpack(words.data() + block * bits, output + block * 128, bits);
    }
#endif
}

void pack_file(unsigned bits, const std::filesystem::path& codes_path,
               const std::filesystem::path& scales_path, std::size_t rows,
               const std::filesystem::path& output_path) {
    require(bits == 5 || bits == 6, "R4 representative codec bits differ");
    std::ifstream codes(codes_path, std::ios::binary);
    std::ifstream scales(scales_path, std::ios::binary);
    std::ofstream output(output_path, std::ios::binary);
    require(codes && scales && output, "R4 representative codec file open failed");
    std::array<std::uint8_t, dimensions> values{};
    float scale = 0.0F;
    for (std::size_t row = 0; row != rows; ++row) {
        codes.read(reinterpret_cast<char*>(values.data()), values.size());
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
    require(bits == 5 || bits == 6, "R4 representative codec bits differ");
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

void self_test() {
    std::array<std::uint8_t, dimensions> values{};
    for (std::size_t index = 0; index != values.size(); ++index) {
        values[index] = static_cast<std::uint8_t>(index % 32);
    }
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    const auto packed = simdcomp_pack(values.data(), 5);
    std::array<std::uint32_t, dimensions> decoded{};
    simdcomp_unpack(packed.data(), 5, decoded.data());
    for (std::size_t index = 0; index != values.size(); ++index) {
        require(decoded[index] == values[index],
                "R4 representative codec SIMDComp self-test differs");
    }
#endif
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
        throw std::runtime_error("usage: --self-test | --pack BITS CODES SCALES ROWS OUTPUT | --unpack BITS INPUT ROWS OUTPUT");
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-representative-codec: "
                  << error.what() << '\n';
        return 1;
    }
}
