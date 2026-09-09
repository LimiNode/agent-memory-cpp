#include "neuroute_record_codec.hpp"

#include <agent_memory.hpp>
#include <nlohmann/json.hpp>

#include <array>
#include <algorithm>
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

using agent_memory::neuroute::RecordExecutionKernel;
using agent_memory::neuroute::RepresentativeStorageMode;
constexpr std::size_t dimensions = agent_memory::neuroute::record_dimensions;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

nlohmann::json read_json(const std::filesystem::path& path) {
    std::ifstream stream(path);
    require(static_cast<bool>(stream), "record-codec JSON open failed");
    nlohmann::json value;
    stream >> value;
    return value;
}

void write_json(const std::filesystem::path& path, const nlohmann::json& value) {
    std::ofstream stream(path);
    require(static_cast<bool>(stream), "record-codec JSON output failed");
    stream << value.dump(2) << '\n';
}

nlohmann::json descriptor_json(RepresentativeStorageMode mode) {
    const auto value = agent_memory::neuroute::record_format(mode);
    return {{"format_version", value.version}, {"dimensions", value.dimensions},
        {"codec", agent_memory::neuroute::storage_mode_name(value.codec)},
        {"record_bytes", value.record_bytes},
        {"code_layout", value.code_layout},
        {"scale_layout", value.scale_layout},
        {"compander", value.compander}};
}

std::vector<RecordExecutionKernel> supported_kernels() {
    std::vector<RecordExecutionKernel> result;
    for (const auto kernel : {RecordExecutionKernel::Portable,
                              RecordExecutionKernel::Sse2,
                              RecordExecutionKernel::Avx2}) {
        if (agent_memory::neuroute::execution_kernel_supported(kernel))
            result.push_back(kernel);
    }
    return result;
}

std::array<std::uint8_t,
    agent_memory::neuroute::nonlinear_int5_record_bytes> synthetic_record() {
    std::array<std::uint32_t, dimensions> codes{};
    for (std::size_t index = 0; index != codes.size(); ++index)
        codes[index] = static_cast<std::uint32_t>((index * 17U + 11U) % 31U);
    std::array<std::uint8_t,
        agent_memory::neuroute::nonlinear_int5_record_bytes> record{};
    agent_memory::neuroute::pack_nonlinear_int5_power_half(codes, 0.75F,
                                                            record);
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    std::array<std::uint8_t, 240> simdcomp{};
    for (std::size_t block = 0; block != 3; ++block) {
        alignas(16) std::array<__m128i, 5> packed{};
        simdpackwithoutmask(codes.data() + block * 128U, packed.data(), 5);
        std::memcpy(simdcomp.data() + block * 80U, packed.data(), 80U);
    }
    require(std::equal(simdcomp.begin(), simdcomp.end(), record.begin()),
            "portable canonical pack differs from SIMDComp BP128 bytes");
#endif
    return record;
}

void self_test() {
    require(agent_memory::neuroute::parse_storage_mode("int8") ==
                RepresentativeStorageMode::Int8 &&
            agent_memory::neuroute::parse_storage_mode(
                "nonlinear_int5_power_half") ==
                RepresentativeStorageMode::NonlinearInt5PowerHalf,
            "record-codec explicit storage configuration differs");
    const auto record = synthetic_record();
    std::array<std::uint32_t, dimensions> reference{};
    agent_memory::neuroute::decode_nonlinear_int5_codes(
        record.data(), reference, RecordExecutionKernel::Portable);
    std::array<float, dimensions> query{};
    for (std::size_t index = 0; index != query.size(); ++index)
        query[index] = static_cast<float>(static_cast<int>(index % 23U) - 11) /
                       23.0F;
    const auto score = agent_memory::neuroute::score_nonlinear_int5_power_half(
        record.data(), query.data(), RecordExecutionKernel::Portable);
    for (const auto kernel : supported_kernels()) {
        std::array<std::uint32_t, dimensions> decoded{};
        agent_memory::neuroute::decode_nonlinear_int5_codes(
            record.data(), decoded, kernel);
        require(decoded == reference,
                "record-codec execution decoded codes differ");
        require(agent_memory::neuroute::score_nonlinear_int5_power_half(
                    record.data(), query.data(), kernel) == score,
                "record-codec ordered score differs");
    }
    require(agent_memory::neuroute::nonlinear_int5_amplitude(record.data()) ==
                0.75F,
            "record-codec little-endian amplitude differs");
    std::cout << "NeuRoute record codec contract self-test passed\n";
}

