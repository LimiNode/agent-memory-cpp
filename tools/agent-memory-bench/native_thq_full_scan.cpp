#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

namespace {
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr std::size_t dimension = 384;

template<class T> std::vector<T> read_file(const std::string& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if(!stream) throw std::runtime_error("cannot open " + path);
    const auto size = stream.tellg();
    if(size < 0 || static_cast<std::size_t>(size) % sizeof(T) != 0)
        throw std::runtime_error("payload size differs: " + path);
    std::vector<T> values(static_cast<std::size_t>(size) / sizeof(T));
    stream.seekg(0);
    stream.read(reinterpret_cast<char*>(values.data()), size);
    if(!stream) throw std::runtime_error("cannot read " + path);
    return values;
}

unsigned popcount64(std::uint64_t value) {
#if defined(_MSC_VER) && (defined(_M_X64) || defined(_M_AMD64))
    return static_cast<unsigned>(__popcnt64(value));
#elif defined(__GNUC__) || defined(__clang__)
    return static_cast<unsigned>(__builtin_popcountll(value));
#else
    unsigned count = 0;
    while(value) { value &= value - 1; ++count; }
    return count;
#endif
}

std::uint16_t hamming(const std::uint8_t* left, const std::uint8_t* right,
                      std::size_t bytes) {
    std::uint16_t result = 0;
    for(std::size_t offset = 0; offset < bytes; offset += 8) {
        std::uint64_t a = 0, b = 0;
        std::memcpy(&a, left + offset, 8);
        std::memcpy(&b, right + offset, 8);
        result += static_cast<std::uint16_t>(popcount64(a ^ b));
    }
    return result;
}

float dot(const float* left, const float* right) {
    float result = 0.0F;
    for(std::size_t i = 0; i < dimension; ++i) result += left[i] * right[i];
    return result;
}

template<class Score, class Better>
std::vector<std::uint32_t> top(const std::vector<Score>& scores, std::size_t count,
                               Better better) {
    std::vector<std::uint32_t> ids(scores.size());
    std::iota(ids.begin(), ids.end(), 0U);
    count = std::min(count, ids.size());
    std::partial_sort(ids.begin(), ids.begin() + count, ids.end(),
        [&](std::uint32_t a, std::uint32_t b) {
            if(scores[a] == scores[b]) return a < b;
            return better(scores[a], scores[b]);
        });
    ids.resize(count);
    return ids;
}

double overlap(const std::vector<std::uint32_t>& selected,
               const std::vector<std::int64_t>& teacher, std::size_t query) {
    std::size_t hits = 0;
    for(const auto document : selected)
        for(std::size_t rank = 0; rank < 10; ++rank)
            if(teacher[query * 10 + rank] == document) ++hits;
    return static_cast<double>(hits) / 10.0;
}

double ndcg(const std::vector<std::uint32_t>& selected,
            const std::vector<std::int64_t>& qrels,
            const std::vector<float>& relevance, std::size_t query) {
    std::vector<float> ideal;
    for(std::size_t rank = 0; rank < 20; ++rank)
        if(qrels[query * 20 + rank] >= 0) ideal.push_back(relevance[query * 20 + rank]);
    std::sort(ideal.rbegin(), ideal.rend());
    double numerator = 0.0, denominator = 0.0;
    for(std::size_t rank = 0; rank < std::min<std::size_t>(10, ideal.size()); ++rank)
        denominator += (std::pow(2.0, ideal[rank]) - 1.0) / std::log2(rank + 2.0);
    for(std::size_t rank = 0; rank < std::min<std::size_t>(10, selected.size()); ++rank) {
        float score = 0.0F;
        for(std::size_t qrel = 0; qrel < 20; ++qrel)
            if(qrels[query * 20 + qrel] == selected[rank]) score = relevance[query * 20 + qrel];
        numerator += (std::pow(2.0, score) - 1.0) / std::log2(rank + 2.0);
    }
    return denominator > 0.0 ? numerator / denominator : 0.0;
}

double percentile(std::vector<double> values, double fraction) {
    std::sort(values.begin(), values.end());
    return values[static_cast<std::size_t>(fraction * (values.size() - 1))];
}

struct Input {
    std::size_t documents = 0, queries = 0;
    std::vector<float> vectors, query_vectors, qrel_scores;
    std::vector<std::int64_t> teacher, qrels;
    std::vector<std::uint8_t> thq_codes, thq_queries, itq_codes, itq_queries;
};

Input load(const std::string& manifest_path) {
    std::ifstream stream(manifest_path); json m; stream >> m;
    if(m.value("family", "") != "thq_full_scan_materialization_v1")
        throw std::runtime_error("THQ full-scan manifest family differs");
    const auto path = [](const json& row) { return row.at("path").get<std::string>(); };
    Input input; input.documents = m.at("documents"); input.queries = m.at("queries");
    input.vectors = read_file<float>(path(m["references"]["document_vectors"]));
    input.query_vectors = read_file<float>(path(m["references"]["queries"]));
    input.teacher = read_file<std::int64_t>(path(m["references"]["teacher_ids"]));
    input.qrels = read_file<std::int64_t>(path(m["references"]["qrel_ids"]));
    input.qrel_scores = read_file<float>(path(m["references"]["qrel_scores"]));
    input.thq_codes = read_file<std::uint8_t>(path(m["outputs"]["document_codes"]));
    input.thq_queries = read_file<std::uint8_t>(path(m["outputs"]["query_codes"]));
    input.itq_codes = read_file<std::uint8_t>(path(m["references"]["itq_document_codes"]));
    input.itq_queries = read_file<std::uint8_t>(path(m["references"]["itq_query_codes"]));
    if(input.vectors.size() != input.documents * dimension ||
       input.query_vectors.size() != input.queries * dimension ||
       input.teacher.size() != input.queries * 10 || input.qrels.size() != input.queries * 20)
        throw std::runtime_error("THQ full-scan payload shape differs");
    return input;
}

} // namespace

