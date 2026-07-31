#include <agent_memory/embedding/embedding_types.hpp>
#include <agent_memory/index/BinarySignatureInfo.hpp>
#include <agent_memory/index/ExactVectorIndex.hpp>
#include <agent_memory/index/FlatBinarySignatureIndex.hpp>
#include <agent_memory/index/MultiProbeHammingIndex.hpp>
#include <agent_memory/index/RandomHyperplaneBinaryEncoder.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>
#include <agent_memory/index/VectorIndex.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_set>
#include <utility>
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

namespace {

    namespace fs = std::filesystem;
    using Clock = std::chrono::steady_clock;

    struct Config final {
        fs::path output_path;
        std::size_t document_count = 0;
        std::size_t query_count = 0;
        std::size_t embedding_dimensions = 0;
        std::size_t bit_count = 0;
        std::size_t oracle_k = 0;
        std::size_t returned_candidate_limit = 0;
        std::vector<std::size_t> rerank_candidate_limits;
        std::size_t mutation_count = 0;
        std::uint64_t seed = 0;
        std::uint64_t query_noise_seed = 0;
        std::size_t multiprobe_table_count = 8;
        std::size_t multiprobe_bits_per_table = 8;
        std::size_t multiprobe_max_probe_radius = 1;
        std::size_t multiprobe_candidate_multiplier = 64;
        std::size_t multiprobe_minimum_candidate_count = 128;
    };

    struct Dataset final {
        std::vector<agent_memory::Embedding> documents;
        std::vector<agent_memory::Embedding> queries;
    };

    [[nodiscard]] double elapsed_ms(Clock::time_point start, Clock::time_point end) {
        return std::chrono::duration<double, std::milli>(end - start).count();
    }

    [[nodiscard]] std::uint64_t peak_resident_set_bytes() {
#if defined(_WIN32)
        PROCESS_MEMORY_COUNTERS counters{};
        if(GetProcessMemoryInfo(
               GetCurrentProcess(),
               &counters,
               static_cast<DWORD>(sizeof(counters))
           )) {
            return static_cast<std::uint64_t>(counters.PeakWorkingSetSize);
        }
        return 0;
#else
        rusage usage{};
        if(getrusage(RUSAGE_SELF, &usage) != 0) {
            return 0;
        }
#if defined(__APPLE__)
        return static_cast<std::uint64_t>(usage.ru_maxrss);
#else
        return static_cast<std::uint64_t>(usage.ru_maxrss) * 1024ULL;
#endif
#endif
    }

    [[nodiscard]] const nlohmann::json* find_optional(
        const nlohmann::json& document,
        std::string_view field
    ) {
        const auto it = document.find(std::string{field});
        return it == document.end() ? nullptr : &*it;
    }

    [[nodiscard]] std::uint64_t require_positive_integer(
        const nlohmann::json& document,
        std::string_view field
    ) {
        const std::string name{field};
        const auto it = document.find(name);
        if(it == document.end() || !it->is_number_integer()) {
            throw std::runtime_error("config field '" + name + "' must be an integer");
        }
        std::uint64_t value = 0;
        if(it->is_number_unsigned()) {
            value = it->get<std::uint64_t>();
        } else {
            const auto signed_value = it->get<std::int64_t>();
            if(signed_value <= 0) {
                throw std::runtime_error(
                    "config field '" + name + "' must be greater than zero"
                );
            }
            value = static_cast<std::uint64_t>(signed_value);
        }
        if(value == 0) {
            throw std::runtime_error(
                "config field '" + name + "' must be greater than zero"
            );
        }
        return value;
    }

    [[nodiscard]] std::size_t checked_size(
        std::uint64_t value,
        std::string_view field
    ) {
        if(value > static_cast<std::uint64_t>(
            std::numeric_limits<std::size_t>::max()
        )) {
            throw std::runtime_error(
                "config field '" + std::string{field} + "' exceeds size_t"
            );
        }
        return static_cast<std::size_t>(value);
    }

    [[nodiscard]] std::size_t optional_size(
        const nlohmann::json& document,
        std::string_view field,
        std::size_t fallback
    ) {
        const auto* node = find_optional(document, field);
        if(node == nullptr) {
            return fallback;
        }
        const nlohmann::json wrapper = {{std::string{field}, *node}};
        return checked_size(require_positive_integer(wrapper, field), field);
    }

    [[nodiscard]] std::uint64_t optional_u64(
        const nlohmann::json& document,
        std::string_view field,
        std::uint64_t fallback
    ) {
        const auto* node = find_optional(document, field);
        if(node == nullptr) {
            return fallback;
        }
        const nlohmann::json wrapper = {{std::string{field}, *node}};
        return require_positive_integer(wrapper, field);
    }

    [[nodiscard]] std::size_t optional_positive_size(
        const nlohmann::json& document,
        std::string_view field,
        std::size_t fallback
    ) {
        const auto* node = find_optional(document, field);
        if(node == nullptr) {
            return fallback;
        }
        const nlohmann::json wrapper = {{std::string{field}, *node}};
        return checked_size(require_positive_integer(wrapper, field), field);
    }

