#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "rabitqlib/index/estimator.hpp"
#include "rabitqlib/quantization/data_layout.hpp"
#include "rabitqlib/quantization/rabitq.hpp"
#include "rabitqlib/utils/rotator.hpp"
#include "rabitqlib/utils/space.hpp"

namespace {

constexpr std::size_t kDimensions = 384;
constexpr std::size_t kQueryCount = 152;
constexpr std::size_t kCandidatesPerQuery = 128;

#if defined(_MSC_VER)
constexpr const char* kCompiler = "MSVC "
#define AM_STRINGIZE_IMPL(value) #value
#define AM_STRINGIZE(value) AM_STRINGIZE_IMPL(value)
    AM_STRINGIZE(_MSC_VER);
#elif defined(__clang__)
constexpr const char* kCompiler = "Clang " __clang_version__;
#elif defined(__GNUC__)
constexpr const char* kCompiler = "GCC " __VERSION__;
#else
constexpr const char* kCompiler = "unknown";
#endif

using Clock = std::chrono::steady_clock;

template <typename T>
std::vector<T> read_exact(const std::filesystem::path& path, std::size_t count) {
    const auto expected_bytes = count * sizeof(T);
    if (std::filesystem::file_size(path) != expected_bytes) {
        throw std::runtime_error("unexpected byte size: " + path.string());
    }
    std::vector<T> values(count);
    std::ifstream input(path, std::ios::binary);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(expected_bytes));
    if (!input) {
        throw std::runtime_error("failed to read: " + path.string());
    }
    return values;
}

template <typename T>
void write_all(const std::filesystem::path& path, const std::vector<T>& values) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(
        reinterpret_cast<const char*>(values.data()),
        static_cast<std::streamsize>(values.size() * sizeof(T))
    );
    if (!output) {
        throw std::runtime_error("failed to write: " + path.string());
    }
}

void normalize(float* values, std::size_t count) {
    double squared_norm = 0.0;
    for (std::size_t i = 0; i < count; ++i) {
        squared_norm += static_cast<double>(values[i]) * static_cast<double>(values[i]);
    }
    const double norm = std::sqrt(squared_norm);
    if (!std::isfinite(norm) || norm <= std::numeric_limits<double>::min()) {
        throw std::runtime_error("encountered a non-finite or zero source vector");
    }
    const float inverse = static_cast<float>(1.0 / norm);
    for (std::size_t i = 0; i < count; ++i) {
        values[i] *= inverse;
    }
}

std::vector<int> parse_widths(const std::string& value) {
    std::vector<int> widths;
    std::size_t begin = 0;
    while (begin < value.size()) {
        const auto end = value.find(',', begin);
        const auto token = value.substr(begin, end == std::string::npos ? end : end - begin);
        const int width = std::stoi(token);
        if (width < 2 || width > 4 || std::find(widths.begin(), widths.end(), width) != widths.end()) {
            throw std::runtime_error("widths must be a unique subset of 2,3,4");
        }
        widths.push_back(width);
        if (end == std::string::npos) {
            break;
        }
        begin = end + 1;
    }
    if (widths.empty()) {
        throw std::runtime_error("at least one width is required");
    }
    return widths;
}

struct WidthResult {
    int bits = 0;
    std::size_t bin_bytes = 0;
    std::size_t ex_bytes = 0;
    double full_audit_encode_seconds = 0.0;
    double compact_encode_seconds = 0.0;
    double score_seconds = 0.0;
    double max_score_difference = 0.0;
};

