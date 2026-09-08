#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

std::vector<float> ReadFloats(const std::string& path, std::size_t expectedCount) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("cannot open float input");
    }
    std::vector<float> values(expectedCount);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(values.size() * sizeof(float)));
    if (input.gcount() != static_cast<std::streamsize>(values.size() * sizeof(float))) {
        throw std::runtime_error("float input size differs");
    }
    return values;
}

std::vector<std::uint16_t> ReadAddresses(const std::string& path, std::size_t expectedCount) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("cannot open address input");
    }
    std::vector<std::uint16_t> values(expectedCount);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(values.size() * sizeof(std::uint16_t)));
    if (input.gcount() != static_cast<std::streamsize>(values.size() * sizeof(std::uint16_t))) {
        throw std::runtime_error("address input size differs");
    }
    return values;
}

float Dot(const float* left, const float* right, std::size_t dimensions) {
    float sum = 0.0F;
    for (std::size_t dimension = 0; dimension < dimensions; ++dimension) {
        sum += left[dimension] * right[dimension];
    }
    return sum;
}

struct RepeatResult {
    double millisecondsPerQuery;
    std::uint64_t checksum;
};

RepeatResult Run(const std::vector<float>& centroids, const std::vector<float>& queries,
                 const std::vector<std::uint16_t>& pools, std::size_t queryCount,
                 std::size_t poolSize, std::size_t dimensions, std::size_t topK) {
    const auto started = std::chrono::steady_clock::now();
    std::uint64_t checksum = 1469598103934665603ULL;
    for (std::size_t query = 0; query < queryCount; ++query) {
        std::vector<std::pair<float, std::uint16_t>> scored;
        scored.reserve(poolSize);
        const float* queryVector = queries.data() + query * dimensions;
        for (std::size_t position = 0; position < poolSize; ++position) {
            const auto address = pools[query * poolSize + position];
            const float score = Dot(queryVector, centroids.data() + static_cast<std::size_t>(address) * dimensions, dimensions);
            scored.emplace_back(score, address);
        }
        const auto compare = [](const auto& left, const auto& right) {
            return left.first != right.first ? left.first > right.first : left.second < right.second;
        };
        std::partial_sort(scored.begin(), scored.begin() + static_cast<std::ptrdiff_t>(topK), scored.end(), compare);
        for (std::size_t rank = 0; rank < topK; ++rank) {
            checksum ^= static_cast<std::uint64_t>(scored[rank].second + 1U + rank * 257U);
            checksum *= 1099511628211ULL;
        }
    }
    const auto elapsed = std::chrono::steady_clock::now() - started;
    return {std::chrono::duration<double, std::milli>(elapsed).count() / static_cast<double>(queryCount), checksum};
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 10) {
        std::cerr << "usage: native-local-centroid-refinement centroids queries pools query_count pool_size dimensions top_k warmups repeats\n";
        return 2;
    }
    try {
        const std::size_t queryCount = std::stoull(argv[4]);
        const std::size_t poolSize = std::stoull(argv[5]);
        const std::size_t dimensions = std::stoull(argv[6]);
        const std::size_t topK = std::stoull(argv[7]);
        const std::size_t warmups = std::stoull(argv[8]);
        const std::size_t repeats = std::stoull(argv[9]);
        if (queryCount == 0 || poolSize == 0 || dimensions == 0 || topK == 0 || topK > poolSize || repeats == 0) {
            throw std::runtime_error("benchmark dimensions differ");
        }
        const auto centroids = ReadFloats(argv[1], 256U * dimensions);
        const auto queries = ReadFloats(argv[2], queryCount * dimensions);
        const auto pools = ReadAddresses(argv[3], queryCount * poolSize);
        for (std::size_t warmup = 0; warmup < warmups; ++warmup) {
            static_cast<void>(Run(centroids, queries, pools, queryCount, poolSize, dimensions, topK));
        }
        std::vector<RepeatResult> results;
        results.reserve(repeats);
        for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
            results.push_back(Run(centroids, queries, pools, queryCount, poolSize, dimensions, topK));
        }
        std::cout << "{\"schema_version\":1,\"query_count\":" << queryCount
                  << ",\"pool_size\":" << poolSize << ",\"top_k\":" << topK
                  << ",\"milliseconds_per_query\":[";
        for (std::size_t index = 0; index < results.size(); ++index) {
            if (index != 0) {
                std::cout << ',';
            }
            std::cout << std::setprecision(17) << results[index].millisecondsPerQuery;
        }
        std::cout << "],\"checksums\":[";
        for (std::size_t index = 0; index < results.size(); ++index) {
            if (index != 0) {
                std::cout << ',';
            }
            std::cout << results[index].checksum;
        }
        std::cout << "]}\n";
    } catch (const std::exception& error) {
        std::cerr << "native-local-centroid-refinement: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
