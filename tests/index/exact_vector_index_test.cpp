#include <agent_memory.hpp>

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::Metadata make_metadata(std::string scope) {
        agent_memory::Metadata metadata;
        metadata.set("scope", std::move(scope));
        return metadata;
    }

    agent_memory::VectorRecord make_record(
        std::string chunk_id,
        std::vector<float> values,
        std::string scope
    ) {
        return agent_memory::VectorRecord{
            agent_memory::ChunkId{std::move(chunk_id)},
            agent_memory::Embedding{std::move(values)},
            make_metadata(std::move(scope))
        };
    }

    template <typename Fn>
    bool throws_invalid_argument(Fn&& fn) {
        try {
            fn();
        } catch(const std::invalid_argument&) {
            return true;
        }
        return false;
    }

    template <typename Fn>
    bool throws_overflow_error(Fn&& fn) {
        try {
            fn();
        } catch(const std::overflow_error&) {
            return true;
        }
        return false;
    }

    [[nodiscard]] bool almost_equal(float lhs, float rhs) noexcept {
        return std::fabs(lhs - rhs) <= 1.0e-5F;
    }

} // namespace

int main() {
    agent_memory::ExactVectorIndex index(agent_memory::ExactVectorIndexOptions{
        3,
        agent_memory::SimilarityMetric::Cosine
    });

    if(index.dimension() != 3 || index.similarity_metric() != agent_memory::SimilarityMetric::Cosine) {
        return fail("exact vector index must expose configured dimension and metric");
    }

    index.upsert(make_record("chunk:alpha", {1.0F, 0.0F, 0.0F}, "documents"));
    index.upsert(make_record("chunk:beta", {0.0F, 1.0F, 0.0F}, "chat"));
    index.upsert(make_record("chunk:gamma", {0.5F, 0.5F, 0.0F}, "documents"));

    if(index.size() != 3) {
        return fail("exact vector index must count inserted records");
    }

    const auto stored = index.find(agent_memory::ChunkId{"chunk:alpha"});
    if(!stored || stored->embedding.dimension() != 3) {
        return fail("exact vector index must find inserted records");
    }

    const auto cosine_results = index.search(agent_memory::VectorSearchQuery{
        agent_memory::Embedding{{1.0F, 0.0F, 0.0F}},
        2,
        {agent_memory::MetadataFilter{"scope", "documents"}}
    });

    if(cosine_results.size() != 2) {
        return fail("exact vector search must apply limit and metadata filters");
    }

    if(
        cosine_results[0].chunk_id.value() != "chunk:alpha" ||
        cosine_results[1].chunk_id.value() != "chunk:gamma"
    ) {
        return fail("cosine search must rank exact neighbours first");
    }

    if(cosine_results[0].score <= cosine_results[1].score) {
        return fail("cosine search scores must be ordered descending");
    }

    index.upsert(make_record("chunk:alpha", {0.0F, 1.0F, 0.0F}, "documents"));
    if(index.size() != 3) {
        return fail("upsert with existing chunk id must replace without growing");
    }

    if(!index.erase(agent_memory::ChunkId{"chunk:beta"})) {
        return fail("erase must report removed records");
    }

    if(index.find(agent_memory::ChunkId{"chunk:beta"})) {
        return fail("erase must remove records");
    }

    agent_memory::ExactVectorIndex dynamic_index;
    const auto empty_dynamic_results = dynamic_index.search(agent_memory::VectorSearchQuery{
        agent_memory::Embedding{{1.0F, 0.0F}},
        10,
        {}
    });
    if(dynamic_index.dimension() != 0 || !empty_dynamic_results.empty()) {
        return fail("empty dynamic exact vector index must accept queries and return no results");
    }

    dynamic_index.upsert(make_record("chunk:dynamic", {1.0F, 0.0F}, "dynamic"));
    if(dynamic_index.dimension() != 2) {
        return fail("dynamic exact vector index must adopt first record dimension");
    }

    if(!throws_invalid_argument([&dynamic_index] {
        dynamic_index.upsert(make_record("chunk:bad", {1.0F, 0.0F, 0.0F}, "dynamic"));
    })) {
        return fail("exact vector index must reject record dimension mismatches");
    }

    if(!throws_invalid_argument([&dynamic_index] {
        (void)dynamic_index.search(agent_memory::VectorSearchQuery{
            agent_memory::Embedding{{1.0F, 0.0F, 0.0F}},
            1,
            {}
        });
    })) {
        return fail("exact vector index must reject query dimension mismatches");
    }

    agent_memory::ExactVectorIndex invalid_dynamic;
    if(!throws_invalid_argument([&invalid_dynamic] {
           invalid_dynamic.upsert(make_record(
               "chunk:invalid-first",
               {std::numeric_limits<float>::quiet_NaN(), 1.0F},
               "invalid"
           ));
       })) {
        return fail("exact vector index must reject NaN records");
    }
    if(invalid_dynamic.dimension() != 0 || invalid_dynamic.size() != 0) {
        return fail("failed first insertion must not adopt a vector dimension");
    }
    invalid_dynamic.upsert(make_record("chunk:valid-first", {1.0F, 0.0F}, "valid"));
    if(invalid_dynamic.dimension() != 2 || invalid_dynamic.size() != 1) {
        return fail("dimension adoption must follow the first successful insertion");
    }

    if(!throws_invalid_argument([&index] {
           index.upsert(make_record(
               "chunk:alpha",
               {std::numeric_limits<float>::infinity(), 0.0F, 0.0F},
               "documents"
           ));
       })) {
        return fail("exact vector index must reject infinite replacement values");
    }
    const auto alpha_after_invalid = index.find(agent_memory::ChunkId{"chunk:alpha"});
    if(!alpha_after_invalid || alpha_after_invalid->embedding.values[0] != 0.0F) {
        return fail("rejected vector update must preserve the existing record");
    }

    if(!throws_invalid_argument([&index] {
           (void)index.search(agent_memory::VectorSearchQuery{
               agent_memory::Embedding{{-std::numeric_limits<float>::infinity(), 0.0F, 0.0F}},
               1,
               {}
           });
       })) {
        return fail("exact vector index must reject infinite query values");
    }

    agent_memory::ExactVectorIndex dot_index(agent_memory::ExactVectorIndexOptions{
        2,
        agent_memory::SimilarityMetric::DotProduct
    });
    dot_index.upsert(make_record("chunk:dot", {2.0F, 0.0F}, "metric"));
    const auto dot_results = dot_index.search(agent_memory::VectorSearchQuery{
        agent_memory::Embedding{{1.0F, 0.0F}},
        1,
        {}
    });
    if(dot_results.empty() || dot_results.front().score != 2.0F) {
        return fail("dot product search must expose dot product scores");
    }

    agent_memory::ExactVectorIndex tie_index(agent_memory::ExactVectorIndexOptions{
        2,
        agent_memory::SimilarityMetric::DotProduct
    });
    tie_index.upsert(make_record("chunk:b", {1.0F, 0.0F}, "tie"));
    tie_index.upsert(make_record("chunk:a", {1.0F, 0.0F}, "tie"));
    const auto tie_results = tie_index.search(agent_memory::VectorSearchQuery{
        agent_memory::Embedding{{1.0F, 0.0F}},
        2,
        {}
    });
    if(
        tie_results.size() != 2 ||
        tie_results[0].chunk_id.value() != "chunk:a" ||
        tie_results[1].chunk_id.value() != "chunk:b"
    ) {
        return fail("exact vector index must break score ties by chunk id");
    }

    agent_memory::ExactVectorIndex euclidean_index(agent_memory::ExactVectorIndexOptions{
        2,
        agent_memory::SimilarityMetric::Euclidean
    });
    euclidean_index.upsert(make_record("chunk:near", {1.0F, 0.0F}, "metric"));
    euclidean_index.upsert(make_record("chunk:far", {2.0F, 0.0F}, "metric"));
    const auto euclidean_results = euclidean_index.search(agent_memory::VectorSearchQuery{
        agent_memory::Embedding{{0.0F, 0.0F}},
        2,
        {}
    });
    if(
        euclidean_results.size() != 2 ||
        euclidean_results[0].chunk_id.value() != "chunk:near" ||
        euclidean_results[1].chunk_id.value() != "chunk:far"
    ) {
        return fail("euclidean search must rank lower distances first");
    }

    agent_memory::ExactVectorIndex scalar_index(agent_memory::ExactVectorIndexOptions{
        13,
        agent_memory::SimilarityMetric::Cosine,
        false
    });
    agent_memory::ExactVectorIndex simd_index(agent_memory::ExactVectorIndexOptions{
        13,
        agent_memory::SimilarityMetric::Cosine,
        true
    });
    const std::vector<std::pair<std::string, std::vector<float>>> wide_records = {
        {"chunk:wide-a", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}},
        {"chunk:wide-b", {13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1}},
        {"chunk:wide-c", {1, -2, 3, -4, 5, -6, 7, -8, 9, -10, 11, -12, 13}}
    };
    for(const auto& item : wide_records) {
        scalar_index.upsert(make_record(item.first, item.second, "wide"));
        simd_index.upsert(make_record(item.first, item.second, "wide"));
    }
    const agent_memory::VectorSearchQuery wide_query{
        agent_memory::Embedding{{
            2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14
        }},
        3,
        {}
    };
    const auto scalar_results = scalar_index.search(wide_query);
    const auto simd_results = simd_index.search(wide_query);
    if(scalar_index.similarity_backend() != agent_memory::VectorSimilarityBackend::Scalar
       || scalar_results.size() != simd_results.size()) {
        return fail("exact vector index must expose scalar fallback and comparable results");
    }
    for(std::size_t result_index = 0; result_index < scalar_results.size(); ++result_index) {
        if(scalar_results[result_index].chunk_id != simd_results[result_index].chunk_id
           || !almost_equal(
               scalar_results[result_index].score,
               simd_results[result_index].score
           )) {
            return fail("SIMD exact vector ranking must match the scalar reference");
        }
    }

    simd_index.upsert(make_record(
        "chunk:wide-a",
        {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5},
        "wide"
    ));
    const auto replacement_results = simd_index.search(agent_memory::VectorSearchQuery{
        agent_memory::Embedding{{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1}},
        1,
        {}
    });
    if(replacement_results.empty()
       || replacement_results.front().chunk_id.value() != "chunk:wide-a"
       || !almost_equal(replacement_results.front().score, 1.0F)) {
        return fail("upsert replacement must refresh the cached cosine norm");
    }

    const auto extreme = std::numeric_limits<float>::max();
    for(const bool enable_simd : {false, true}) {
        agent_memory::ExactVectorIndex extreme_cosine(
            agent_memory::ExactVectorIndexOptions{
                2,
                agent_memory::SimilarityMetric::Cosine,
                enable_simd
            }
        );
        extreme_cosine.upsert(make_record("chunk:extreme", {extreme, extreme}, "extreme"));
        const auto extreme_results = extreme_cosine.search(agent_memory::VectorSearchQuery{
            agent_memory::Embedding{{extreme, extreme}},
            1,
            {}
        });
        if(extreme_results.size() != 1
           || !std::isfinite(extreme_results.front().score)
           || !almost_equal(extreme_results.front().score, 1.0F)) {
            return fail("cosine scoring must remain finite for extreme finite inputs");
        }
    }

    agent_memory::ExactVectorIndex overflowing_dot(
        agent_memory::ExactVectorIndexOptions{
            2,
            agent_memory::SimilarityMetric::DotProduct,
            false
        }
    );
    overflowing_dot.upsert(make_record("chunk:overflow", {extreme, extreme}, "extreme"));
    if(!throws_overflow_error([&overflowing_dot, extreme] {
           (void)overflowing_dot.search(agent_memory::VectorSearchQuery{
               agent_memory::Embedding{{extreme, extreme}},
               1,
               {}
           });
       })) {
        return fail("non-finite dot-product scores must be rejected before ranking");
    }

    agent_memory::ExactVectorIndex overflowing_euclidean(
        agent_memory::ExactVectorIndexOptions{
            2,
            agent_memory::SimilarityMetric::Euclidean,
            false
        }
    );
    overflowing_euclidean.upsert(make_record("chunk:overflow", {extreme, extreme}, "extreme"));
    if(!throws_overflow_error([&overflowing_euclidean] {
           (void)overflowing_euclidean.search(agent_memory::VectorSearchQuery{
               agent_memory::Embedding{{-std::numeric_limits<float>::max(),
                                        -std::numeric_limits<float>::max()}},
               1,
               {}
           });
       })) {
        return fail("non-finite Euclidean scores must be rejected before ranking");
    }

    index.clear();
    if(index.size() != 0 || index.dimension() != 3) {
        return fail("clear must remove all exact vector index records without changing dimension");
    }

    return 0;
}
