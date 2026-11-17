#include <agent_memory/lexical/IReranker.hpp>
#include <agent_memory/retrieval/HybridRetrievalEngine.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <new>
#include <optional>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

    using Json = nlohmann::json;
    using Clock = std::chrono::steady_clock;

#ifndef AGENT_MEMORY_RERANKER_ADMISSION_SOURCE_COMMIT
#define AGENT_MEMORY_RERANKER_ADMISSION_SOURCE_COMMIT "unconfigured"
#endif
#ifndef AGENT_MEMORY_RERANKER_ADMISSION_PRODUCTION_CONTRACT_COMMIT
#define AGENT_MEMORY_RERANKER_ADMISSION_PRODUCTION_CONTRACT_COMMIT "unconfigured"
#endif

    constexpr std::size_t kRepeats = 12;
    constexpr std::size_t kWarmups = 2;
    constexpr std::array<std::size_t, 5> kCandidateCounts{{10U, 50U, 100U, 500U, 1000U}};
    constexpr std::array<std::array<unsigned, 3>, 6> kOrders{{
        {{0U, 1U, 2U}}, {{0U, 2U, 1U}}, {{1U, 0U, 2U}},
        {{1U, 2U, 0U}}, {{2U, 0U, 1U}}, {{2U, 1U, 0U}}
    }};

    thread_local bool g_count_allocations = false;
    thread_local std::size_t g_allocation_count = 0;
    thread_local std::size_t g_allocated_bytes = 0;

    void* allocate(const std::size_t size) {
        const auto actual = size == 0U ? 1U : size;
        if(auto* result = std::malloc(actual); result != nullptr) {
            if(g_count_allocations) {
                ++g_allocation_count;
                g_allocated_bytes += actual;
            }
            return result;
        }
        throw std::bad_alloc{};
    }

    void release(void* pointer) noexcept {
        std::free(pointer);
    }

} // namespace

void* operator new(const std::size_t size) { return allocate(size); }
void* operator new[](const std::size_t size) { return allocate(size); }
void operator delete(void* pointer) noexcept { release(pointer); }
void operator delete[](void* pointer) noexcept { release(pointer); }
void operator delete(void* pointer, const std::size_t) noexcept { release(pointer); }
void operator delete[](void* pointer, const std::size_t) noexcept { release(pointer); }

namespace {

    struct Identity final {
        std::string chunk;
        std::string resource;
        agent_memory::SectionId section = 0;
        std::uint8_t tier = 0;

        [[nodiscard]] bool operator==(const Identity& other) const noexcept {
            return chunk == other.chunk && resource == other.resource
                && section == other.section && tier == other.tier;
        }
        [[nodiscard]] bool operator<(const Identity& other) const noexcept {
            return std::tie(chunk, resource, section, tier)
                < std::tie(other.chunk, other.resource, other.section, other.tier);
        }
    };

    struct IdentityHash final {
        [[nodiscard]] std::size_t operator()(const Identity& value) const noexcept {
            std::size_t result = std::hash<std::string>{}(value.chunk);
            result ^= std::hash<std::string>{}(value.resource) + 0x9e3779b9U
                + (result << 6U) + (result >> 2U);
            result ^= std::hash<std::size_t>{}(value.section) + 0x9e3779b9U
                + (result << 6U) + (result >> 2U);
            result ^= std::hash<unsigned>{}(value.tier) + 0x9e3779b9U
                + (result << 6U) + (result >> 2U);
            return result;
        }
    };

    [[nodiscard]] Identity identity_of(const agent_memory::LexicalSearchResult& value) {
        return Identity{
            value.chunk_id.value(), value.resource_id.value(), value.section_id, value.result_tier
        };
    }

    [[nodiscard]] bool protected_equal(
        const agent_memory::LexicalSearchResult& left,
        const agent_memory::LexicalSearchResult& right
    ) noexcept {
        return identity_of(left) == identity_of(right)
            && left.metadata.values() == right.metadata.values()
            && left.enrichment_level == right.enrichment_level;
    }

    [[nodiscard]] bool finite_score(const float score) noexcept {
        return std::isfinite(score);
    }