nlohmann::json verify_store(const std::filesystem::path& manifest_path,
                            const std::string& representation_id,
                            RepresentativeStorageMode selected_mode) {
    require(selected_mode == RepresentativeStorageMode::NonlinearInt5PowerHalf,
            "verification store does not match selected storage mode");
    const auto manifest = read_json(manifest_path);
    require(manifest.value("family", "") ==
                "neuroute_full_corpus_codec_storage" &&
            manifest.at("dimensions").get<std::size_t>() == dimensions,
            "record-codec storage manifest differs");
    const auto found = std::find_if(manifest.at("representations").begin(),
        manifest.at("representations").end(), [&](const nlohmann::json& row) {
            return row.at("id").get<std::string>() == representation_id;
        });
    require(found != manifest.at("representations").end() &&
            found->at("record_bytes").get<std::size_t>() ==
                agent_memory::neuroute::nonlinear_int5_record_bytes &&
            found->at("layout").get<std::string>() == "simdcomp_bp128",
            "record-codec representation differs");
    const auto path = manifest_path.parent_path() /
                      found->at("file").get<std::string>();
    require(std::filesystem::file_size(path) ==
                found->at("bytes").get<std::uint64_t>() &&
            agent_memory::sha256_file_hex(path) ==
                found->at("sha256").get<std::string>(),
            "record-codec physical bytes differ");
    constexpr std::size_t samples = 65536;
    const auto documents = manifest.at("documents").get<std::size_t>();
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "record-codec store open failed");
    std::array<std::uint8_t,
        agent_memory::neuroute::nonlinear_int5_record_bytes> record{};
    std::vector<std::uint8_t> reference_bytes;
    reference_bytes.reserve(samples * dimensions * sizeof(std::uint32_t));
    std::vector<std::uint8_t> kernel_bytes;
    kernel_bytes.reserve(reference_bytes.capacity());
    std::array<std::uint32_t, dimensions> reference{}, decoded{};
    for (std::size_t sample = 0; sample != samples; ++sample) {
        const auto document = (sample * 104729U + 8191U) % documents;
        stream.seekg(static_cast<std::streamoff>(document * record.size()));
        stream.read(reinterpret_cast<char*>(record.data()), record.size());
        require(static_cast<bool>(stream), "record-codec sample read failed");
        agent_memory::neuroute::decode_nonlinear_int5_codes(
            record.data(), reference, RecordExecutionKernel::Portable);
        const auto* bytes = reinterpret_cast<const std::uint8_t*>(
            reference.data());
        reference_bytes.insert(reference_bytes.end(), bytes,
                               bytes + dimensions * sizeof(std::uint32_t));
    }
    const auto reference_sha = agent_memory::sha256_bytes_hex(reference_bytes);
    nlohmann::json kernels = nlohmann::json::array();
    for (const auto kernel : supported_kernels()) {
        stream.clear();
        kernel_bytes.clear();
        for (std::size_t sample = 0; sample != samples; ++sample) {
            const auto document = (sample * 104729U + 8191U) % documents;
            stream.seekg(static_cast<std::streamoff>(document * record.size()));
            stream.read(reinterpret_cast<char*>(record.data()), record.size());
            require(static_cast<bool>(stream), "record-codec replay read failed");
            agent_memory::neuroute::decode_nonlinear_int5_codes(
                record.data(), decoded, kernel);
            const auto* bytes = reinterpret_cast<const std::uint8_t*>(
                decoded.data());
            kernel_bytes.insert(kernel_bytes.end(), bytes,
                                bytes + dimensions * sizeof(std::uint32_t));
        }
        const auto digest = agent_memory::sha256_bytes_hex(kernel_bytes);
        require(digest == reference_sha,
                "record-codec physical decode digest differs");
        kernels.push_back({{"id",
            agent_memory::neuroute::execution_kernel_name(kernel)},
            {"decoded_sha256", digest}, {"matches_portable", true}});
    }
    return {{"schema_version", 1},
        {"family", "neuroute_record_codec_compatibility_report"},
        {"selected_storage_mode",
            agent_memory::neuroute::storage_mode_name(selected_mode)},
        {"stores_materialized_by_configuration", 1},
        {"format", descriptor_json(selected_mode)},
        {"physical_store", {{"path", path.string()},
            {"bytes", std::filesystem::file_size(path)},
            {"sha256", found->at("sha256")},
            {"records", documents}}},
        {"sample_records", samples},
        {"portable_decoded_sha256", reference_sha},
        {"kernels", kernels},
        {"build", {
            {"safe_portable_default", true},
            {"sse2_compiled", static_cast<bool>(
                AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2)},
            {"avx2_compiled", static_cast<bool>(
                AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2)},
            {"avx2_runtime_supported",
                agent_memory::neuroute::execution_kernel_supported(
                    RecordExecutionKernel::Avx2)}}}};
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            self_test();
        } else if (argc == 6 && std::string(argv[1]) == "--verify") {
            const auto mode = agent_memory::neuroute::parse_storage_mode(argv[4]);
            write_json(argv[5], verify_store(argv[2], argv[3], mode));
        } else {
            throw std::runtime_error(
                "usage: --self-test | --verify MANIFEST REPRESENTATION STORAGE_MODE OUTPUT");
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-record-codec-contract: "
                  << error.what() << '\n';
        return 1;
    }
}