int run(int argc, char** argv) {
    if (argc != 8) {
        throw std::runtime_error(
            "usage: runner <documents.f32> <queries.f32> <selected-ids.i64> "
            "<rotation.f32> <widths> <output-dir> <metadata.json>"
        );
    }

    const std::filesystem::path documents_path(argv[1]);
    const std::filesystem::path queries_path(argv[2]);
    const std::filesystem::path selected_ids_path(argv[3]);
    const std::filesystem::path rotation_path(argv[4]);
    const auto widths = parse_widths(argv[5]);
    const std::filesystem::path output_dir(argv[6]);
    const std::filesystem::path metadata_path(argv[7]);

    if (std::filesystem::file_size(documents_path) != 1'000'000ULL * kDimensions * sizeof(float)) {
        throw std::runtime_error("documents must contain 1M FP32x384 rows");
    }
    const auto selected_ids = read_exact<std::int64_t>(
        selected_ids_path, kQueryCount * kCandidatesPerQuery
    );
    auto queries = read_exact<float>(queries_path, kQueryCount * kDimensions);
    const auto rotation = read_exact<float>(rotation_path, kDimensions * kDimensions);

    std::vector<std::int64_t> unique_ids = selected_ids;
    std::sort(unique_ids.begin(), unique_ids.end());
    unique_ids.erase(std::unique(unique_ids.begin(), unique_ids.end()), unique_ids.end());
    if (unique_ids.empty() || unique_ids.front() < 0 || unique_ids.back() >= 1'000'000) {
        throw std::runtime_error("selected document ID is outside the corpus");
    }

    std::unordered_map<std::int64_t, std::size_t> unique_positions;
    unique_positions.reserve(unique_ids.size());
    for (std::size_t i = 0; i < unique_ids.size(); ++i) {
        unique_positions.emplace(unique_ids[i], i);
    }
    std::vector<std::size_t> occurrence_positions(selected_ids.size());
    for (std::size_t i = 0; i < selected_ids.size(); ++i) {
        occurrence_positions[i] = unique_positions.at(selected_ids[i]);
    }

    const auto load_started = Clock::now();
    std::vector<float> documents(unique_ids.size() * kDimensions);
    std::ifstream document_input(documents_path, std::ios::binary);
    for (std::size_t row = 0; row < unique_ids.size(); ++row) {
        const auto byte_offset = static_cast<std::uint64_t>(unique_ids[row]) * kDimensions * sizeof(float);
        document_input.seekg(static_cast<std::streamoff>(byte_offset), std::ios::beg);
        document_input.read(
            reinterpret_cast<char*>(documents.data() + row * kDimensions),
            static_cast<std::streamsize>(kDimensions * sizeof(float))
        );
        if (!document_input) {
            throw std::runtime_error("failed to read selected document row");
        }
    }
    const double selected_document_load_seconds =
        std::chrono::duration<double>(Clock::now() - load_started).count();

    const auto rotation_started = Clock::now();
    rabitqlib::rotator_impl::MatrixRotator<float> rotator(kDimensions, kDimensions);
    rotator.load(reinterpret_cast<const char*>(rotation.data()));
    std::vector<float> rotated_documents(documents.size());
    std::vector<float> rotated_queries(queries.size());
    for (std::size_t row = 0; row < unique_ids.size(); ++row) {
        auto* source = documents.data() + row * kDimensions;
        normalize(source, kDimensions);
        rotator.rotate(source, rotated_documents.data() + row * kDimensions);
    }
    for (std::size_t row = 0; row < kQueryCount; ++row) {
        auto* source = queries.data() + row * kDimensions;
        normalize(source, kDimensions);
        rotator.rotate(source, rotated_queries.data() + row * kDimensions);
    }
    const double normalization_rotation_seconds =
        std::chrono::duration<double>(Clock::now() - rotation_started).count();

    std::filesystem::create_directories(output_dir);
    write_all(output_dir / "unique-ids.i64", unique_ids);
    write_all(output_dir / "rotated-queries.f32", rotated_queries);

    std::vector<WidthResult> results;
    const std::vector<float> zero_centroid(kDimensions, 0.0F);
    for (const int bits : widths) {
        const std::size_t ex_bits = static_cast<std::size_t>(bits - 1);
        const std::size_t bin_bytes = rabitqlib::BinDataMap<float>::data_bytes(kDimensions);
        const std::size_t ex_bytes = rabitqlib::ExDataMap<float>::data_bytes(kDimensions, ex_bits);
        std::vector<std::uint8_t> full_codes(unique_ids.size() * kDimensions);
        std::vector<float> full_factors(unique_ids.size() * 3);
        std::vector<char> bin_data(unique_ids.size() * bin_bytes);
        std::vector<char> ex_data(unique_ids.size() * ex_bytes);

        const auto full_encode_started = Clock::now();
        for (std::size_t row = 0; row < unique_ids.size(); ++row) {
            const auto* vector = rotated_documents.data() + row * kDimensions;
            auto* factors = full_factors.data() + row * 3;
            rabitqlib::quant::quantize_full_single<float, std::uint8_t>(
                vector,
                kDimensions,
                static_cast<std::size_t>(bits),
                full_codes.data() + row * kDimensions,
                factors[0],
                factors[1],
                factors[2],
                rabitqlib::METRIC_IP
            );
        }
        const double full_audit_encode_seconds =
            std::chrono::duration<double>(Clock::now() - full_encode_started).count();
        const auto compact_encode_started = Clock::now();
        for (std::size_t row = 0; row < unique_ids.size(); ++row) {
            const auto* vector = rotated_documents.data() + row * kDimensions;
            rabitqlib::quant::quantize_split_single(
                vector,
                zero_centroid.data(),
                kDimensions,
                ex_bits,
                bin_data.data() + row * bin_bytes,
                ex_data.data() + row * ex_bytes,
                rabitqlib::METRIC_IP
            );
        }
        const double compact_encode_seconds =
            std::chrono::duration<double>(Clock::now() - compact_encode_started).count();

        std::vector<float> full_scores(selected_ids.size());
        std::vector<float> compact_scores(selected_ids.size());
        double max_score_difference = 0.0;
        const auto score_started = Clock::now();
        const auto* full_ip = &rabitqlib::excode_ipimpl::ip_fxi<float, std::uint8_t>;
        const auto ex_ip = rabitqlib::select_excode_ipfunc(ex_bits);
        rabitqlib::quant::RabitqConfig query_config;
        for (std::size_t query_index = 0; query_index < kQueryCount; ++query_index) {
            const auto* query = rotated_queries.data() + query_index * kDimensions;
            const float query_sum = std::accumulate(query, query + kDimensions, 0.0F);
            const float k1xsumq = -0.5F * query_sum;
            rabitqlib::SplitSingleQuery<float> split_query(
                query, kDimensions, ex_bits, query_config, rabitqlib::METRIC_IP
            );
            split_query.set_g_add(1.0F, 0.0F);
            for (std::size_t rank = 0; rank < kCandidatesPerQuery; ++rank) {
                const std::size_t occurrence = query_index * kCandidatesPerQuery + rank;
                const std::size_t row = occurrence_positions[occurrence];
                const auto* factors = full_factors.data() + row * 3;
                const float full_distance = rabitqlib::quant::full_est_dist(
                    full_codes.data() + row * kDimensions,
                    query,
                    full_ip,
                    kDimensions,
                    static_cast<std::size_t>(bits),
                    factors[0],
                    factors[1],
                    0.0F,
                    k1xsumq
                );
                float compact_distance = 0.0F;
                float low_distance = 0.0F;
                float binary_inner_product = 0.0F;
                rabitqlib::split_single_fulldist(
                    bin_data.data() + row * bin_bytes,
                    ex_data.data() + row * ex_bytes,
                    ex_ip,
                    split_query,
                    kDimensions,
                    ex_bits,
                    compact_distance,
                    low_distance,
                    binary_inner_product,
                    0.0F,
                    1.0F
                );
                full_scores[occurrence] = 1.0F - full_distance;
                compact_scores[occurrence] = 1.0F - compact_distance;
                max_score_difference = std::max(
                    max_score_difference,
                    std::abs(static_cast<double>(full_distance) - static_cast<double>(compact_distance))
                );
            }
        }
        const double score_seconds = std::chrono::duration<double>(Clock::now() - score_started).count();

        const std::string prefix = "rabitq-b" + std::to_string(bits);
        write_all(output_dir / (prefix + ".full-codes.u8"), full_codes);
        write_all(output_dir / (prefix + ".full-factors.f32"), full_factors);
        write_all(output_dir / (prefix + ".bin-data.bin"), bin_data);
        write_all(output_dir / (prefix + ".ex-data.bin"), ex_data);
        write_all(output_dir / (prefix + ".full-scores.f32"), full_scores);
        write_all(output_dir / (prefix + ".compact-scores.f32"), compact_scores);
        results.push_back(
            {bits,
             bin_bytes,
             ex_bytes,
             full_audit_encode_seconds,
             compact_encode_seconds,
             score_seconds,
             max_score_difference}
        );
    }

    std::ofstream metadata(metadata_path, std::ios::trunc);
    metadata << std::setprecision(17);
    metadata << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"compiler\": \"" << kCompiler << "\",\n"
             << "  \"build_type\": \"" << AM_RABITQ_BUILD_TYPE << "\",\n"
             << "  \"native_optimization\": false,\n"
             << "  \"dimensions\": " << kDimensions << ",\n"
             << "  \"query_count\": " << kQueryCount << ",\n"
             << "  \"candidates_per_query\": " << kCandidatesPerQuery << ",\n"
             << "  \"unique_documents\": " << unique_ids.size() << ",\n"
             << "  \"selected_document_load_seconds\": " << selected_document_load_seconds << ",\n"
             << "  \"normalization_rotation_seconds\": " << normalization_rotation_seconds << ",\n"
             << "  \"widths\": [\n";
    for (std::size_t i = 0; i < results.size(); ++i) {
        const auto& result = results[i];
        metadata << "    {\"bits\": " << result.bits
                 << ", \"bin_bytes\": " << result.bin_bytes
                 << ", \"ex_bytes\": " << result.ex_bytes
                 << ", \"logical_side_bytes\": " << (result.bin_bytes + result.ex_bytes)
                 << ", \"full_audit_encode_seconds\": " << result.full_audit_encode_seconds
                 << ", \"compact_encode_seconds\": " << result.compact_encode_seconds
                 << ", \"score_seconds\": " << result.score_seconds
                 << ", \"full_compact_max_abs_score_difference\": " << result.max_score_difference
                 << "}" << (i + 1 == results.size() ? "\n" : ",\n");
    }
    metadata << "  ]\n}\n";
    if (!metadata) {
        throw std::runtime_error("failed to write native metadata");
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "official RaBitQ runner failed: " << error.what() << '\n';
        return 1;
    }
}