    [[nodiscard]] std::vector<std::size_t> optional_size_list(
        const nlohmann::json& document,
        std::string_view field
    ) {
        const auto* node = find_optional(document, field);
        if(node == nullptr) {
            return {};
        }
        const std::string name{field};
        if(!node->is_array()) {
            throw std::runtime_error("config field '" + name + "' must be an array");
        }
        std::vector<std::size_t> values;
        values.reserve(node->size());
        for(std::size_t index = 0; index < node->size(); ++index) {
            const nlohmann::json wrapper = {
                {name, (*node)[index]}
            };
            values.push_back(checked_size(
                require_positive_integer(wrapper, field),
                field
            ));
        }
        std::sort(values.begin(), values.end());
        if(std::adjacent_find(values.begin(), values.end()) != values.end()) {
            throw std::runtime_error(
                "config field '" + name + "' must not contain duplicates"
            );
        }
        return values;
    }

    [[nodiscard]] Config load_config(const fs::path& path) {
        std::ifstream input(path, std::ios::binary);
        if(!input) {
            throw std::runtime_error("cannot open binary lifecycle config");
        }
        nlohmann::json document;
        input >> document;
        if(!document.is_object()) {
            throw std::runtime_error("binary lifecycle config root must be an object");
        }

        Config config;
        const auto* output = find_optional(document, "output_path");
        if(output != nullptr) {
            if(!output->is_string() || output->get<std::string>().empty()) {
                throw std::runtime_error("config field 'output_path' must be a string");
            }
            config.output_path = output->get<std::string>();
        }
        config.document_count = checked_size(
            require_positive_integer(document, "document_count"),
            "document_count"
        );
        config.query_count = checked_size(
            require_positive_integer(document, "query_count"),
            "query_count"
        );
        config.embedding_dimensions = checked_size(
            require_positive_integer(document, "embedding_dimensions"),
            "embedding_dimensions"
        );
        config.bit_count = checked_size(
            require_positive_integer(document, "bit_count"),
            "bit_count"
        );
        std::size_t legacy_result_limit = 0;
        const bool has_legacy_result_limit =
            find_optional(document, "result_limit") != nullptr;
        if(has_legacy_result_limit) {
            legacy_result_limit = checked_size(
                require_positive_integer(document, "result_limit"),
                "result_limit"
            );
        }
        config.oracle_k = optional_size(document, "oracle_k", legacy_result_limit);
        config.returned_candidate_limit = optional_size(
            document,
            "returned_candidate_limit",
            legacy_result_limit
        );
        if(has_legacy_result_limit
           && find_optional(document, "returned_candidate_limit") != nullptr
           && legacy_result_limit != config.returned_candidate_limit) {
            throw std::runtime_error(
                "config field 'result_limit' is a legacy alias and must match "
                "'returned_candidate_limit' when both are present"
            );
        }
        if(config.oracle_k == 0) {
            throw std::runtime_error(
                "config field 'oracle_k' is required unless result_limit is passed"
            );
        }
        if(config.returned_candidate_limit == 0) {
            throw std::runtime_error(
                "config field 'returned_candidate_limit' is required unless result_limit is passed"
            );
        }
        config.rerank_candidate_limits = optional_size_list(
            document,
            "rerank_candidate_limits"
        );
        config.mutation_count = checked_size(
            require_positive_integer(document, "mutation_count"),
            "mutation_count"
        );
        config.seed = require_positive_integer(document, "seed");
        config.query_noise_seed = optional_u64(
            document,
            "query_noise_seed",
            config.seed ^ std::uint64_t{0x9E3779B97F4A7C15ULL}
        );
        config.multiprobe_table_count = optional_positive_size(
            document,
            "multiprobe_table_count",
            config.multiprobe_table_count
        );
        config.multiprobe_bits_per_table = optional_positive_size(
            document,
            "multiprobe_bits_per_table",
            config.multiprobe_bits_per_table
        );
        config.multiprobe_max_probe_radius = optional_positive_size(
            document,
            "multiprobe_max_probe_radius",
            config.multiprobe_max_probe_radius
        );
        config.multiprobe_candidate_multiplier = optional_positive_size(
            document,
            "multiprobe_candidate_multiplier",
            config.multiprobe_candidate_multiplier
        );
        config.multiprobe_minimum_candidate_count = optional_positive_size(
            document,
            "multiprobe_minimum_candidate_count",
            config.multiprobe_minimum_candidate_count
        );
        if(config.oracle_k > config.document_count) {
            throw std::runtime_error("oracle_k must not exceed document_count");
        }
        if(config.returned_candidate_limit > config.document_count) {
            throw std::runtime_error(
                "returned_candidate_limit must not exceed document_count"
            );
        }
        for(const auto candidate_limit : config.rerank_candidate_limits) {
            if(candidate_limit < config.oracle_k) {
                throw std::runtime_error(
                    "rerank_candidate_limits entries must be at least oracle_k"
                );
            }
            if(candidate_limit > config.document_count) {
                throw std::runtime_error(
                    "rerank_candidate_limits entries must not exceed document_count"
                );
            }
        }
        if(config.mutation_count > config.document_count) {
            throw std::runtime_error("mutation_count must not exceed document_count");
        }
        return config;
    }

    void normalize(agent_memory::Embedding& embedding) {
        double squared_norm = 0.0;
        for(const auto value : embedding.values) {
            squared_norm += static_cast<double>(value) * static_cast<double>(value);
        }
        if(squared_norm == 0.0) {
            throw std::runtime_error("cannot normalize a zero vector");
        }
        const auto scale = static_cast<float>(1.0 / std::sqrt(squared_norm));
        for(auto& value : embedding.values) {
            value *= scale;
        }
    }