int main(int argc, char** argv) {
    try {
        if(argc < 3 || argc > 4) {
            std::cerr << "usage: native_thq_full_scan <manifest> <output> [query_limit]\n";
            return 2;
        }
        auto input = load(argv[1]);
        if(argc == 4) input.queries = std::min(input.queries,
            static_cast<std::size_t>(std::stoul(argv[3])));
        json report{{"schema_version", 1}, {"family", "native_thq_full_scan_result_v1"},
                    {"query_count", input.queries}, {"rows", json::array()}};
        for(const auto codec : {std::string("itq256_hamming"), std::string("thq4_quantile")}) {
            const auto& codes = codec[0] == 'i' ? input.itq_codes : input.thq_codes;
            const auto& queries = codec[0] == 'i' ? input.itq_queries : input.thq_queries;
            const std::size_t bytes = codec[0] == 'i' ? 32 : 144;
            std::vector<std::vector<double>> overlaps(4), ndcgs(4), scan_ms(4),
                top_ms(4), exact_ms(4), total_ms(4);
            const std::array<unsigned, 4> limits{{256, 512, 768, 1024}};
            for(auto& values : {&overlaps, &ndcgs, &scan_ms, &top_ms, &exact_ms, &total_ms})
                for(auto& row : *values) row.reserve(input.queries);
            for(std::size_t query = 0; query < input.queries; ++query) {
                const auto begin = Clock::now();
                std::vector<std::uint16_t> distances(input.documents);
                for(std::size_t document = 0; document < input.documents; ++document)
                    distances[document] = hamming(codes.data() + document * bytes,
                        queries.data() + query * bytes, bytes);
                const auto scanned = Clock::now();
                const auto scan_duration = std::chrono::duration<double, std::milli>(scanned - begin).count();
                for(std::size_t lane = 0; lane < limits.size(); ++lane) {
                    const auto top_started = Clock::now();
                    const auto shortlist = top(distances, limits[lane],
                        [](std::uint16_t a, std::uint16_t b) { return a < b; });
                    const auto topped = Clock::now();
                    std::vector<float> scores(shortlist.size());
                    for(std::size_t i = 0; i < shortlist.size(); ++i)
                        scores[i] = dot(input.vectors.data() + shortlist[i] * dimension,
                                        input.query_vectors.data() + query * dimension);
                    const auto exact_order = top(scores, 10,
                        [](float a, float b) { return a > b; });
                    std::vector<std::uint32_t> selected(exact_order.size());
                    for(std::size_t i = 0; i < selected.size(); ++i)
                        selected[i] = shortlist[exact_order[i]];
                    const auto stop = Clock::now();
                    overlaps[lane].push_back(overlap(selected, input.teacher, query));
                    ndcgs[lane].push_back(ndcg(selected, input.qrels, input.qrel_scores, query));
                    const auto ms = [](auto a, auto b) {
                        return std::chrono::duration<double, std::milli>(b - a).count();
                    };
                    scan_ms[lane].push_back(scan_duration);
                    top_ms[lane].push_back(ms(top_started, topped));
                    exact_ms[lane].push_back(ms(topped, stop));
                    total_ms[lane].push_back(ms(begin, stop));
                }
            }
            for(std::size_t lane = 0; lane < limits.size(); ++lane) {
                const auto k = limits[lane];
                report["rows"].push_back({{"codec", codec}, {"k", k},
                    {"payload_bytes_per_document", bytes},
                    {"mean_overlap", std::accumulate(overlaps[lane].begin(), overlaps[lane].end(), 0.0) / overlaps[lane].size()},
                    {"mean_ndcg", std::accumulate(ndcgs[lane].begin(), ndcgs[lane].end(), 0.0) / ndcgs[lane].size()},
                    {"p05_overlap", percentile(overlaps[lane], .05)}, {"worst_overlap", percentile(overlaps[lane], 0.0)},
                    {"p95_scan_ms", percentile(scan_ms[lane], .95)}, {"p95_top_k_ms", percentile(top_ms[lane], .95)},
                    {"p95_exact_ms", percentile(exact_ms[lane], .95)}, {"p95_total_ms", percentile(total_ms[lane], .95)}});
            }
        }
        std::ofstream output(argv[2]); output << report.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
