#include <agent_memory/index/ExactVectorIndex.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

using agent_memory::ChunkId;
using agent_memory::Embedding;
using agent_memory::ExactVectorIndex;
using agent_memory::ExactVectorIndexOptions;
using agent_memory::SimilarityMetric;
using agent_memory::VectorRecord;
using agent_memory::VectorSearchQuery;
using agent_memory::VectorSearchResult;

struct OracleHit final {
    std::size_t row = 0;
    long double score = 0.0L;
};

struct Timing final {
    std::vector<double> milliseconds;
};

struct CaseResult final {
    std::size_t rows = 0;
    std::size_t dimension = 0;
    std::size_t queries = 0;
    std::size_t top_k = 0;
    std::string backend;
    bool parity = false;
    double max_score_error = 0.0;
    Timing kernel;
    Timing end_to_end;
};

std::vector<float> make_vector(
    std::mt19937& generator,
    const std::size_t dimension,
    const std::size_t row
) {
    std::uniform_real_distribution<float> distribution(-1.0F, 1.0F);
    std::vector<float> values(dimension);
    for(auto& value : values) {
        value = distribution(generator);
    }
    if(!values.empty()) {
        values[row % dimension] += 0.0001F;
    }
    return values;
}

long double cosine(
    const std::vector<float>& lhs,
    const std::vector<float>& rhs
) {
    long double dot = 0.0L;
    long double lhs_norm = 0.0L;
    long double rhs_norm = 0.0L;
    for(std::size_t index = 0; index < lhs.size(); ++index) {
        const auto left = static_cast<long double>(lhs[index]);
        const auto right = static_cast<long double>(rhs[index]);
        dot += left * right;
        lhs_norm += left * left;
        rhs_norm += right * right;
    }
    if(lhs_norm == 0.0L || rhs_norm == 0.0L) {
        return 0.0L;
    }
    return dot / (std::sqrt(lhs_norm) * std::sqrt(rhs_norm));
}

std::vector<OracleHit> oracle(
    const std::vector<std::vector<float>>& corpus,
    const std::vector<float>& query,
    const std::size_t limit
) {
    std::vector<OracleHit> hits;
    hits.reserve(corpus.size());
    for(std::size_t row = 0; row < corpus.size(); ++row) {
        hits.push_back(OracleHit{row, cosine(query, corpus[row])});
    }
    std::stable_sort(hits.begin(), hits.end(), [&corpus](const auto& lhs, const auto& rhs) {
        if(lhs.score != rhs.score) {
            return lhs.score > rhs.score;
        }
        return std::string("chunk:") + std::to_string(lhs.row)
            < std::string("chunk:") + std::to_string(rhs.row);
    });
    if(hits.size() > limit) {
        hits.resize(limit);
    }
    return hits;
}

std::vector<VectorSearchQuery> make_queries(
    const std::vector<std::vector<float>>& corpus,
    const std::size_t count
) {
    std::vector<VectorSearchQuery> queries;
    queries.reserve(count);
    for(std::size_t index = 0; index < count; ++index) {
        queries.push_back(VectorSearchQuery{Embedding{corpus[index % corpus.size()]}, 10, {}});
    }
    return queries;
}

template <typename Fn>
Timing measure(Fn&& fn, const std::size_t samples) {
    Timing timing;
    timing.milliseconds.reserve(samples);
    for(std::size_t sample = 0; sample < samples; ++sample) {
        const auto start = std::chrono::steady_clock::now();
        fn();
        const auto end = std::chrono::steady_clock::now();
        timing.milliseconds.push_back(std::chrono::duration<double, std::milli>(end - start).count());
    }
    return timing;
}

CaseResult run_case(
    const std::size_t rows,
    const std::size_t dimension,
    const std::size_t query_count,
    const std::size_t top_k
) {
    std::mt19937 generator(static_cast<std::uint32_t>(0x5eedU + rows));
    std::vector<std::vector<float>> corpus;
    corpus.reserve(rows);
    for(std::size_t row = 0; row < rows; ++row) {
        corpus.push_back(make_vector(generator, dimension, row));
    }
    auto queries = make_queries(corpus, query_count);
    ExactVectorIndex index(ExactVectorIndexOptions{dimension, SimilarityMetric::Cosine, true});
    for(std::size_t row = 0; row < corpus.size(); ++row) {
        index.upsert(VectorRecord{ChunkId{"chunk:" + std::to_string(row)}, Embedding{corpus[row]}, {}});
    }

    bool parity = true;
    double max_score_error = 0.0;
    volatile std::size_t sink = 0;
    for(const auto& query : queries) {
        const auto expected = oracle(corpus, query.embedding.values, top_k);
        const auto actual = index.search(VectorSearchQuery{query.embedding, top_k, {}});
        if(actual.size() != expected.size()) {
            parity = false;
            continue;
        }
        for(std::size_t position = 0; position < actual.size(); ++position) {
            const auto expected_id = "chunk:" + std::to_string(expected[position].row);
            if(actual[position].chunk_id.value() != expected_id) {
                parity = false;
            }
            max_score_error = std::max(
                max_score_error,
                static_cast<double>(std::abs(
                    static_cast<long double>(actual[position].score) - expected[position].score
                ))
            );
            sink += actual[position].chunk_id.value().size();
        }
    }
    (void)sink;

    for(std::size_t warmup = 0; warmup < 2; ++warmup) {
        for(const auto& query : queries) {
            sink += index.search(query).size();
        }
    }
    const auto kernel = measure([&] {
        for(const auto& query : queries) {
            sink += index.search(query).size();
        }
    }, 5);
    const auto end_to_end = measure([&] {
        ExactVectorIndex rebuilt(ExactVectorIndexOptions{dimension, SimilarityMetric::Cosine, true});
        for(std::size_t row = 0; row < corpus.size(); ++row) {
            rebuilt.upsert(VectorRecord{
                ChunkId{"chunk:" + std::to_string(row)},
                Embedding{corpus[row]},
                {}
            });
        }
        for(const auto& query : queries) {
            sink += rebuilt.search(query).size();
        }
    }, 5);
    (void)sink;
    return CaseResult{
        rows,
        dimension,
        query_count,
        top_k,
        std::string(agent_memory::vector_similarity_backend_name(index.similarity_backend())),
        parity,
        max_score_error,
        kernel,
        end_to_end
    };
}