    [[nodiscard]] Dataset generate_dataset(const Config& config) {
        std::mt19937_64 document_rng(config.seed);
        std::mt19937_64 query_rng(config.query_noise_seed);
        std::normal_distribution<float> normal(0.0F, 1.0F);
        std::normal_distribution<float> query_noise(0.0F, 0.08F);

        Dataset dataset;
        dataset.documents.reserve(config.document_count);
        dataset.queries.reserve(config.query_count);
        for(std::size_t index = 0; index < config.document_count; ++index) {
            agent_memory::Embedding embedding;
            embedding.values.reserve(config.embedding_dimensions);
            for(std::size_t dim = 0; dim < config.embedding_dimensions; ++dim) {
                embedding.values.push_back(normal(document_rng));
            }
            normalize(embedding);
            dataset.documents.push_back(std::move(embedding));
        }
        for(std::size_t query = 0; query < config.query_count; ++query) {
            agent_memory::Embedding embedding = dataset.documents[
                query % config.document_count
            ];
            for(auto& value : embedding.values) {
                value += query_noise(query_rng);
            }
            normalize(embedding);
            dataset.queries.push_back(std::move(embedding));
        }
        return dataset;
    }

    [[nodiscard]] agent_memory::ChunkId make_chunk_id(std::size_t index) {
        return agent_memory::ChunkId{"chunk:" + std::to_string(index)};
    }

    [[nodiscard]] agent_memory::BinarySignatureInfo make_signature_info(
        const Config& config,
        const agent_memory::BinarySignatureEncoderInfo& encoder_info
    ) {
        agent_memory::EmbeddingModelInfo model;
        model.model_id = "synthetic-lifecycle-normalized-v1";
        model.dimension = config.embedding_dimensions;
        model.similarity_metric = agent_memory::SimilarityMetric::Cosine;
        model.normalized = true;
        return agent_memory::make_binary_signature_info(
            encoder_info,
            model,
            "synthetic_dense_normalized"
        );
    }

    [[nodiscard]] std::vector<agent_memory::VectorRecord> make_vector_records(
        const Dataset& dataset
    ) {
        std::vector<agent_memory::VectorRecord> records;
        records.reserve(dataset.documents.size());
        for(std::size_t index = 0; index < dataset.documents.size(); ++index) {
            records.push_back({make_chunk_id(index), dataset.documents[index], {}});
        }
        return records;
    }

    [[nodiscard]] std::vector<agent_memory::BinarySignatureRecord>
    make_binary_records(
        const std::vector<agent_memory::BinarySignature>& signatures,
        const agent_memory::BinarySignatureInfo& signature_info
    ) {
        std::vector<agent_memory::BinarySignatureRecord> records;
        records.reserve(signatures.size());
        for(std::size_t index = 0; index < signatures.size(); ++index) {
            records.push_back({make_chunk_id(index), signatures[index], signature_info, {}});
        }
        return records;
    }

    template <class Index, class Records>
    [[nodiscard]] double build_index(Index& index, const Records& records) {
        const auto start = Clock::now();
        for(const auto& record : records) {
            index.upsert(record);
        }
        return elapsed_ms(start, Clock::now());
    }

    struct ExactQueryMeasurement final {
        nlohmann::json query;
        std::vector<std::unordered_set<std::string>> exact_sets;
        std::vector<std::vector<std::string>> exact_ids;
    };

    struct BinaryCandidateMeasurement final {
        nlohmann::json query;
        std::vector<std::vector<agent_memory::BinarySignatureSearchResult>>
            query_results;
    };

    [[nodiscard]] std::vector<agent_memory::VectorSearchQuery>
    make_vector_queries(
        const Dataset& dataset,
        std::size_t limit
    ) {
        std::vector<agent_memory::VectorSearchQuery> queries;
        queries.reserve(dataset.queries.size());
        for(const auto& query : dataset.queries) {
            agent_memory::VectorSearchQuery search_query;
            search_query.embedding = query;
            search_query.limit = limit;
            queries.push_back(std::move(search_query));
        }
        return queries;
    }

    [[nodiscard]] std::vector<agent_memory::BinarySignatureSearchQuery>
    make_binary_queries(
        const std::vector<agent_memory::BinarySignature>& query_signatures,
        const agent_memory::BinarySignatureInfo& signature_info,
        std::size_t limit
    ) {
        std::vector<agent_memory::BinarySignatureSearchQuery> queries;
        queries.reserve(query_signatures.size());
        for(const auto& signature : query_signatures) {
            agent_memory::BinarySignatureSearchQuery search_query;
            search_query.signature = signature;
            search_query.signature_info = signature_info;
            search_query.limit = limit;
            queries.push_back(std::move(search_query));
        }
        return queries;
    }

