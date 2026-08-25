#include <hnswlib/hnswlib.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

constexpr std::size_t kDimension = 384U;
constexpr std::size_t kBinaryBits = 512U;
constexpr std::size_t kBinaryBytes = kBinaryBits / 8U;

enum class Method {
    Fp32,
    Fp16,
    Int8,
    SymmetricHamming,
    AsymmetricSignDot,
    Hnsw,
};

struct Input {
    std::size_t m_document_count = 0U;
    std::size_t m_centroid_count = 0U;
    std::size_t m_query_count = 0U;
    std::vector<float> m_centroids;
    std::vector<float> m_queries;
    std::vector<std::uint32_t> m_assignments;
    std::vector<std::uint8_t> m_centroid_codes;
    std::vector<std::uint8_t> m_query_codes;
    std::vector<float> m_query_projection;
    std::vector<std::vector<std::uint32_t>> m_lists;
};

struct QuantizedCentroids {
    std::vector<std::uint16_t> m_fp16;
    std::vector<std::int8_t> m_int8;
    std::vector<float> m_scales;
};

struct Selection {
    bool m_feasible = false;
    std::size_t m_document_count = 0U;
    std::vector<bool> m_centroids;
};

struct Quality {
    double m_selected_centroid_recall = 0.0;
    double m_teacher_document_overlap = 0.0;
    double m_actual_candidate_fraction = 0.0;
};

template <class T>
std::vector<T> read_binary(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("cannot open " + path.string());
    }
    stream.seekg(0, std::ios::end);
    const auto bytes = static_cast<std::size_t>(stream.tellg());
    stream.seekg(0);
    if (bytes % sizeof(T) != 0U) {
        throw std::runtime_error("invalid payload width: " + path.string());
    }
    std::vector<T> values(bytes / sizeof(T));
    stream.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(bytes));
    if (!stream) {
        throw std::runtime_error("cannot read " + path.string());
    }
    return values;
}

float dot(const float* left, const float* right, const std::size_t count) {
    float result = 0.0F;
    for (std::size_t index = 0U; index < count; ++index) {
        result += left[index] * right[index];
    }
    return result;
}

unsigned popcount8(std::uint8_t value) {
    unsigned result = 0U;
    while (value != 0U) {
        value = static_cast<std::uint8_t>(value & static_cast<std::uint8_t>(value - 1U));
        ++result;
    }
    return result;
}

std::uint16_t fp16(const float value) {
    union {
        float m_floating;
        std::uint32_t m_integer;
    } bits{value};
    const auto sign = (bits.m_integer >> 16U) & 0x8000U;
    auto exponent = static_cast<int>((bits.m_integer >> 23U) & 0xffU) - 127 + 15;
    auto mantissa = bits.m_integer & 0x7fffffU;
    if (exponent <= 0) {
        return static_cast<std::uint16_t>(sign);
    }
    if (exponent >= 31) {
        return static_cast<std::uint16_t>(sign | 0x7c00U);
    }
    mantissa = (mantissa + 0x1000U) >> 13U;
    if (mantissa == 0x400U) {
        mantissa = 0U;
        ++exponent;
    }
    if (exponent >= 31) {
        return static_cast<std::uint16_t>(sign | 0x7c00U);
    }
    return static_cast<std::uint16_t>(
        sign | (static_cast<std::uint32_t>(exponent) << 10U) | mantissa);
}

float from_fp16(const std::uint16_t value) {
    const auto sign = (value & 0x8000U) == 0U ? 1.0F : -1.0F;
    const auto exponent = static_cast<int>((value >> 10U) & 0x1fU);
    const auto mantissa = static_cast<float>(value & 0x3ffU) / 1024.0F;
    return exponent == 0 ? sign * std::ldexp(mantissa, -14) :
        sign * std::ldexp(1.0F + mantissa, exponent - 15);
}

std::vector<std::uint32_t> rank_scores(const std::vector<float>& scores, const std::size_t limit) {
    std::vector<std::uint32_t> positions(scores.size());
    std::iota(positions.begin(), positions.end(), 0U);
    const auto count = std::min(limit, positions.size());
    const auto closer = [&scores](const std::uint32_t left, const std::uint32_t right) {
        return scores[left] == scores[right] ? left < right : scores[left] > scores[right];
    };
    if (count < positions.size()) {
        std::partial_sort(positions.begin(), positions.begin() + static_cast<std::ptrdiff_t>(count),
            positions.end(), closer);
        positions.resize(count);
    } else {
        std::sort(positions.begin(), positions.end(), closer);
    }
    return positions;
}

