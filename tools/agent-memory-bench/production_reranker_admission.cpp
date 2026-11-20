#include <agent_memory/lexical/ILexicalIndex.hpp>
#include <agent_memory/lexical/IReranker.hpp>
#include <agent_memory/retrieval/HybridRetrievalEngine.hpp>
#include "LinearResearchHybridRetrievalEngine.hpp"
#include "SortedResearchHybridRetrievalEngine.hpp"
#include "HashResearchHybridRetrievalEngine.hpp"
#include "ProfiledLinearResearchHybridRetrievalEngine.hpp"
#include "ProfiledSortedResearchHybridRetrievalEngine.hpp"
#include "ProfiledHashResearchHybridRetrievalEngine.hpp"
#include "RerankerResearchProfile.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <new>
#include <optional>
#include <stdexcept>
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

#ifndef AGENT_MEMORY_PRODUCTION_RERANKER_SOURCE_COMMIT
#define AGENT_MEMORY_PRODUCTION_RERANKER_SOURCE_COMMIT "unconfigured"
#endif
#ifndef AGENT_MEMORY_PRODUCTION_RERANKER_EXPECTED_PR_HEAD_SHA
#define AGENT_MEMORY_PRODUCTION_RERANKER_EXPECTED_PR_HEAD_SHA "unconfigured"
#endif
#ifndef AGENT_MEMORY_PRODUCTION_RERANKER_SOURCE_MANIFEST_SHA256
#define AGENT_MEMORY_PRODUCTION_RERANKER_SOURCE_MANIFEST_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_PRODUCTION_RERANKER_FIXTURE_SHA256
#define AGENT_MEMORY_PRODUCTION_RERANKER_FIXTURE_SHA256 "unconfigured"
#endif

    constexpr std::size_t kWarmups = 2;
    constexpr std::size_t kRepeats = 12;
    constexpr std::size_t kProfileRepeats = 3;
    constexpr std::array<std::size_t, 6> kCandidateCounts{{10U, 50U, 100U, 500U, 1000U, 2000U}};
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
    ) {
        return left.chunk_id == right.chunk_id
            && left.resource_id == right.resource_id
            && left.section_id == right.section_id
            && left.result_tier == right.result_tier
            && left.metadata.values() == right.metadata.values()
            && left.enrichment_level == right.enrichment_level;
    }

    [[nodiscard]] bool finite_score(const float score) noexcept {
        return std::isfinite(score);
    }

    [[nodiscard]] bool matches_filters(
        const agent_memory::LexicalSearchResult& hit,
        const std::vector<agent_memory::MetadataFilter>& filters
    ) {
        return std::all_of(filters.begin(), filters.end(), [&hit](const auto& filter) {
            return hit.metadata.get(filter.key) == std::optional<std::string>{filter.value};
        });
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
            ++search_calls;
            std::vector<agent_memory::LexicalSearchResult> result;
            result.reserve(std::min(query.limit, m_hits.size()));
            for(const auto& hit : m_hits) {
                if(matches_filters(hit, query.metadata_filters)) {
                    result.push_back(hit);
                    if(result.size() == query.limit) {
                        break;
                    }
                }
            }
            return result;
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
        mutable std::size_t search_calls = 0;

    private:
        std::vector<agent_memory::LexicalSearchResult> m_hits;
    };

    enum class Variant {
        Linear,
        Sorted,
        Hash
    };

    thread_local std::size_t g_indexed_admission_calls = 0;

    [[nodiscard]] const char* variant_name(const Variant variant) noexcept {
        switch(variant) {
            case Variant::Linear: return "linear_scan";
            case Variant::Sorted: return "sorted_identity_index";
            case Variant::Hash: return "hash_identity_index";
        }
        return "unknown";
    }

    enum class Mutation {
        None,
        ForeignChunk,
        ForeignResource,
        MismatchedSection,
        ProtectedMetadata,
        DuplicateId,
        AllRepeatedIds,
        LateDuplicateId,
        NonFinite,
        PositiveInfinity,
        NegativeInfinity,
        UntrustedTier,
        UntrustedEnrichment,
        Oversized
    };

    struct SortedIndex final {
        std::vector<std::pair<Identity, std::size_t>> entries;
    };

    struct HashIndex final {
        std::unordered_map<Identity, std::size_t, IdentityHash> entries;
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

    template <bool Profiled>
    [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> admit_sorted(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked
    ) {
        if(reranked.size() > admitted.size()) {
            return {};
        }
        const auto index = [&admitted] {
            agent_memory_research::AdmissionStageTimer<Profiled> timer(
                agent_memory_research::AdmissionStage::LookupConstruction);
            return make_sorted_index(admitted);
        }();
        std::unordered_set<Identity, IdentityHash> seen;
        seen.reserve(reranked.size());
        std::vector<agent_memory::LexicalSearchResult> result;
        result.reserve(reranked.size());
        for(const auto& candidate : reranked) {
            agent_memory_research::AdmissionStageTimer<Profiled> validation_timer(
                agent_memory_research::AdmissionStage::ValidationAndLookup);
            if(!finite_score(candidate.score)) {
                return {};
            }
            const auto key = identity_of(candidate);
            const auto matching = std::lower_bound(
                index.entries.begin(), index.entries.end(),
                std::pair<Identity, std::size_t>{key, 0U}
            );
            if(matching == index.entries.end() || !(matching->first == key)
               || !seen.emplace(key).second
               || !protected_equal(admitted[matching->second], candidate)) {
                return {};
            }
            validation_timer.finish();
            agent_memory_research::AdmissionStageTimer<Profiled> copy_timer(
                agent_memory_research::AdmissionStage::TrustedResultCopy);
            auto trusted = admitted[matching->second];
            trusted.score = candidate.score;
            result.push_back(std::move(trusted));
        }
        return result;
    }

    template <bool Profiled>
    [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> admit_hash(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked
    ) {
        if(reranked.size() > admitted.size()) {
            return {};
        }
        const auto index = [&admitted] {
            agent_memory_research::AdmissionStageTimer<Profiled> timer(
                agent_memory_research::AdmissionStage::LookupConstruction);
            return make_hash_index(admitted);
        }();
        std::unordered_set<Identity, IdentityHash> seen;
        seen.reserve(reranked.size());
        std::vector<agent_memory::LexicalSearchResult> result;
        result.reserve(reranked.size());
        for(const auto& candidate : reranked) {
            agent_memory_research::AdmissionStageTimer<Profiled> validation_timer(
                agent_memory_research::AdmissionStage::ValidationAndLookup);
            if(!finite_score(candidate.score)) {
                return {};
            }
            const auto key = identity_of(candidate);
            const auto matching = index.entries.find(key);
            if(matching == index.entries.end() || !seen.emplace(key).second
               || !protected_equal(admitted[matching->second], candidate)) {
                return {};
            }
            validation_timer.finish();
            agent_memory_research::AdmissionStageTimer<Profiled> copy_timer(
                agent_memory_research::AdmissionStage::TrustedResultCopy);
            auto trusted = admitted[matching->second];
            trusted.score = candidate.score;
            result.push_back(std::move(trusted));
        }
        return result;
    }

    class MatchedAdmissionReranker final : public agent_memory::IReranker {
    public:
        explicit MatchedAdmissionReranker(const Mutation mutation)
            : m_mutation(mutation) {}

        [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> rerank(
            const std::string&,
            std::vector<agent_memory::LexicalSearchResult> candidates
        ) const override {
            std::reverse(candidates.begin(), candidates.end());
            for(std::size_t index = 0; index < candidates.size(); ++index) {
                candidates[index].score = 1000.0F - static_cast<float>(index);
            }
            apply_mutation(candidates);
            return candidates;
        }

    private:
        void apply_mutation(std::vector<agent_memory::LexicalSearchResult>& candidates) const {
            if(candidates.empty()) {
                return;
            }
            switch(m_mutation) {
                case Mutation::None: break;
                case Mutation::ForeignChunk:
                    candidates.front().chunk_id = agent_memory::ChunkId{"foreign:chunk"};
                    break;
                case Mutation::ForeignResource:
                    candidates.front().resource_id = agent_memory::ResourceId{"foreign:resource"};
                    break;
                case Mutation::MismatchedSection:
                    candidates.front().section_id = 999999U;
                    break;
                case Mutation::ProtectedMetadata:
                    candidates.front().metadata.set("owner", "mutated");
                    break;
                case Mutation::DuplicateId:
                    if(candidates.size() > 1U) {
                        candidates[1] = candidates[0];
                    }
                    break;
                case Mutation::AllRepeatedIds:
                    std::fill(candidates.begin() + 1, candidates.end(), candidates.front());
                    break;
                case Mutation::LateDuplicateId:
                    if(candidates.size() > 1U) {
                        candidates.back() = candidates.front();
                    }
                    break;
                case Mutation::NonFinite:
                    candidates.front().score = std::numeric_limits<float>::quiet_NaN();
                    break;
                case Mutation::PositiveInfinity:
                    candidates.front().score = std::numeric_limits<float>::infinity();
                    break;
                case Mutation::NegativeInfinity:
                    candidates.front().score = -std::numeric_limits<float>::infinity();
                    break;
                case Mutation::UntrustedTier:
                    candidates.front().result_tier = 9U;
                    break;
                case Mutation::UntrustedEnrichment:
                    candidates.front().enrichment_level = 999U;
                    break;
                case Mutation::Oversized:
                    candidates.push_back(candidates.back());
                    break;
            }
        }

        Mutation m_mutation;
    };

    struct RunResult final {
        agent_memory::RetrievalResponse response;
        std::optional<agent_memory::LexicalSearchQuery> query;
        std::size_t search_calls = 0;
        std::size_t indexed_admission_calls = 0;
        double retrieve_ms = 0.0;
        std::size_t retrieve_allocations = 0;
        std::size_t retrieve_bytes = 0;
        agent_memory_research::AdmissionProfile profile;
    };

    [[nodiscard]] agent_memory::LexicalSearchResult make_hit(
        const std::size_t index,
        const std::size_t metadata_width
    ) {
        agent_memory::Metadata metadata;
        metadata.set("scope", "documents");
        metadata.set("owner", "production-admission-fixture");
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

    [[nodiscard]] bool response_matches(
        const agent_memory::RetrievalResponse& response,
        const std::vector<agent_memory::LexicalSearchResult>& expected
    ) {
        if(response.size() != expected.size()) {
            return false;
        }
        for(std::size_t index = 0; index < expected.size(); ++index) {
            const auto& item = response.items[index];
            const auto& hit = expected[index];
            if(!protected_equal(item.lexical, hit) || item.lexical.score != hit.score
               || item.object.type != agent_memory::ObjectType::Chunk
               || item.object.metadata.values() != hit.metadata.values()
               || item.object.enrichment_level != hit.enrichment_level
               || (hit.result_tier == 0U
                   && item.object.id.value() != hit.chunk_id.value())
               || (hit.result_tier != 0U && !item.object.id.empty())) {
                return false;
            }
        }
        return true;
    }

    template <typename Engine>
    [[nodiscard]] RunResult run_engine(
        const Mutation mutation,
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::size_t limit
    ) {
        FixtureIndex index(admitted);
        g_indexed_admission_calls = 0;
        agent_memory_research::admission_profile = {};
        auto reranker = std::make_unique<MatchedAdmissionReranker>(mutation);
        Engine engine(index);
        engine.set_reranker(std::move(reranker));
        agent_memory::RetrievalRequest request;
        request.query = "memory";
        request.limit = limit;
        request.metadata_filters.push_back({"scope", "documents"});
        // The production query operates on an existing index and engine. Their
        // fixture construction is outside the primary timing/allocation scope.
        const bool previous_tracking = g_count_allocations;
        const auto setup_allocations = g_allocation_count;
        const auto setup_bytes = g_allocated_bytes;
        g_count_allocations = true;
        g_allocation_count = 0;
        g_allocated_bytes = 0;
        const auto started = Clock::now();
        agent_memory::RetrievalResponse response;
        try {
            response = engine.retrieve(request);
        } catch(...) {
            g_count_allocations = previous_tracking;
            g_allocation_count += setup_allocations;
            g_allocated_bytes += setup_bytes;
            throw;
        }
        const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
        const auto retrieve_allocations = g_allocation_count;
        const auto retrieve_bytes = g_allocated_bytes;
        g_count_allocations = previous_tracking;
        g_allocation_count += setup_allocations;
        g_allocated_bytes += setup_bytes;
        return RunResult{std::move(response), index.last_query, index.search_calls,
                         g_indexed_admission_calls, elapsed, retrieve_allocations, retrieve_bytes,
                         agent_memory_research::admission_profile};
    }

    [[nodiscard]] RunResult run_variant(
        const Variant variant,
        const Mutation mutation,
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::size_t limit,
        const bool profiled = false
    ) {
        switch(variant) {
            case Variant::Linear:
                if(profiled) {
                    return run_engine<agent_memory::ProfiledLinearResearchHybridRetrievalEngine>(mutation, admitted, limit);
                }
                return run_engine<agent_memory::LinearResearchHybridRetrievalEngine>(mutation, admitted, limit);
            case Variant::Sorted:
                if(profiled) {
                    return run_engine<agent_memory::ProfiledSortedResearchHybridRetrievalEngine>(mutation, admitted, limit);
                }
                return run_engine<agent_memory::SortedResearchHybridRetrievalEngine>(mutation, admitted, limit);
            case Variant::Hash:
                if(profiled) {
                    return run_engine<agent_memory::ProfiledHashResearchHybridRetrievalEngine>(mutation, admitted, limit);
                }
                return run_engine<agent_memory::HashResearchHybridRetrievalEngine>(mutation, admitted, limit);
        }
        throw std::runtime_error("unknown admission variant");
    }

    template <typename Fn>
    auto measured(Fn&& fn) {
        const auto started = Clock::now();
        auto result = fn();
        const auto elapsed = std::chrono::duration<double, std::milli>(
            Clock::now() - started
        ).count();
        return std::pair{std::move(result), elapsed};
    }

    [[nodiscard]] Json summarize(std::vector<double> values) {
        const auto raw_values = values;
        std::sort(values.begin(), values.end());
        const auto at = [&values](const double fraction) {
            return values[static_cast<std::size_t>(fraction * static_cast<double>(values.size() - 1U))];
        };
        double total = 0.0;
        for(const auto value : values) {
            total += value;
        }
        return Json{
            {"samples_ms", raw_values},
            {"sample_count", values.size()},
            {"mean_ms", total / static_cast<double>(values.size())},
            {"p50_ms", at(0.50)},
            {"p95_ms", at(0.95)},
            {"min_ms", values.front()},
            {"max_ms", values.back()}
        };
    }

    [[nodiscard]] Json profile_runs(
        const Variant variant,
        const Mutation mutation,
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::size_t candidate_count,
        const std::vector<agent_memory::LexicalSearchResult>& expected
    ) {
        constexpr auto stage_count = static_cast<std::size_t>(agent_memory_research::AdmissionStage::Count);
        constexpr std::array<const char*, stage_count> names{{
            "index_search", "trusted_pool_copy", "reranker", "admission_total",
            "lookup_construction", "validation_and_lookup", "trusted_result_copy",
            "response_materialization"
        }};
        std::array<std::vector<double>, stage_count> samples;
        std::array<std::vector<std::size_t>, stage_count> calls;
        std::vector<double> full_samples;
        std::vector<std::size_t> allocations;
        std::vector<std::size_t> allocated_bytes;
        for(std::size_t repeat = 0; repeat < kProfileRepeats + 1U; ++repeat) {
            const auto result = run_variant(variant, mutation, admitted, candidate_count, true);
            if((mutation == Mutation::None && !response_matches(result.response, expected))
               || (mutation != Mutation::None && !result.response.empty())
               || result.indexed_admission_calls != (variant == Variant::Linear ? 0U : 1U)) {
                throw std::runtime_error("instrumented retrieval changed parity or fail-closed behavior");
            }
            if(repeat == 0U) {
                continue;
            }
            full_samples.push_back(result.retrieve_ms);
            allocations.push_back(result.retrieve_allocations);
            allocated_bytes.push_back(result.retrieve_bytes);
            for(std::size_t stage = 0; stage < stage_count; ++stage) {
                samples[stage].push_back(result.profile.milliseconds[stage]);
                calls[stage].push_back(result.profile.calls[stage]);
            }
        }
        Json stages = Json::object();
        for(std::size_t stage = 0; stage < stage_count; ++stage) {
            stages[names[stage]] = Json{{"timing", summarize(samples[stage])}, {"calls", calls[stage]}};
        }
        return Json{
            {"execution_status", "EXECUTED"},
            {"timing_scope", "separate instrumented retrieve; direct stage timers, not subtraction; admission_total contains lookup/validation/trusted-result-copy"},
            {"instrumentation_overhead_included", true},
            {"primary_performance_evidence", false},
            {"warmups", 1},
            {"repeats", kProfileRepeats},
            {"full_retrieve_timing", summarize(full_samples)},
            {"allocation_counts", allocations},
            {"allocated_bytes", allocated_bytes},
            {"stages", stages}
        };
    }

    [[nodiscard]] Json run_case(
        const std::size_t candidate_count,
        const std::size_t metadata_width,
        const bool identity_sorted = false
    ) {
        std::vector<agent_memory::LexicalSearchResult> admitted;
        admitted.reserve(candidate_count + 1U);
        for(std::size_t index = 0; index < candidate_count; ++index) {
            admitted.push_back(make_hit(index, metadata_width));
        }
        if(identity_sorted) {
            std::sort(admitted.begin(), admitted.end(), [](const auto& left, const auto& right) {
                return identity_of(left) < identity_of(right);
            });
        }
        auto expected = admitted;
        std::reverse(expected.begin(), expected.end());
        for(std::size_t index = 0; index < expected.size(); ++index) {
            expected[index].score = 1000.0F - static_cast<float>(index);
        }
        auto decoy = make_hit(candidate_count + 1U, metadata_width);
        decoy.metadata.set("scope", "other-scope");
        admitted.insert(admitted.begin(), std::move(decoy));

        Json variants = Json::array();
        std::array<std::vector<double>, 3> samples;
        std::array<std::vector<double>, 3> setup_and_retrieve_samples;
        std::array<std::vector<std::size_t>, 3> allocation_counts;
        std::array<std::vector<std::size_t>, 3> allocation_bytes;
        Json execution_orders = Json::array();
        for(auto& values : samples) {
            values.reserve(kRepeats);
        }
        for(std::size_t repeat = 0; repeat < kWarmups + kRepeats; ++repeat) {
            const auto& order = kOrders[(repeat < kWarmups ? repeat : repeat - kWarmups) % kOrders.size()];
            if(repeat >= kWarmups) {
                execution_orders.push_back(order);
            }
            for(const unsigned variant : order) {
                const auto measured_result = measured([&] {
                    return run_variant(
                        static_cast<Variant>(variant), Mutation::None, admitted, candidate_count
                    );
                });
                if(!response_matches(measured_result.first.response, expected)
                   || measured_result.first.search_calls != 1U
                   || !measured_result.first.query
                   || measured_result.first.query->metadata_filters.size() != 1U
                   || measured_result.first.indexed_admission_calls != (variant == 0U ? 0U : 1U)) {
                    throw std::runtime_error("production-shaped variant changed the retrieval contract");
                }
                if(repeat >= kWarmups) {
                    samples[variant].push_back(measured_result.first.retrieve_ms);
                    setup_and_retrieve_samples[variant].push_back(measured_result.second);
                    allocation_counts[variant].push_back(measured_result.first.retrieve_allocations);
                    allocation_bytes[variant].push_back(measured_result.first.retrieve_bytes);
                }
            }
        }
        const auto means = [&samples] {
            std::array<double, 3> result{};
            for(unsigned variant = 0; variant < 3U; ++variant) {
                for(const auto value : samples[variant]) {
                    result[variant] += value;
                }
                result[variant] /= static_cast<double>(samples[variant].size());
            }
            return result;
        }();
        const auto average_size = [](const std::vector<std::size_t>& values) {
            std::size_t total = 0;
            for(const auto value : values) {
                total += value;
            }
            return static_cast<double>(total) / static_cast<double>(values.size());
        };
        for(unsigned variant = 0; variant < 3U; ++variant) {
            variants.push_back(Json{
                {"algorithm", variant_name(static_cast<Variant>(variant))},
                {"timing", summarize(samples[variant])},
                {"setup_and_retrieve_timing", summarize(setup_and_retrieve_samples[variant])},
                {"fixture_engine_setup_excluded", true},
                {"allocation_counts", allocation_counts[variant]},
                {"allocated_bytes", allocation_bytes[variant]},
                {"allocation_count_mean", average_size(allocation_counts[variant])},
                {"allocated_bytes_mean", average_size(allocation_bytes[variant])},
                {"full_retrieve_path", true},
                {"index_construction_included", true},
                {"correctness_parity", true},
                {"materialization_checked", true},
                {"fail_closed_controls_checked", true}
            });
            variants.back()["stage_profile"] = profile_runs(
                static_cast<Variant>(variant), Mutation::None, admitted, candidate_count, expected);
        }
        return Json{
            {"candidate_count", candidate_count},
            {"metadata_width", metadata_width},
            {"trusted_input_order", identity_sorted ? "identity_sorted" : "fixture_numeric_order"},
            {"reranker_output_order", "reverse_trusted_input"},
            {"execution_orders", execution_orders},
            {"timing_scope", "HybridRetrievalEngine::retrieve() only: fixture/engine constructed before timer; includes index search, reranker, per-query admission-index construction, validation and response materialization"},
            {"variants", std::move(variants)},
            {"mean_ms_by_variant", Json{
                {variant_name(Variant::Linear), means[0]},
                {variant_name(Variant::Sorted), means[1]},
                {variant_name(Variant::Hash), means[2]}
            }}
        };
    }

    [[nodiscard]] Json negative_workloads() {
        Json result = Json::array();
        for(const auto count : {10U, 100U, 2000U}) {
            for(const auto width : {2U, 32U}) {
                std::vector<agent_memory::LexicalSearchResult> admitted;
                for(std::size_t index = 0; index < count; ++index) {
                    admitted.push_back(make_hit(index, width));
                }
                for(const auto mutation : {Mutation::AllRepeatedIds, Mutation::LateDuplicateId}) {
                    for(unsigned variant = 0; variant < 3U; ++variant) {
                        auto profile = profile_runs(static_cast<Variant>(variant), mutation, admitted, count, {});
                        const auto validated = mutation == Mutation::AllRepeatedIds ? 2U : count;
                        const auto copied = validated - 1U;
                        for(const auto value : profile["stages"]["validation_and_lookup"]["calls"]) {
                            if(value.get<std::size_t>() != validated) {
                                throw std::runtime_error("negative workload did not reach claimed validation depth");
                            }
                        }
                        for(const auto value : profile["stages"]["trusted_result_copy"]["calls"]) {
                            if(value.get<std::size_t>() != copied) {
                                throw std::runtime_error("negative workload changed trusted-copy count");
                            }
                        }
                        result.push_back(Json{
                            {"candidate_count", count}, {"metadata_width", width},
                            {"algorithm", variant_name(static_cast<Variant>(variant))},
                            {"mutation", mutation == Mutation::AllRepeatedIds ? "all_repeated_ids" : "late_duplicate_id"},
                            {"rejected", true}, {"validated_candidates", validated},
                            {"trusted_copies_before_rejection", copied},
                            {"stage_profile", std::move(profile)}
                        });
                    }
                }
            }
        }
        return result;
    }

    [[nodiscard]] bool self_test() {
        std::vector<agent_memory::LexicalSearchResult> admitted{
            make_hit(0U, 2U), make_hit(1U, 2U), make_hit(2U, 2U), make_hit(3U, 2U)
        };
        auto expected = admitted;
        std::reverse(expected.begin(), expected.end());
        for(std::size_t index = 0; index < expected.size(); ++index) {
            expected[index].score = 1000.0F - static_cast<float>(index);
        }
        // The baseline copy must agree with the unchanged, linked production engine.
        const auto production = run_engine<agent_memory::HybridRetrievalEngine>(Mutation::None, admitted, 4U);
        if(!response_matches(production.response, expected)) {
            return false;
        }
        auto decoy = make_hit(100U, 2U);
        decoy.metadata.set("scope", "foreign-scope");
        admitted.insert(admitted.begin(), std::move(decoy));
        for(unsigned variant = 0; variant < 3U; ++variant) {
            const auto valid = run_variant(static_cast<Variant>(variant), Mutation::None, admitted, 4U);
            if(!response_matches(valid.response, expected)
               || valid.search_calls != 1U
               || !valid.query
               || valid.query->metadata_filters.size() != 1U
               || valid.query->metadata_filters.front().key != "scope"
               || valid.query->metadata_filters.front().value != "documents"
               || valid.indexed_admission_calls != (variant == 0U ? 0U : 1U)
               || valid.response.items.front().object.metadata.values()
                    != valid.response.items.front().lexical.metadata.values()) {
                return false;
            }
            for(const auto mutation : {
                    Mutation::ForeignChunk, Mutation::ForeignResource,
                    Mutation::MismatchedSection, Mutation::ProtectedMetadata,
                    Mutation::DuplicateId, Mutation::NonFinite,
                    Mutation::AllRepeatedIds, Mutation::LateDuplicateId,
                    Mutation::PositiveInfinity, Mutation::NegativeInfinity,
                    Mutation::UntrustedTier, Mutation::UntrustedEnrichment,
                    Mutation::Oversized
                }) {
                for(const bool instrumented : {false, true}) {
                    const auto invalid = run_variant(
                        static_cast<Variant>(variant), mutation, admitted, 4U, instrumented
                    );
                    if(!invalid.response.empty()
                       || invalid.indexed_admission_calls != (variant == 0U ? 0U : 1U)) {
                        return false;
                    }
                }
            }
            const auto profiled = run_variant(static_cast<Variant>(variant), Mutation::None, admitted, 4U, true);
            if(!response_matches(profiled.response, expected)
               || profiled.profile.calls[static_cast<std::size_t>(agent_memory_research::AdmissionStage::ValidationAndLookup)] != 4U
               || profiled.profile.calls[static_cast<std::size_t>(agent_memory_research::AdmissionStage::TrustedResultCopy)] != 4U) {
                return false;
            }
        }
        return true;
    }

} // namespace

namespace agent_memory_research {

    std::vector<agent_memory::LexicalSearchResult> admit_sorted(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked
    ) {
        ++g_indexed_admission_calls;
        return ::admit_sorted<false>(admitted, reranked);
    }

    std::vector<agent_memory::LexicalSearchResult> admit_hash(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked
    ) {
        ++g_indexed_admission_calls;
        return ::admit_hash<false>(admitted, reranked);
    }

    std::vector<agent_memory::LexicalSearchResult> admit_sorted_profiled(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked
    ) {
        ++g_indexed_admission_calls;
        return ::admit_sorted<true>(admitted, reranked);
    }

    std::vector<agent_memory::LexicalSearchResult> admit_hash_profiled(
        const std::vector<agent_memory::LexicalSearchResult>& admitted,
        const std::vector<agent_memory::LexicalSearchResult>& reranked
    ) {
        ++g_indexed_admission_calls;
        return ::admit_hash<true>(admitted, reranked);
    }

} // namespace agent_memory_research

int main(int argc, char** argv) {
    if(argc > 1 && std::string_view(argv[1]) == "--self-test") {
        if(!self_test()) {
            std::cerr << "production-shaped reranker admission self-test: FAIL\n";
            return 1;
        }
        std::cout << "production-shaped reranker admission self-test: PASS\n";
        return 0;
    }
    const std::string output_path = argc > 1 ? argv[1] : "production-reranker-admission.json";
    try {
        if(!self_test()) {
            throw std::runtime_error("fail-closed or materialization controls failed");
        }
        Json cases = Json::array();
        Json sorted_input_cases = Json::array();
        Json break_even = Json::array();
        for(const auto width : {2U, 8U, 32U}) {
            std::optional<std::size_t> sorted_first;
            std::optional<std::size_t> hash_first;
            for(const auto count : kCandidateCounts) {
                auto item = run_case(count, width);
                const auto& means = item.at("mean_ms_by_variant");
                if(!sorted_first && means.at("sorted_identity_index").get<double>()
                        <= means.at("linear_scan").get<double>()) {
                    sorted_first = count;
                }
                if(!hash_first && means.at("hash_identity_index").get<double>()
                        <= means.at("linear_scan").get<double>()) {
                    hash_first = count;
                }
                cases.push_back(std::move(item));
            }
            break_even.push_back(Json{
                {"metadata_width", width},
                {"sorted_first_tested_count", sorted_first ? Json(*sorted_first) : Json(nullptr)},
                {"hash_first_tested_count", hash_first ? Json(*hash_first) : Json(nullptr)}
            });
        }
        for(const auto count : kCandidateCounts) {
            sorted_input_cases.push_back(run_case(count, 8U, true));
        }
        Json report{
            {"schema_version", 2},
            {"benchmark_name", "production_reranker_admission"},
            {"scope", "production-shaped matched research harness; no production rewrite or retrieval-default change"},
            {"source_commit", AGENT_MEMORY_PRODUCTION_RERANKER_SOURCE_COMMIT},
            {"expected_pr_head_sha", AGENT_MEMORY_PRODUCTION_RERANKER_EXPECTED_PR_HEAD_SHA},
            {"source_manifest_sha256", AGENT_MEMORY_PRODUCTION_RERANKER_SOURCE_MANIFEST_SHA256},
            {"fixture_sha256", AGENT_MEMORY_PRODUCTION_RERANKER_FIXTURE_SHA256},
            {"generated_source_sha256", Json{
                {"LinearResearchHybridRetrievalEngine.cpp", AGENT_MEMORY_PRODUCTION_RERANKER_Linear_SHA256},
                {"SortedResearchHybridRetrievalEngine.cpp", AGENT_MEMORY_PRODUCTION_RERANKER_Sorted_SHA256},
                {"HashResearchHybridRetrievalEngine.cpp", AGENT_MEMORY_PRODUCTION_RERANKER_Hash_SHA256},
                {"ProfiledLinearResearchHybridRetrievalEngine.cpp", AGENT_MEMORY_PRODUCTION_RERANKER_ProfiledLinear_SHA256},
                {"ProfiledSortedResearchHybridRetrievalEngine.cpp", AGENT_MEMORY_PRODUCTION_RERANKER_ProfiledSorted_SHA256},
                {"ProfiledHashResearchHybridRetrievalEngine.cpp", AGENT_MEMORY_PRODUCTION_RERANKER_ProfiledHash_SHA256}
            }},
            {"build_environment", Json{
                {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
                {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
                {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
                {"build_configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
                {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
                {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
                {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS}
            }},
            {"production_contract", "HybridRetrievalEngine::retrieve() admission implementation merged by PR #625"},
            {"candidate_counts", kCandidateCounts},
            {"metadata_widths", Json::array({2, 8, 32})},
            {"warmups", kWarmups},
            {"repeats", kRepeats},
            {"timing_semantics", "raw samples are repeated executions of the same fixture; sample_count is not independent-request count"},
            {"variants", Json::array({
                "linear_scan", "sorted_identity_index", "hash_identity_index"
            })},
            {"fail_closed_controls", Json::array({
                "foreign_chunk", "foreign_resource", "mismatched_section",
                "protected_metadata_mutation", "duplicate_id", "non_finite_score",
                "positive_infinity", "negative_infinity", "untrusted_tier",
                "untrusted_enrichment", "oversized_output", "all_repeated_ids", "late_duplicate_id"
            })},
            {"cases", cases},
            {"sorted_input_cases", sorted_input_cases},
            {"negative_workloads", negative_workloads()},
            {"candidate_count_distribution", Json{
                {"execution_status", "NOT_EXECUTED"},
                {"reason", "REPRESENTATIVE_REQUEST_DISTRIBUTION_UNAVAILABLE"},
                {"source", "fixed synthetic fixtures only; no production request log or representative request-frequency sample is supplied"},
                {"synthetic_case_counts_are_request_frequencies", false}
            }},
            {"memory_scope", Json{
                {"allocation_series", "ordinary new/new[] requests within retrieve; excludes direct C and over-aligned APIs"},
                {"peak_retained_workspace", "NOT_MEASURED"},
                {"peak_retained_reason", "allocation counters do not track live allocation sizes on deallocation; allocated_bytes is not retained or peak memory"},
                {"process_peak_rss", "NOT_MEASURED"}
            }},
            {"production_default_decision", "KEEP CURRENT"},
            {"conditional_dispatch_decision", "DEFER: representative candidate-count frequencies unavailable; no threshold selected from synthetic host"},
            {"break_even", Json{
                {"definition", "first tested candidate count where sorted/hash mean_ms <= linear mean_ms"},
                {"scope", "this machine and fixed metadata width only; first observed crossover is not a stable production threshold"},
                {"by_metadata_width", break_even}
            }},
            {"recommendation", "QUALIFICATION_ONLY: end-to-end parity is established for this fixture; no production admission algorithm is selected"}
        };
        std::ofstream output(output_path, std::ios::binary);
        if(!output) {
            return 2;
        }
        output << report.dump(2) << '\n';
        output.close();
        if(!output) {
            throw std::runtime_error("could not write complete receipt");
        }
        std::cout << "production-reranker-admission: PASS; cases=" << cases.size()
                  << "; receipt=" << output_path << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "production-reranker-admission: " << error.what() << '\n';
        return 1;
    }
}