    [[nodiscard]] ExactQueryMeasurement measure_exact_queries(
        const agent_memory::ExactVectorIndex& index,
        const std::vector<agent_memory::VectorSearchQuery>& queries
    ) {
        std::vector<std::vector<agent_memory::VectorSearchResult>> query_results;
        query_results.reserve(queries.size());
        std::size_t result_count = 0;
        const auto start = Clock::now();
        for(const auto& query : queries) {
            auto results = index.search(query);
            query_results.push_back(std::move(results));
        }
        const auto total_ms = elapsed_ms(start, Clock::now());

        std::vector<std::unordered_set<std::string>> sets;
        sets.reserve(query_results.size());
        std::vector<std::vector<std::string>> ids;
        ids.reserve(query_results.size());
        for(const auto& results : query_results) {
            result_count += results.size();
            std::unordered_set<std::string> set;
            std::vector<std::string> ordered_ids;
            ordered_ids.reserve(results.size());
            for(const auto& result : results) {
                set.insert(result.chunk_id.value());
                ordered_ids.push_back(result.chunk_id.value());
            }
            sets.push_back(std::move(set));
            ids.push_back(std::move(ordered_ids));
        }

        return {
            {
                {"total_ms", total_ms},
                {"mean_ms", total_ms / static_cast<double>(queries.size())},
                {"mean_result_count", static_cast<double>(result_count) /
                                          static_cast<double>(queries.size())}
            },
            std::move(sets),
            std::move(ids)
        };
    }

    template <class SearchFn>
    [[nodiscard]] BinaryCandidateMeasurement collect_binary_candidates(
        const std::vector<agent_memory::BinarySignatureSearchQuery>& queries,
        SearchFn&& search_fn
    ) {
        std::vector<std::vector<agent_memory::BinarySignatureSearchResult>>
            query_results;
        query_results.reserve(queries.size());
        std::size_t result_count = 0;
        const auto start = Clock::now();
        for(const auto& query : queries) {
            query_results.push_back(search_fn(query));
        }
        const auto total_ms = elapsed_ms(start, Clock::now());

        for(const auto& results : query_results) {
            result_count += results.size();
        }

        return {
            {
                {"total_ms", total_ms},
                {"mean_ms", total_ms / static_cast<double>(queries.size())},
                {"mean_result_count", static_cast<double>(result_count) /
                                          static_cast<double>(queries.size())}
            },
            std::move(query_results)
        };
    }

    template <class SearchFn>
    [[nodiscard]] nlohmann::json measure_binary_queries(
        const std::vector<agent_memory::BinarySignatureSearchQuery>& queries,
        const std::vector<std::unordered_set<std::string>>& exact_sets,
        SearchFn&& search_fn
    ) {
        const auto candidate_measurement = collect_binary_candidates(
            queries,
            std::forward<SearchFn>(search_fn)
        );
        const auto& query_results = candidate_measurement.query_results;

        double coverage_sum = 0.0;
        std::size_t result_count = 0;
        for(std::size_t query = 0; query < query_results.size(); ++query) {
            const auto& results = query_results[query];
            result_count += results.size();
            std::size_t hits = 0;
            for(const auto& result : results) {
                if(exact_sets[query].find(result.chunk_id.value()) != exact_sets[query].end()) {
                    ++hits;
                }
            }
            coverage_sum += static_cast<double>(hits) /
                            static_cast<double>(exact_sets[query].size());
        }
        return {
            {"total_ms", candidate_measurement.query.at("total_ms")},
            {"mean_ms", candidate_measurement.query.at("mean_ms")},
            {"mean_result_count", static_cast<double>(result_count) /
                                      static_cast<double>(queries.size())},
            {"exact_top_k_candidate_coverage",
             coverage_sum / static_cast<double>(queries.size())}
        };
    }

    [[nodiscard]] std::size_t parse_chunk_index(const agent_memory::ChunkId& chunk_id) {
        constexpr std::string_view prefix = "chunk:";
        const auto text = chunk_id.value();
        if(text.size() <= prefix.size()
           || text.compare(0, prefix.size(), prefix) != 0) {
            throw std::runtime_error(
                "binary lifecycle candidate chunk id is not a synthetic chunk id"
            );
        }
        std::size_t value = 0;
        const char* first = text.data() + static_cast<std::ptrdiff_t>(prefix.size());
        const char* last = text.data() + static_cast<std::ptrdiff_t>(text.size());
        const auto result = std::from_chars(first, last, value);
        if(result.ec != std::errc{} || result.ptr != last) {
            throw std::runtime_error(
                "binary lifecycle candidate chunk id has an invalid numeric suffix"
            );
        }
        return value;
    }

    struct ScoredCandidate final {
        std::size_t index = 0;
        agent_memory::ChunkId chunk_id;
        float score = 0.0F;
    };

    [[nodiscard]] std::vector<float> make_document_inverse_norms(
        const std::vector<agent_memory::Embedding>& documents,
        const agent_memory::VectorSimilarityComputer& similarity
    ) {
        std::vector<float> inverse_norms;
        inverse_norms.reserve(documents.size());
        for(const auto& document : documents) {
            const auto squared_norm = similarity.squared_norm(document);
            inverse_norms.push_back(
                squared_norm > 0.0F ? 1.0F / std::sqrt(squared_norm) : 0.0F
            );
        }
        return inverse_norms;
    }