double percentile(std::vector<double> samples, const double fraction) {
    std::sort(samples.begin(), samples.end());
    const auto position = static_cast<std::size_t>(
        std::floor(fraction * static_cast<double>(samples.size() - 1U)));
    return samples[position];
}

Input load_input(const std::filesystem::path& root) {
    std::ifstream manifest_stream(root / "manifest.json");
    if (!manifest_stream) {
        throw std::runtime_error("native centroid materialization manifest missing");
    }
    nlohmann::json manifest;
    manifest_stream >> manifest;
    if (manifest.value("schema_version", 0U) != 1U ||
        manifest.value("family", "") != "native_centroid_routing_materialization_v1" ||
        manifest.value("dimension", 0U) != kDimension) {
        throw std::runtime_error("native centroid materialization manifest differs");
    }

    Input input;
    input.m_document_count = manifest.at("documents").get<std::size_t>();
    input.m_centroid_count = manifest.at("centroid_count").get<std::size_t>();
    input.m_query_count = manifest.at("query_count").get<std::size_t>();
    const auto& outputs = manifest.at("outputs");
    const auto read = [&root, &outputs](const char* name) {
        return root / outputs.at(name).at("path").get<std::string>();
    };
    input.m_centroids = read_binary<float>(read("centroids"));
    input.m_queries = read_binary<float>(read("queries"));
    input.m_assignments = read_binary<std::uint32_t>(read("assignments"));
    input.m_centroid_codes = read_binary<std::uint8_t>(read("centroid_codes"));
    input.m_query_codes = read_binary<std::uint8_t>(read("query_codes"));
    input.m_query_projection = read_binary<float>(read("query_projection"));
    if (input.m_centroids.size() != input.m_centroid_count * kDimension ||
        input.m_queries.size() != input.m_query_count * kDimension ||
        input.m_assignments.size() != input.m_document_count ||
        input.m_centroid_codes.size() != input.m_centroid_count * kBinaryBytes ||
        input.m_query_codes.size() != input.m_query_count * kBinaryBytes ||
        input.m_query_projection.size() != input.m_query_count * kBinaryBits) {
        throw std::runtime_error("native centroid materialization payload shape differs");
    }
    input.m_lists.resize(input.m_centroid_count);
    for (std::size_t document = 0U; document < input.m_assignments.size(); ++document) {
        const auto centroid = input.m_assignments[document];
        if (centroid >= input.m_centroid_count) {
            throw std::runtime_error("native centroid assignment differs");
        }
        input.m_lists[centroid].push_back(static_cast<std::uint32_t>(document));
    }
    return input;
}

QuantizedCentroids quantize(const Input& input) {
    QuantizedCentroids result;
    result.m_fp16.resize(input.m_centroids.size());
    result.m_int8.resize(input.m_centroids.size());
    result.m_scales.resize(input.m_centroid_count);
    for (std::size_t centroid = 0U; centroid < input.m_centroid_count; ++centroid) {
        const auto offset = centroid * kDimension;
        float maximum = 0.0F;
        for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
            const auto value = input.m_centroids[offset + dimension];
            result.m_fp16[offset + dimension] = fp16(value);
            maximum = std::max(maximum, std::abs(value));
        }
        result.m_scales[centroid] = maximum == 0.0F ? 1.0F : maximum / 127.0F;
        for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
            result.m_int8[offset + dimension] = static_cast<std::int8_t>(std::round(
                input.m_centroids[offset + dimension] / result.m_scales[centroid]));
        }
    }
    return result;
}

std::vector<float> score_all(const Input& input, const QuantizedCentroids& quantized,
    const std::size_t query, const Method method) {
    std::vector<float> scores(input.m_centroid_count);
    const auto query_offset = query * kDimension;
    for (std::size_t centroid = 0U; centroid < input.m_centroid_count; ++centroid) {
        const auto centroid_offset = centroid * kDimension;
        if (method == Method::Fp32) {
            scores[centroid] = dot(input.m_centroids.data() + centroid_offset,
                input.m_queries.data() + query_offset, kDimension);
        } else if (method == Method::Fp16) {
            for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
                scores[centroid] += from_fp16(quantized.m_fp16[centroid_offset + dimension]) *
                    input.m_queries[query_offset + dimension];
            }
        } else if (method == Method::Int8) {
            for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
                scores[centroid] += static_cast<float>(quantized.m_int8[centroid_offset + dimension]) *
                    quantized.m_scales[centroid] * input.m_queries[query_offset + dimension];
            }
        } else if (method == Method::SymmetricHamming) {
            const auto centroid_code_offset = centroid * kBinaryBytes;
            const auto query_code_offset = query * kBinaryBytes;
            for (std::size_t byte = 0U; byte < kBinaryBytes; ++byte) {
                scores[centroid] -= static_cast<float>(popcount8(static_cast<std::uint8_t>(
                    input.m_centroid_codes[centroid_code_offset + byte] ^
                    input.m_query_codes[query_code_offset + byte])));
            }
        } else if (method == Method::AsymmetricSignDot) {
            const auto centroid_code_offset = centroid * kBinaryBytes;
            const auto query_projection_offset = query * kBinaryBits;
            for (std::size_t bit = 0U; bit < kBinaryBits; ++bit) {
                const auto code = input.m_centroid_codes[centroid_code_offset + bit / 8U];
                const auto sign = ((code >> (bit % 8U)) & 1U) == 0U ? -1.0F : 1.0F;
                scores[centroid] += input.m_query_projection[query_projection_offset + bit] * sign;
            }
        }
    }
    return scores;
}