bool check_adversarial_controls() {
    ExactVectorIndex cancellation(ExactVectorIndexOptions{3, SimilarityMetric::Cosine, false});
    cancellation.upsert(VectorRecord{
        ChunkId{"chunk:bad"}, Embedding{{1.0F, -1.0F, 1.0e-10F}}, {}
    });
    cancellation.upsert(VectorRecord{
        ChunkId{"chunk:good"}, Embedding{{1.0F, 1.0F, 1.0e-10F}}, {}
    });
    const auto cancellation_result = cancellation.search(VectorSearchQuery{
        Embedding{{1.0F, 1.0F, 1.0e-10F}}, 2, {}
    });
    if(cancellation_result.size() != 2
       || cancellation_result.front().chunk_id.value() != "chunk:good") {
        return false;
    }

    ExactVectorIndex near_tie(ExactVectorIndexOptions{2, SimilarityMetric::Cosine, true});
    near_tie.upsert(VectorRecord{ChunkId{"chunk:b"}, Embedding{{1.0F, 0.0F}}, {}});
    near_tie.upsert(VectorRecord{ChunkId{"chunk:a"}, Embedding{{1.0F, 0.0F}}, {}});
    const auto ties = near_tie.search(VectorSearchQuery{Embedding{{1.0F, 0.0F}}, 2, {}});
    if(ties.size() != 2 || ties[0].chunk_id.value() != "chunk:a") {
        return false;
    }

    bool rejected_non_finite = false;
    try {
        (void)near_tie.search(VectorSearchQuery{
            Embedding{{std::numeric_limits<float>::quiet_NaN(), 0.0F}}, 1, {}
        });
    } catch(const std::invalid_argument&) {
        rejected_non_finite = true;
    }
    if(!rejected_non_finite) {
        return false;
    }

    ExactVectorIndex overflowing(ExactVectorIndexOptions{2, SimilarityMetric::DotProduct, false});
    const auto extreme = std::numeric_limits<float>::max();
    overflowing.upsert(VectorRecord{ChunkId{"chunk:overflow"}, Embedding{{extreme, extreme}}, {}});
    bool rejected_overflow = false;
    try {
        (void)overflowing.search(VectorSearchQuery{Embedding{{extreme, extreme}}, 1, {}});
    } catch(const std::overflow_error&) {
        rejected_overflow = true;
    }
    return rejected_overflow;
}

void write_timing(std::ostream& output, const Timing& timing) {
    output << '[';
    for(std::size_t index = 0; index < timing.milliseconds.size(); ++index) {
        if(index != 0) {
            output << ',';
        }
        output << std::setprecision(12) << timing.milliseconds[index];
    }
    output << ']';
}

void write_case(std::ostream& output, const CaseResult& result) {
    output << "{\"rows\":" << result.rows
           << ",\"dimension\":" << result.dimension
           << ",\"queries\":" << result.queries
           << ",\"top_k\":" << result.top_k
           << ",\"backend\":\"" << result.backend
           << "\",\"parity\":" << (result.parity ? "true" : "false")
           << ",\"max_score_error\":" << std::setprecision(12) << result.max_score_error
           << ",\"kernel_only_ms\":";
    write_timing(output, result.kernel);
    output << ",\"end_to_end_ms\":";
    write_timing(output, result.end_to_end);
    output << '}';
}

} // namespace

int main(int argc, char** argv) {
    const auto output_path = argc > 1 ? std::string(argv[1]) : std::string{};
    std::ofstream file;
    std::ostream* output = &std::cout;
    if(!output_path.empty()) {
        file.open(output_path, std::ios::trunc);
        if(!file) {
            std::cerr << "cannot open output: " << output_path << '\n';
            return 2;
        }
        output = &file;
    }

    const bool controls = check_adversarial_controls();
    const std::vector<std::size_t> sizes = {128, 1024, 8192, 65536};
    std::vector<CaseResult> cases;
    for(const auto rows : sizes) {
        cases.push_back(run_case(rows, 8, 4, 10));
    }

    bool parity = controls;
    for(const auto& result : cases) {
        parity = parity && result.parity;
    }
    *output << "{\"schema\":\"r5.exact_vector_index.decision.v1\""
            << ",\"status\":\"" << (parity ? "PASS" : "FAIL") << '\"'
            << ",\"production_path\":\"ExactVectorIndex::search\""
            << ",\"metric\":\"cosine\""
            << ",\"oracle\":\"compensated_long_double\""
            << ",\"long_double_bytes\":" << sizeof(long double)
            << ",\"adversarial_controls\":" << (controls ? "true" : "false")
            << ",\"cases\":[";
    for(std::size_t index = 0; index < cases.size(); ++index) {
        if(index != 0) {
            *output << ',';
        }
        write_case(*output, cases[index]);
    }
    *output << "]}\n";
    return parity ? 0 : 1;
}