    [[nodiscard]] std::vector<std::string> rerank_candidates_exact(
        const agent_memory::Embedding& query,
        const std::vector<agent_memory::Embedding>& documents,
        const std::vector<float>& document_inverse_norms,
        const std::vector<agent_memory::BinarySignatureSearchResult>& candidates,
        std::size_t oracle_k,
        const agent_memory::VectorSimilarityComputer& similarity
    ) {
        std::vector<ScoredCandidate> scored;
        scored.reserve(candidates.size());
        const auto query_squared_norm = similarity.squared_norm(query);
        const auto query_inverse_norm =
            query_squared_norm > 0.0F ? 1.0F / std::sqrt(query_squared_norm) : 0.0F;
        for(const auto& candidate : candidates) {
            const auto index = parse_chunk_index(candidate.chunk_id);
            if(index >= documents.size()) {
                throw std::runtime_error(
                    "binary lifecycle candidate chunk id exceeds document_count"
                );
            }
            float score = 0.0F;
            if(query_inverse_norm != 0.0F && document_inverse_norms[index] != 0.0F) {
                score = similarity.dot_product(query, documents[index])
                        * query_inverse_norm * document_inverse_norms[index];
            }
            scored.push_back({
                index,
                candidate.chunk_id,
                score
            });
        }

        const auto compare = [](const ScoredCandidate& lhs, const ScoredCandidate& rhs) {
            if(lhs.score == rhs.score) {
                return lhs.chunk_id < rhs.chunk_id;
            }
            return lhs.score > rhs.score;
        };
        if(scored.size() > oracle_k) {
            std::partial_sort(
                scored.begin(),
                scored.begin() + static_cast<std::ptrdiff_t>(oracle_k),
                scored.end(),
                compare
            );
            scored.resize(oracle_k);
        } else {
            std::sort(scored.begin(), scored.end(), compare);
        }

        std::vector<std::string> ids;
        ids.reserve(scored.size());
        for(const auto& item : scored) {
            ids.push_back(item.chunk_id.value());
        }
        return ids;
    }

    [[nodiscard]] double recall_against_exact(
        const std::vector<std::string>& actual,
        const std::unordered_set<std::string>& exact_set
    ) {
        if(exact_set.empty()) {
            return 0.0;
        }
        std::size_t hits = 0;
        for(const auto& id : actual) {
            if(exact_set.find(id) != exact_set.end()) {
                ++hits;
            }
        }
        return static_cast<double>(hits) / static_cast<double>(exact_set.size());
    }

    [[nodiscard]] nlohmann::json measure_exact_rerank(
        const Dataset& dataset,
        const std::vector<float>& document_inverse_norms,
        const std::vector<std::vector<agent_memory::BinarySignatureSearchResult>>&
            query_results,
        const std::vector<std::unordered_set<std::string>>& exact_sets,
        const std::vector<std::vector<std::string>>& exact_ids,
        std::size_t oracle_k,
        const agent_memory::VectorSimilarityComputer& similarity
    ) {
        std::vector<std::vector<std::string>> reranked_ids;
        reranked_ids.reserve(query_results.size());
        const auto start = Clock::now();
        for(std::size_t query_index = 0; query_index < query_results.size();
            ++query_index) {
            reranked_ids.push_back(rerank_candidates_exact(
                dataset.queries[query_index],
                dataset.documents,
                document_inverse_norms,
                query_results[query_index],
                oracle_k,
                similarity
            ));
        }
        const auto total_ms = elapsed_ms(start, Clock::now());

        double recall_sum = 0.0;
        std::size_t top1_hits = 0;
        for(std::size_t query_index = 0; query_index < reranked_ids.size();
            ++query_index) {
            recall_sum += recall_against_exact(
                reranked_ids[query_index],
                exact_sets[query_index]
            );
            if(!reranked_ids[query_index].empty()
               && !exact_ids[query_index].empty()
               && reranked_ids[query_index].front() == exact_ids[query_index].front()) {
                ++top1_hits;
            }
        }

        return {
            {"total_ms", total_ms},
            {"mean_ms", total_ms / static_cast<double>(query_results.size())},
            {"reranked_recall_at_k_vs_exact",
             recall_sum / static_cast<double>(query_results.size())},
            {"reranked_top1_agreement",
             static_cast<double>(top1_hits) /
                 static_cast<double>(query_results.size())}
        };
    }

    [[nodiscard]] double candidate_coverage(
        const std::vector<std::vector<agent_memory::BinarySignatureSearchResult>>&
            query_results,
        const std::vector<std::unordered_set<std::string>>& exact_sets
    ) {
        double coverage_sum = 0.0;
        for(std::size_t query = 0; query < query_results.size(); ++query) {
            std::size_t hits = 0;
            for(const auto& result : query_results[query]) {
                if(exact_sets[query].find(result.chunk_id.value()) != exact_sets[query].end()) {
                    ++hits;
                }
            }
            coverage_sum += static_cast<double>(hits) /
                            static_cast<double>(exact_sets[query].size());
        }
        return coverage_sum / static_cast<double>(query_results.size());
    }

    struct MultiProbeDiagnostics final {
        double candidate_sum = 0.0;
        double bucket_sum = 0.0;
        double posting_sum = 0.0;
    };

    [[nodiscard]] MultiProbeDiagnostics collect_multiprobe_diagnostics(
        const std::vector<agent_memory::BinarySignatureSearchQuery>& queries,
        const agent_memory::MultiProbeHammingIndex& index
    ) {
        MultiProbeDiagnostics diagnostics;
        for(const auto& query : queries) {
            const auto result = index.search_with_diagnostics(query);
            diagnostics.candidate_sum += static_cast<double>(result.candidate_count);
            diagnostics.bucket_sum += static_cast<double>(result.probed_bucket_count);
            diagnostics.posting_sum += static_cast<double>(result.visited_posting_count);
        }
        return diagnostics;
    }