std::vector<std::uint32_t> exact_rerank(const Input& input, const std::size_t query,
    const std::vector<std::uint32_t>& shortlist) {
    std::vector<float> scores(shortlist.size());
    const auto query_offset = query * kDimension;
    for (std::size_t index = 0U; index < shortlist.size(); ++index) {
        scores[index] = dot(input.m_centroids.data() + static_cast<std::size_t>(shortlist[index]) * kDimension,
            input.m_queries.data() + query_offset, kDimension);
    }
    auto order = rank_scores(scores, scores.size());
    std::vector<std::uint32_t> result(order.size());
    for (std::size_t index = 0U; index < order.size(); ++index) {
        result[index] = shortlist[order[index]];
    }
    return result;
}

Selection select_mass(const Input& input, const std::vector<std::uint32_t>& ranked,
    const double target_fraction) {
    Selection result;
    result.m_centroids.assign(input.m_centroid_count, false);
    const auto target = static_cast<std::size_t>(std::ceil(
        target_fraction * static_cast<double>(input.m_document_count)));
    for (const auto centroid : ranked) {
        result.m_centroids[centroid] = true;
        result.m_document_count += input.m_lists[centroid].size();
        if (result.m_document_count >= target) {
            result.m_feasible = true;
            break;
        }
    }
    return result;
}

Quality compare(const Input& input, const Selection& oracle, const Selection& current) {
    Quality result;
    std::size_t oracle_centroids = 0U;
    std::size_t matching_centroids = 0U;
    std::size_t matching_documents = 0U;
    for (std::size_t centroid = 0U; centroid < input.m_centroid_count; ++centroid) {
        if (oracle.m_centroids[centroid]) {
            ++oracle_centroids;
        }
        if (oracle.m_centroids[centroid] && current.m_centroids[centroid]) {
            ++matching_centroids;
            matching_documents += input.m_lists[centroid].size();
        }
    }
    result.m_selected_centroid_recall = static_cast<double>(matching_centroids) /
        static_cast<double>(oracle_centroids);
    result.m_teacher_document_overlap = static_cast<double>(matching_documents) /
        static_cast<double>(oracle.m_document_count);
    result.m_actual_candidate_fraction = static_cast<double>(current.m_document_count) /
        static_cast<double>(input.m_document_count);
    return result;
}

class HnswIndex final {
public:
    HnswIndex(const Input& input, const std::size_t connectivity, const std::size_t ef_construction)
        : m_space(kDimension),
          m_index(&m_space, input.m_centroid_count, connectivity, ef_construction, 20260825U) {
        for (std::size_t centroid = 0U; centroid < input.m_centroid_count; ++centroid) {
            m_index.addPoint(input.m_centroids.data() + centroid * kDimension, centroid);
        }
    }

    std::vector<std::uint32_t> search(const float* query, const std::size_t limit,
        const std::size_t ef_search) {
        m_index.setEf(ef_search);
        auto matches = m_index.searchKnn(query, limit);
        std::vector<std::uint32_t> result(matches.size());
        for (std::size_t index = matches.size(); index > 0U; --index) {
            result[index - 1U] = static_cast<std::uint32_t>(matches.top().second);
            matches.pop();
        }
        return result;
    }

    [[nodiscard]] std::size_t bytes() const {
        return m_index.indexFileSize();
    }

private:
    hnswlib::InnerProductSpace m_space;
    hnswlib::HierarchicalNSW<float> m_index;
};