    class FixtureIndex final : public agent_memory::ILexicalIndex {
    public:
        explicit FixtureIndex(std::vector<agent_memory::LexicalSearchResult> hits)
            : m_hits(std::move(hits)) {}

        [[nodiscard]] std::size_t size() const noexcept override {
            return m_hits.size();
        }

        void upsert(agent_memory::LexicalDocumentRecord) override {}

        [[nodiscard]] std::optional<agent_memory::LexicalDocumentStats> find_stats(
            const agent_memory::ChunkId&
        ) const override {
            return std::nullopt;
        }

        [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> search(
            const agent_memory::LexicalSearchQuery& query
        ) const override {
            last_query = query;
            return m_hits;
        }

        [[nodiscard]] bool erase(const agent_memory::ChunkId&) override {
            return false;
        }

        [[nodiscard]] std::size_t erase_resource(
            const agent_memory::ResourceId&
        ) override {
            return 0;
        }

        void clear() override {}

        mutable std::optional<agent_memory::LexicalSearchQuery> last_query;

    private:
        std::vector<agent_memory::LexicalSearchResult> m_hits;
    };

    class FixedReranker final : public agent_memory::IReranker {
    public:
        explicit FixedReranker(std::vector<agent_memory::LexicalSearchResult> result)
            : m_result(std::move(result)) {}

        [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> rerank(
            const std::string&, std::vector<agent_memory::LexicalSearchResult>
        ) const override {
            return m_result;
        }

    private:
        std::vector<agent_memory::LexicalSearchResult> m_result;
    };

    class ProductionAdmissionRunner final {
    public:
        ProductionAdmissionRunner(
            const std::vector<agent_memory::LexicalSearchResult>& admitted,
            const std::vector<agent_memory::LexicalSearchResult>& reranked
        )
            : m_index(admitted), m_engine(m_index) {
            m_engine.set_reranker(std::make_unique<FixedReranker>(reranked));
        }

        [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> run(
            const std::size_t limit
        ) const {
            agent_memory::RetrievalRequest request;
            request.query = "memory";
            request.limit = limit;
            request.metadata_filters.push_back({"scope", "documents"});
            const auto response = m_engine.retrieve(request);
            std::vector<agent_memory::LexicalSearchResult> result;
            result.reserve(response.items.size());
            for(const auto& item : response.items) {
                result.push_back(item.lexical);
            }
            return result;
        }

        [[nodiscard]] const FixtureIndex& index() const noexcept {
            return m_index;
        }

    private:
        FixtureIndex m_index;
        agent_memory::HybridRetrievalEngine m_engine;
    };

    struct SortedIndex final {
        std::vector<std::pair<Identity, std::size_t>> entries;
    };

    [[nodiscard]] SortedIndex make_sorted_index(
        const std::vector<agent_memory::LexicalSearchResult>& admitted
    ) {
        SortedIndex result;
        result.entries.reserve(admitted.size());
        for(std::size_t index = 0; index < admitted.size(); ++index) {
            result.entries.emplace_back(identity_of(admitted[index]), index);
        }
        std::sort(result.entries.begin(), result.entries.end());
        return result;
    }