    template <class SearchFn>
    [[nodiscard]] nlohmann::json measure_rerank_grid(
        const Dataset& dataset,
        const std::vector<float>& document_inverse_norms,
        const std::vector<agent_memory::BinarySignature>& query_signatures,
        const agent_memory::BinarySignatureInfo& signature_info,
        const std::vector<std::size_t>& candidate_limits,
        const std::vector<std::unordered_set<std::string>>& exact_sets,
        const std::vector<std::vector<std::string>>& exact_ids,
        std::size_t oracle_k,
        const agent_memory::VectorSimilarityComputer& similarity,
        SearchFn&& search_fn
    ) {
        nlohmann::json rows = nlohmann::json::array();
        for(const auto candidate_limit : candidate_limits) {
            const auto binary_queries = make_binary_queries(
                query_signatures,
                signature_info,
                candidate_limit
            );
            const auto candidates = collect_binary_candidates(
                binary_queries,
                search_fn
            );
            const auto rerank = measure_exact_rerank(
                dataset,
                document_inverse_norms,
                candidates.query_results,
                exact_sets,
                exact_ids,
                oracle_k,
                similarity
            );
            const auto binary_total_ms =
                candidates.query.at("total_ms").template get<double>();
            const auto binary_mean_ms =
                candidates.query.at("mean_ms").template get<double>();
            const auto binary_mean_result_count =
                candidates.query.at("mean_result_count").template get<double>();
            const auto coverage = candidate_coverage(
                candidates.query_results,
                exact_sets
            );
            const auto rerank_total_ms =
                rerank.at("total_ms").template get<double>();
            const auto rerank_mean_ms =
                rerank.at("mean_ms").template get<double>();
            const auto reranked_recall =
                rerank.at("reranked_recall_at_k_vs_exact").template get<double>();
            const auto reranked_top1 =
                rerank.at("reranked_top1_agreement").template get<double>();
            rows.push_back(nlohmann::json{
                {"candidate_limit", candidate_limit},
                {"binary_search_total_ms", binary_total_ms},
                {"binary_search_mean_ms", binary_mean_ms},
                {"binary_mean_result_count", binary_mean_result_count},
                {"exact_top_k_candidate_coverage", coverage},
                {"exact_rerank_total_ms", rerank_total_ms},
                {"exact_rerank_mean_ms", rerank_mean_ms},
                {"reranked_recall_at_k_vs_exact", reranked_recall},
                {"reranked_top1_agreement", reranked_top1},
                {"end_to_end_total_ms", binary_total_ms + rerank_total_ms},
                {"end_to_end_mean_ms",
                 (binary_total_ms + rerank_total_ms) /
                     static_cast<double>(dataset.queries.size())}
            });
        }
        return rows;
    }