std::string method_name(const Method method) {
    switch (method) {
    case Method::Fp32: return "exact_fp32_scan";
    case Method::Fp16: return "fp16_centroids_fp32_accumulation";
    case Method::Int8: return "int8_per_centroid_symmetric_quantized_scan";
    case Method::SymmetricHamming: return "rademacher512_symmetric_hamming_then_exact_fp32_rerank";
    case Method::AsymmetricSignDot: return "rademacher512_asymmetric_sign_dot_then_exact_fp32_rerank";
    case Method::Hnsw: return "hnsw_fp32_inner_product_then_exact_fp32_rerank";
    }
    throw std::runtime_error("native centroid method differs");
}

nlohmann::json run_row(const Input& input, const QuantizedCentroids& quantized,
    const std::vector<Selection>& oracle, const Method method, const double fraction,
    const std::size_t warmups, const std::size_t repeats, const std::size_t multiplier,
    const std::size_t hnsw_connectivity, const std::size_t hnsw_ef_search, HnswIndex* hnsw) {
    const auto needs_rerank = method == Method::SymmetricHamming || method == Method::AsymmetricSignDot ||
        method == Method::Hnsw;
    const auto requested_shortlist = needs_rerank ? std::min(input.m_centroid_count,
        static_cast<std::size_t>(std::ceil(static_cast<double>(multiplier) * fraction *
            static_cast<double>(input.m_centroid_count)))) : input.m_centroid_count;
    const bool feasible = method != Method::Hnsw || hnsw_ef_search >= requested_shortlist;
    nlohmann::json row{
        {"treatment", method_name(method)},
        {"target_candidate_fraction", fraction},
        {"shortlist_multiplier", needs_rerank ? nlohmann::json(multiplier) : nlohmann::json(nullptr)},
        {"hnsw_connectivity", method == Method::Hnsw ? nlohmann::json(hnsw_connectivity) : nlohmann::json(nullptr)},
        {"hnsw_ef_search", method == Method::Hnsw ? nlohmann::json(hnsw_ef_search) : nlohmann::json(nullptr)},
        {"requested_centroid_shortlist", requested_shortlist},
        {"target_mass_feasible", feasible},
    };
    if (!feasible) {
        row["raw_timing_samples_ms_per_query"] = nlohmann::json::array();
        return row;
    }

    std::vector<double> samples;
    Quality total;
    bool all_queries_feasible = true;
    std::size_t feasible_query_count = 0U;
    for (std::size_t pass = 0U; pass < warmups + repeats; ++pass) {
        Quality current;
        std::size_t current_feasible_query_count = 0U;
        const auto begin = Clock::now();
        for (std::size_t query = 0U; query < input.m_query_count; ++query) {
            std::vector<std::uint32_t> ranked;
            if (method == Method::Hnsw) {
                ranked = exact_rerank(input, query, hnsw->search(input.m_queries.data() + query * kDimension,
                    requested_shortlist, hnsw_ef_search));
            } else {
                ranked = rank_scores(score_all(input, quantized, query, method), requested_shortlist);
                if (needs_rerank) {
                    ranked = exact_rerank(input, query, ranked);
                }
            }
            const auto selected = select_mass(input, ranked, fraction);
            if (!selected.m_feasible) {
                all_queries_feasible = false;
                continue;
            }
            const auto quality = compare(input, oracle[query], selected);
            current.m_selected_centroid_recall += quality.m_selected_centroid_recall;
            current.m_teacher_document_overlap += quality.m_teacher_document_overlap;
            current.m_actual_candidate_fraction += quality.m_actual_candidate_fraction;
            ++current_feasible_query_count;
        }
        const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count() /
            static_cast<double>(input.m_query_count);
        if (pass >= warmups) {
            samples.push_back(elapsed);
            total = current;
            feasible_query_count = current_feasible_query_count;
        }
    }
    row["target_mass_feasible"] = all_queries_feasible;
    row["feasible_query_count"] = feasible_query_count;
    row["raw_timing_samples_ms_per_query"] = samples;
    row["routing_p50_ms_per_query"] = percentile(samples, 0.50);
    row["routing_p95_ms_per_query"] = percentile(samples, 0.95);
    if (!all_queries_feasible) {
        return row;
    }
    row["exact_float_selected_centroid_recall_at_matched_candidate_mass"] =
        total.m_selected_centroid_recall / static_cast<double>(input.m_query_count);
    row["teacher_candidate_document_overlap_at_matched_candidate_mass"] =
        total.m_teacher_document_overlap / static_cast<double>(input.m_query_count);
    row["actual_candidate_fraction"] = total.m_actual_candidate_fraction /
        static_cast<double>(input.m_query_count);
    return row;
}