    [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> sorted_admit(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked,
        const SortedIndex& index
    ) {
        if(reranked.size() > admitted.size()) {
            return {};
        }
        std::vector<agent_memory::LexicalSearchResult> result;
        result.reserve(reranked.size());
        std::unordered_set<Identity, IdentityHash> seen;
        seen.reserve(reranked.size());
        for(const auto& candidate : reranked) {
            if(!finite_score(candidate.score)) {
                return {};
            }
            const auto key = identity_of(candidate);
            const auto matching = std::lower_bound(index.entries.begin(), index.entries.end(),
                std::pair<Identity, std::size_t>{key, 0U});
            if(matching == index.entries.end() || !(matching->first == key)
               || std::find(seen.begin(), seen.end(), key) != seen.end()) {
                return {};
            }
            const auto& original = admitted[matching->second];
            if(!protected_equal(original, candidate) || !seen.emplace(key).second) {
                return {};
            }
            auto trusted = original;
            trusted.score = candidate.score;
            result.push_back(std::move(trusted));
        }
        return result;
    }

    struct HashIndex final {
        std::unordered_map<Identity, std::size_t, IdentityHash> entries;
    };

    [[nodiscard]] HashIndex make_hash_index(
        const std::vector<agent_memory::LexicalSearchResult>& admitted
    ) {
        HashIndex result;
        result.entries.reserve(admitted.size());
        for(std::size_t index = 0; index < admitted.size(); ++index) {
            result.entries.emplace(identity_of(admitted[index]), index);
        }
        return result;
    }

    [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> hash_admit(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked,
        const HashIndex& index
    ) {
        if(reranked.size() > admitted.size()) {
            return {};
        }
        std::vector<agent_memory::LexicalSearchResult> result;
        result.reserve(reranked.size());
        std::unordered_set<Identity, IdentityHash> seen;
        seen.reserve(reranked.size());
        for(const auto& candidate : reranked) {
            if(!finite_score(candidate.score)) {
                return {};
            }
            const auto key = identity_of(candidate);
            const auto matching = index.entries.find(key);
            if(matching == index.entries.end() || !seen.emplace(key).second
               || !protected_equal(admitted[matching->second], candidate)) {
                return {};
            }
            auto trusted = admitted[matching->second];
            trusted.score = candidate.score;
            result.push_back(std::move(trusted));
        }
        return result;
    }

    [[nodiscard]] agent_memory::LexicalSearchResult make_hit(
        const std::size_t index,
        const std::size_t metadata_width
    ) {
        agent_memory::Metadata metadata;
        metadata.set("scope", "documents");
        metadata.set("owner", "admission-fixture");
        for(std::size_t key = 0; key < metadata_width; ++key) {
            metadata.set("attribute:" + std::to_string(key), std::to_string(index + key));
        }
        agent_memory::LexicalSearchResult result;
        result.chunk_id = agent_memory::ChunkId{"chunk:" + std::to_string(index / 2U)};
        result.resource_id = agent_memory::ResourceId{"resource:" + std::to_string(index / 4U)};
        result.section_id = static_cast<agent_memory::SectionId>(index / 2U);
        result.result_tier = static_cast<std::uint8_t>(index % 2U);
        result.enrichment_level = static_cast<std::uint32_t>(index % 4U);
        result.score = static_cast<float>(index) / 10.0F;
        result.metadata = std::move(metadata);
        return result;
    }

    [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> reranked_for(
        const std::vector<agent_memory::LexicalSearchResult>& admitted
    ) {
        std::vector<agent_memory::LexicalSearchResult> result = admitted;
        std::reverse(result.begin(), result.end());
        for(std::size_t index = 0; index < result.size(); ++index) {
            result[index].score = 1000.0F - static_cast<float>(index);
        }
        return result;
    }

    struct AllocationReceipt final {
        std::size_t count = 0;
        std::size_t bytes = 0;
    };

    template <typename Fn>
    auto measured(Fn&& fn, AllocationReceipt& allocations) {
        g_allocation_count = 0;
        g_allocated_bytes = 0;
        g_count_allocations = true;
        try {
            const auto started = Clock::now();
            auto result = fn();
            const auto elapsed = std::chrono::duration<double, std::milli>(
                Clock::now() - started
            ).count();
            g_count_allocations = false;
            allocations = AllocationReceipt{g_allocation_count, g_allocated_bytes};
            return std::pair{std::move(result), elapsed};
        } catch(...) {
            g_count_allocations = false;
            throw;
        }
    }

    [[nodiscard]] bool equivalent(
        const std::vector<agent_memory::LexicalSearchResult>& left,
        const std::vector<agent_memory::LexicalSearchResult>& right
    ) noexcept {
        if(left.size() != right.size()) {
            return false;
        }
        for(std::size_t index = 0; index < left.size(); ++index) {
            if(!protected_equal(left[index], right[index]) || left[index].score != right[index].score) {
                return false;
            }
        }
        return true;
    }

    [[nodiscard]] Json run_case(const std::size_t candidate_count, const std::size_t metadata_width) {
        std::vector<agent_memory::LexicalSearchResult> admitted;
        admitted.reserve(candidate_count);
        for(std::size_t index = 0; index < candidate_count; ++index) {
            admitted.push_back(make_hit(index, metadata_width));
        }
        const auto reranked = reranked_for(admitted);
        ProductionAdmissionRunner production(admitted, reranked);
        const auto sorted_started = Clock::now();
        const auto sorted = make_sorted_index(admitted);
        const auto sorted_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - sorted_started).count();
        const auto hash_started = Clock::now();
        const auto hashed = make_hash_index(admitted);
        const auto hash_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - hash_started).count();

        const auto expected = production.run(candidate_count);
        Json variants = Json::array();
        std::array<std::vector<double>, 3> samples;
        std::array<std::vector<std::size_t>, 3> allocation_counts;
        std::array<std::vector<std::size_t>, 3> allocation_bytes;
        for(auto& values : samples) {
            values.reserve(kRepeats);
        }
        for(std::size_t repeat = 0; repeat < kWarmups + kRepeats; ++repeat) {
            const auto& order = kOrders[repeat % kOrders.size()];
            for(const auto variant : order) {
                AllocationReceipt allocations;
                auto measured_result = measured([&] {
                    if(variant == 0U) {
                        return production.run(candidate_count);
                    }
                    if(variant == 1U) {
                        return sorted_admit(admitted, reranked, sorted);
                    }
                    return hash_admit(admitted, reranked, hashed);
                }, allocations);
                if(repeat >= kWarmups) {
                    samples[variant].push_back(measured_result.second);
                    allocation_counts[variant].push_back(allocations.count);
                    allocation_bytes[variant].push_back(allocations.bytes);
                }
                if(!equivalent(measured_result.first, expected)) {
                    throw std::runtime_error("admission variant changed the accepted result");
                }
            }
        }

        const auto summarize = [](std::vector<double> values) {
            const auto raw_values = values;
            std::sort(values.begin(), values.end());
            const auto at = [&values](const double fraction) {
                if(values.empty()) return 0.0;
                return values[static_cast<std::size_t>(fraction * static_cast<double>(values.size() - 1U))];
            };
            double total = 0.0;
            for(const auto value : values) total += value;
            return Json{
                {"samples_ms", raw_values},
                {"sample_count", values.size()}, {"mean_ms", values.empty() ? 0.0 : total / values.size()},
                {"p50_ms", at(0.50)}, {"p95_ms", at(0.95)}, {"min_ms", values.empty() ? 0.0 : values.front()},
                {"max_ms", values.empty() ? 0.0 : values.back()}
            };
        };
        const auto average_size = [](const std::vector<std::size_t>& values) {
            std::size_t total = 0;
            for(const auto value : values) total += value;
            return values.empty() ? 0.0 : static_cast<double>(total) / values.size();
        };
        for(unsigned variant = 0; variant < 3U; ++variant) {
            variants.push_back(Json{
                {"algorithm", variant == 0U ? "production_hybrid_engine_admission" : variant == 1U ? "sorted_identity_index" : "hash_identity_index"},
                {"timing", summarize(samples[variant])},
                {"allocation_counts", allocation_counts[variant]},
                {"allocated_bytes", allocation_bytes[variant]},
                {"allocation_count_mean", average_size(allocation_counts[variant])},
                {"allocated_bytes_mean", average_size(allocation_bytes[variant])},
                {"trusted_record_copy_count", expected.size()},
                {"correctness_parity", true}
            });
        }
        return Json{
            {"candidate_count", candidate_count},
            {"metadata_width", metadata_width},
            {"sorted_index_build_ms", sorted_build_ms},
            {"hash_index_build_ms", hash_build_ms},
            {"build_timing_scope", "index construction is measured separately from lookup/admission timing"},
            {"lookup_timing_scope", "production variant is the actual HybridRetrievalEngine retrieve() path; indexed variants measure admission lookup only"},
            {"variants", std::move(variants)}
        };
    }

    [[nodiscard]] bool self_test() {
        const auto admitted = std::vector<agent_memory::LexicalSearchResult>{
            make_hit(0, 2), make_hit(1, 2), make_hit(2, 2), make_hit(3, 2)
        };
        const auto reranked = reranked_for(admitted);
        ProductionAdmissionRunner production(admitted, reranked);
        const auto sorted = make_sorted_index(admitted);
        const auto hashed = make_hash_index(admitted);
        const auto expected = production.run(admitted.size());
        if(!equivalent(expected, sorted_admit(admitted, reranked, sorted))
           || !equivalent(expected, hash_admit(admitted, reranked, hashed))) {
            return false;
        }
        if(!production.index().last_query
           || production.index().last_query->metadata_filters.size() != 1U
           || production.index().last_query->metadata_filters.front().key != "scope"
           || production.index().last_query->metadata_filters.front().value != "documents") {
            return false;
        }
        auto duplicate = reranked;
        duplicate.back() = duplicate.front();
        auto foreign = reranked;
        foreign.front().chunk_id = agent_memory::ChunkId{"foreign"};
        auto metadata = reranked;
        metadata.front().metadata.set("scope", "foreign");
        auto resource = reranked;
        resource.front().resource_id = agent_memory::ResourceId{"foreign"};
        auto section = reranked;
        section.front().section_id = 999;
        auto nonfinite = reranked;
        nonfinite.front().score = std::numeric_limits<float>::quiet_NaN();
        auto too_many = reranked;
        too_many.push_back(too_many.back());
        ProductionAdmissionRunner duplicate_production(admitted, duplicate);
        ProductionAdmissionRunner foreign_production(admitted, foreign);
        ProductionAdmissionRunner metadata_production(admitted, metadata);
        ProductionAdmissionRunner resource_production(admitted, resource);
        ProductionAdmissionRunner section_production(admitted, section);
        ProductionAdmissionRunner nonfinite_production(admitted, nonfinite);
        ProductionAdmissionRunner too_many_production(admitted, too_many);
        return duplicate_production.run(admitted.size()).empty()
            && sorted_admit(admitted, duplicate, sorted).empty()
            && hash_admit(admitted, duplicate, hashed).empty()
            && foreign_production.run(admitted.size()).empty()
            && sorted_admit(admitted, foreign, sorted).empty()
            && hash_admit(admitted, foreign, hashed).empty()
            && metadata_production.run(admitted.size()).empty()
            && resource_production.run(admitted.size()).empty()
            && section_production.run(admitted.size()).empty()
            && nonfinite_production.run(admitted.size()).empty()
            && too_many_production.run(admitted.size()).empty();
    }

} // namespace

int main(int argc, char** argv) {
    if(argc > 1 && std::string_view(argv[1]) == "--self-test") {
        if(!self_test()) {
            std::cerr << "reranker-admission-scaling self-test: FAIL\n";
            return 1;
        }
        std::cout << "reranker-admission-scaling self-test: PASS\n";
        return 0;
    }
    const std::string output_path = argc > 1 ? argv[1] : "reranker-admission-scaling.json";
    try {
        Json report{
            {"schema_version", 1},
            {"benchmark_name", "reranker_admission_scaling"},
            {"scope", "isolated executable model of HybridRetrievalEngine admission contract; not a production latency claim"},
            {"source_commit", AGENT_MEMORY_RERANKER_ADMISSION_SOURCE_COMMIT},
            {"production_contract_source_commit", AGENT_MEMORY_RERANKER_ADMISSION_PRODUCTION_CONTRACT_COMMIT},
            {"production_contract", "HybridRetrievalEngine admission implementation merged by PR #625"},
            {"timing_semantics", "balanced six-order query-independent samples; warmups excluded; sample_count is not independent-request count"},
            {"metadata_widths", Json::array({2, 8, 32})},
            {"cases", Json::array({
                run_case(10, 2), run_case(50, 2), run_case(100, 8),
                run_case(500, 8), run_case(1000, 32)
            })},
            {"recommendation", "QUALITY_INCONCLUSIVE_FOR_PRODUCTION_REWRITE: compare isolated curves after #625 integration; no algorithm is selected by this receipt"}
        };
        std::ofstream output(output_path, std::ios::binary);
        if(!output) return 2;
        output << report.dump(2) << '\n';
        std::cout << report.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "reranker-admission-scaling: " << error.what() << '\n';
        return 1;
    }
}