    [[nodiscard]] nlohmann::json measure_multiprobe_rerank_grid(
        const Dataset& dataset,
        const std::vector<float>& document_inverse_norms,
        const std::vector<agent_memory::BinarySignature>& query_signatures,
        const agent_memory::BinarySignatureInfo& signature_info,
        const std::vector<std::size_t>& candidate_limits,
        const std::vector<std::unordered_set<std::string>>& exact_sets,
        const std::vector<std::vector<std::string>>& exact_ids,
        std::size_t oracle_k,
        const agent_memory::VectorSimilarityComputer& similarity,
        const agent_memory::MultiProbeHammingIndex& index
    ) {
        auto rows = measure_rerank_grid(
            dataset,
            document_inverse_norms,
            query_signatures,
            signature_info,
            candidate_limits,
            exact_sets,
            exact_ids,
            oracle_k,
            similarity,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return index.search(query);
            }
        );
        for(std::size_t row = 0; row < candidate_limits.size(); ++row) {
            const auto binary_queries = make_binary_queries(
                query_signatures,
                signature_info,
                candidate_limits[row]
            );
            const auto diagnostics = collect_multiprobe_diagnostics(
                binary_queries,
                index
            );
            const auto query_count = static_cast<double>(binary_queries.size());
            rows[row]["mean_candidate_count"] =
                diagnostics.candidate_sum / query_count;
            rows[row]["mean_probed_bucket_count"] =
                diagnostics.bucket_sum / query_count;
            rows[row]["mean_visited_posting_count"] =
                diagnostics.posting_sum / query_count;
        }
        return rows;
    }

    template <class Index, class Records>
    [[nodiscard]] nlohmann::json measure_mutations(
        Index& index,
        const Records& records,
        std::size_t mutation_count
    ) {
        const auto size_before_erase = index.size();
        const auto erase_start = Clock::now();
        std::size_t erased = 0;
        for(std::size_t index_number = 0; index_number < mutation_count; ++index_number) {
            if(index.erase(records[index_number].chunk_id)) {
                ++erased;
            }
        }
        const auto erase_ms = elapsed_ms(erase_start, Clock::now());
        const auto size_after_erase = index.size();

        const auto upsert_start = Clock::now();
        for(std::size_t index_number = 0; index_number < mutation_count; ++index_number) {
            index.upsert(records[index_number]);
        }
        const auto upsert_ms = elapsed_ms(upsert_start, Clock::now());
        const auto size_after_upsert = index.size();

        return {
            {"mutation_count", mutation_count},
            {"erased_count", erased},
            {"erase_ms", erase_ms},
            {"upsert_ms", upsert_ms},
            {"size_before_erase", size_before_erase},
            {"size_after_erase", size_after_erase},
            {"size_after_upsert", size_after_upsert}
        };
    }

    [[nodiscard]] nlohmann::json run_benchmark(const Config& config) {
        const auto data_start = Clock::now();
        const auto dataset = generate_dataset(config);
        const auto data_generation_ms = elapsed_ms(data_start, Clock::now());

        agent_memory::RandomHyperplaneBinaryEncoder encoder({
            config.embedding_dimensions,
            config.bit_count,
            config.seed
        });
        const auto signature_info = make_signature_info(config, encoder.info());
        const auto encode_start = Clock::now();
        const auto document_signatures = encoder.encode_batch(dataset.documents);
        const auto query_signatures = encoder.encode_batch(dataset.queries);
        const auto binary_encoding_ms = elapsed_ms(encode_start, Clock::now());

        const auto vector_records = make_vector_records(dataset);
        const auto binary_records = make_binary_records(document_signatures, signature_info);
        const auto vector_queries = make_vector_queries(dataset, config.oracle_k);
        const auto binary_queries = make_binary_queries(
            query_signatures,
            signature_info,
            config.returned_candidate_limit
        );
        agent_memory::VectorSimilarityComputer rerank_similarity;
        const auto rerank_prepare_start = Clock::now();
        const auto document_inverse_norms = make_document_inverse_norms(
            dataset.documents,
            rerank_similarity
        );
        const auto rerank_prepare_ms = elapsed_ms(
            rerank_prepare_start,
            Clock::now()
        );

        agent_memory::ExactVectorIndex exact({
            config.embedding_dimensions,
            agent_memory::SimilarityMetric::Cosine,
            true
        });
        const auto exact_build_ms = build_index(exact, vector_records);
        const auto exact_size_after_build = exact.size();
        const auto exact_measurement = measure_exact_queries(
            exact,
            vector_queries
        );
        const auto& exact_sets = exact_measurement.exact_sets;
        const auto& exact_ids = exact_measurement.exact_ids;

        agent_memory::FlatBinarySignatureIndex flat({signature_info});
        const auto flat_build_ms = build_index(flat, binary_records);
        const auto flat_size_after_build = flat.size();

        agent_memory::MultiProbeHammingIndexOptions multiprobe_options;
        multiprobe_options.signature_info = signature_info;
        multiprobe_options.table_count = config.multiprobe_table_count;
        multiprobe_options.bits_per_table = config.multiprobe_bits_per_table;
        multiprobe_options.max_probe_radius = config.multiprobe_max_probe_radius;
        multiprobe_options.candidate_multiplier =
            config.multiprobe_candidate_multiplier;
        multiprobe_options.minimum_candidate_count =
            config.multiprobe_minimum_candidate_count;
        agent_memory::MultiProbeHammingIndex multiprobe(multiprobe_options);
        const auto multiprobe_build_ms = build_index(multiprobe, binary_records);
        const auto multiprobe_size_after_build = multiprobe.size();

        const auto flat_query = measure_binary_queries(
            binary_queries,
            exact_sets,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return flat.search(query);
            }
        );

        const auto multiprobe_query = measure_binary_queries(
            binary_queries,
            exact_sets,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return multiprobe.search(query);
            }
        );
        const auto multiprobe_diagnostics = collect_multiprobe_diagnostics(
            binary_queries,
            multiprobe
        );
        const auto flat_rerank = measure_rerank_grid(
            dataset,
            document_inverse_norms,
            query_signatures,
            signature_info,
            config.rerank_candidate_limits,
            exact_sets,
            exact_ids,
            config.oracle_k,
            rerank_similarity,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return flat.search(query);
            }
        );
        const auto multiprobe_rerank = measure_multiprobe_rerank_grid(
            dataset,
            document_inverse_norms,
            query_signatures,
            signature_info,
            config.rerank_candidate_limits,
            exact_sets,
            exact_ids,
            config.oracle_k,
            rerank_similarity,
            multiprobe
        );

        auto flat_mutations = measure_mutations(flat, binary_records, config.mutation_count);
        const auto flat_post_upsert_query = measure_binary_queries(
            binary_queries,
            exact_sets,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return flat.search(query);
            }
        );
        auto multiprobe_mutations = measure_mutations(
            multiprobe,
            binary_records,
            config.mutation_count
        );
        const auto multiprobe_post_upsert_query = measure_binary_queries(
            binary_queries,
            exact_sets,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return multiprobe.search(query);
            }
        );

        const auto flat_rebuild_start = Clock::now();
        flat.clear();
        const auto flat_size_after_clear = flat.size();
        for(const auto& record : binary_records) {
            flat.upsert(record);
        }
        const auto flat_rebuild_ms = elapsed_ms(flat_rebuild_start, Clock::now());
        const auto flat_size_after_rebuild = flat.size();
        const auto flat_post_rebuild_query = measure_binary_queries(
            binary_queries,
            exact_sets,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return flat.search(query);
            }
        );

        const auto multiprobe_rebuild_start = Clock::now();
        multiprobe.clear();
        const auto multiprobe_size_after_clear = multiprobe.size();
        for(const auto& record : binary_records) {
            multiprobe.upsert(record);
        }
        const auto multiprobe_rebuild_ms = elapsed_ms(
            multiprobe_rebuild_start,
            Clock::now()
        );
        const auto multiprobe_size_after_rebuild = multiprobe.size();
        const auto multiprobe_post_rebuild_query = measure_binary_queries(
            binary_queries,
            exact_sets,
            [&](const agent_memory::BinarySignatureSearchQuery& query) {
                return multiprobe.search(query);
            }
        );

        const auto query_count = static_cast<double>(dataset.queries.size());
        const auto word_count = agent_memory::binary_signature_word_count(config.bit_count);
        const auto exact_payload_bytes = static_cast<std::uint64_t>(
            config.document_count * config.embedding_dimensions * sizeof(float)
        );
        const auto binary_payload_bytes = static_cast<std::uint64_t>(
            config.document_count * word_count * sizeof(std::uint64_t)
        );

        nlohmann::json report;
        report["schema_version"] = 1;
        report["mode"] = "synthetic_binary_lifecycle";
        report["document_count"] = config.document_count;
        report["query_count"] = config.query_count;
        report["embedding_dimensions"] = config.embedding_dimensions;
        report["bit_count"] = config.bit_count;
        report["oracle_k"] = config.oracle_k;
        report["returned_candidate_limit"] = config.returned_candidate_limit;
        report["result_limit"] = config.returned_candidate_limit;
        report["rerank_candidate_limits"] = config.rerank_candidate_limits;
        report["mutation_count"] = config.mutation_count;
        report["seed"] = config.seed;
        report["query_noise_seed"] = config.query_noise_seed;
        report["timing_ms"] = {
            {"data_generation", data_generation_ms},
            {"binary_chunk_and_query_encoding", binary_encoding_ms},
            {"rerank_prepare_ms", rerank_prepare_ms}
        };
        report["exact_vector"] = {
            {"build_ms", exact_build_ms},
            {"query_total_ms", exact_measurement.query.at("total_ms")},
            {"query_mean_ms", exact_measurement.query.at("mean_ms")},
            {"query_mean_result_count",
             exact_measurement.query.at("mean_result_count")},
            {"similarity_backend",
             std::string(agent_memory::vector_similarity_backend_name(
                 exact.similarity_backend()
             ))},
            {"payload_bytes", exact_payload_bytes},
            {"size_after_build", exact_size_after_build}
        };
        report["flat_binary"] = {
            {"build_ms", flat_build_ms},
            {"size_after_build", flat_size_after_build},
            {"query", flat_query},
            {"post_upsert_query", flat_post_upsert_query},
            {"post_rebuild_query", flat_post_rebuild_query},
            {"mutations", flat_mutations},
            {"rebuild_ms", flat_rebuild_ms},
            {"size_after_clear", flat_size_after_clear},
            {"size_after_rebuild", flat_size_after_rebuild},
            {"rerank", flat_rerank},
            {"hamming_backend",
             std::string(agent_memory::hamming_distance_backend_name(
                 *flat.hamming_backend()
             ))},
            {"payload_bytes", binary_payload_bytes}
        };
        report["multiprobe_binary"] = {
            {"build_ms", multiprobe_build_ms},
            {"size_after_build", multiprobe_size_after_build},
            {"query", multiprobe_query},
            {"post_upsert_query", multiprobe_post_upsert_query},
            {"post_rebuild_query", multiprobe_post_rebuild_query},
            {"mutations", multiprobe_mutations},
            {"rebuild_ms", multiprobe_rebuild_ms},
            {"size_after_clear", multiprobe_size_after_clear},
            {"size_after_rebuild", multiprobe_size_after_rebuild},
            {"mean_candidate_count",
             multiprobe_diagnostics.candidate_sum / query_count},
            {"mean_probed_bucket_count",
             multiprobe_diagnostics.bucket_sum / query_count},
            {"mean_visited_posting_count",
             multiprobe_diagnostics.posting_sum / query_count},
            {"rerank", multiprobe_rerank},
            {"options",
             {
                 {"table_count", config.multiprobe_table_count},
                 {"bits_per_table", config.multiprobe_bits_per_table},
                 {"max_probe_radius", config.multiprobe_max_probe_radius},
                 {"candidate_multiplier", config.multiprobe_candidate_multiplier},
                 {"minimum_candidate_count", config.multiprobe_minimum_candidate_count}
             }},
            {"payload_bytes", binary_payload_bytes}
        };
        report["process_peak_resident_set_bytes"] = peak_resident_set_bytes();
        return report;
    }

} // namespace

int main(int argc, char** argv) {
    if(argc < 2 || argc > 3) {
        std::cerr
            << "usage: agent-memory-binary-lifecycle-bench <config.json> "
               "[output.json]\n";
        return 2;
    }

    try {
        auto config = load_config(fs::path{argv[1]});
        if(argc == 3) {
            config.output_path = fs::path{argv[2]};
        }
        if(config.output_path.empty()) {
            throw std::runtime_error(
                "config field 'output_path' is required unless output path is passed"
            );
        }
        const auto report = run_benchmark(config);
        std::ofstream output(config.output_path, std::ios::binary);
        if(!output) {
            throw std::runtime_error(
                "cannot open binary lifecycle output: " + config.output_path.string()
            );
        }
        output << report.dump(2) << '\n';
        std::cout << "JSON output: " << config.output_path.string() << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "agent-memory-binary-lifecycle-bench: " << error.what()
                  << '\n';
        return 1;
    }
}