int run(const std::filesystem::path& root, const std::filesystem::path& report,
    const std::size_t warmups, const std::size_t repeats) {
    const auto input = load_input(root);
    const auto quantized = quantize(input);
    const std::vector<double> fractions{0.05, 0.10, 0.25};
    std::vector<std::vector<Selection>> oracle(input.m_query_count,
        std::vector<Selection>(fractions.size()));
    for (std::size_t query = 0U; query < input.m_query_count; ++query) {
        const auto ranked = rank_scores(score_all(input, quantized, query, Method::Fp32), input.m_centroid_count);
        for (std::size_t fraction = 0U; fraction < fractions.size(); ++fraction) {
            oracle[query][fraction] = select_mass(input, ranked, fractions[fraction]);
        }
    }

    const auto oracle_for_fraction = [&oracle, &input](const std::size_t fraction) {
        std::vector<Selection> result(input.m_query_count);
        for (std::size_t query = 0U; query < input.m_query_count; ++query) {
            result[query] = oracle[query][fraction];
        }
        return result;
    };

    nlohmann::json rows = nlohmann::json::array();
    const std::vector<Method> scan_methods{Method::Fp32, Method::Fp16, Method::Int8,
        Method::SymmetricHamming, Method::AsymmetricSignDot};
    for (std::size_t fraction = 0U; fraction < fractions.size(); ++fraction) {
        const auto teacher = oracle_for_fraction(fraction);
        for (const auto method : scan_methods) {
            const auto multipliers = method == Method::SymmetricHamming || method == Method::AsymmetricSignDot ?
                std::vector<std::size_t>{2U, 4U} : std::vector<std::size_t>{1U};
            for (const auto multiplier : multipliers) {
                rows.push_back(run_row(input, quantized, teacher, method, fractions[fraction], warmups,
                    repeats, multiplier, 0U, 0U, nullptr));
            }
        }
    }

    for (const auto connectivity : {8U, 16U}) {
        HnswIndex hnsw(input, connectivity, 200U);
        for (std::size_t fraction = 0U; fraction < fractions.size(); ++fraction) {
            const auto teacher = oracle_for_fraction(fraction);
            for (const auto ef_search : {256U, 512U, 2048U, 8192U}) {
                auto row = run_row(input, quantized, teacher, Method::Hnsw, fractions[fraction], warmups,
                    repeats, 2U, connectivity, ef_search, &hnsw);
                row["index_bytes"] = hnsw.bytes();
                rows.push_back(std::move(row));
            }
        }
    }

    const nlohmann::json output{
        {"schema_version", 1},
        {"family", "native_centroid_routing_calibration_raw_v1"},
        {"documents", input.m_document_count},
        {"centroid_count", input.m_centroid_count},
        {"query_count", input.m_query_count},
        {"warmup_repeats", warmups},
        {"measured_repeats", repeats},
        {"concurrency", "single_native_routing_thread_v1"},
        {"hnswlib_revision", AGENT_MEMORY_HNSWLIB_REVISION},
        {"centroid_payload_bytes", {
            {"fp32", input.m_centroid_count * kDimension * sizeof(float)},
            {"fp16", input.m_centroid_count * kDimension * sizeof(std::uint16_t)},
            {"int8_per_centroid_scale", input.m_centroid_count * kDimension * sizeof(std::int8_t) +
                input.m_centroid_count * sizeof(float)},
            {"rademacher512", input.m_centroid_count * kBinaryBytes},
        }},
        {"rows", rows},
    };
    std::ofstream stream(report);
    if (!stream) {
        throw std::runtime_error("cannot write native centroid routing report");
    }
    stream << output.dump(2) << '\n';
    return 0;
}

void self_test() {
    if (popcount8(0b11010010U) != 4U || std::abs(from_fp16(fp16(0.25F)) - 0.25F) > 0.0001F) {
        throw std::runtime_error("native centroid routing primitive self-test differs");
    }
    const auto ranked = rank_scores({0.25F, 0.75F, 0.75F}, 3U);
    if (ranked != std::vector<std::uint32_t>{1U, 2U, 0U}) {
        throw std::runtime_error("native centroid routing ordering self-test differs");
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            self_test();
            std::cout << "native centroid routing self-test passed\n";
            return 0;
        }
        if (argc != 5) {
            std::cerr << "usage: agent-memory-native-centroid-routing <materialization-dir> <report.json> <warmups> <repeats>\n";
            return 2;
        }
        return run(argv[1], argv[2], static_cast<std::size_t>(std::stoul(argv[3])),
            static_cast<std::size_t>(std::stoul(argv[4])));
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
