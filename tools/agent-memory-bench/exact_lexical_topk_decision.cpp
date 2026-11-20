#include <agent_memory/lexical/ExactLexicalIndex.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/retrieval/HybridRetrievalEngine.hpp>
#include "ResearchPartialSortExactLexicalIndex.hpp"
#include "ResearchNthElementExactLexicalIndex.hpp"
#include "ResearchBoundedHeapExactLexicalIndex.hpp"
#include "ResearchProfiledFullSortExactLexicalIndex.hpp"
#include "ResearchProfiledPartialSortExactLexicalIndex.hpp"
#include "ResearchProfiledNthElementExactLexicalIndex.hpp"
#include "ResearchProfiledBoundedHeapExactLexicalIndex.hpp"
#include "lexical_topk_stage_profile.hpp"
#include "lexical_topk_profile_fixture.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <initializer_list>
#include <memory>
#include <iostream>
#include <new>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {

    using Json = nlohmann::json;
    using Clock = std::chrono::steady_clock;
    using Hit = agent_memory::LexicalSearchResult;

#ifndef AGENT_MEMORY_LEXICAL_TOPK_SOURCE_COMMIT
#define AGENT_MEMORY_LEXICAL_TOPK_SOURCE_COMMIT "unconfigured"
#endif
#ifndef AGENT_MEMORY_LEXICAL_TOPK_EXPECTED_PR_HEAD_SHA
#define AGENT_MEMORY_LEXICAL_TOPK_EXPECTED_PR_HEAD_SHA "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256
#define AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256
#define AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_COMPILER_ID
#define AGENT_MEMORY_EVALUATOR_COMPILER_ID "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_COMPILER_VERSION
#define AGENT_MEMORY_EVALUATOR_COMPILER_VERSION "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_GENERATOR
#define AGENT_MEMORY_EVALUATOR_GENERATOR "unconfigured"
#endif

    constexpr std::size_t kWarmups = 2;
    constexpr std::size_t kRepeats = 8;
    constexpr std::array<std::size_t, 4> kDocumentCounts{{128U, 1024U, 8192U, 65536U}};
    constexpr std::array<std::string_view, 4> kVariants{{
        "full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"
    }};
    using IndexSet = std::array<std::unique_ptr<agent_memory::ILexicalIndex>, kVariants.size()>;
    constexpr std::array<std::string_view, 7> kStages{{
        "candidate_generation", "metadata_filtering", "bm25_scoring", "candidate_construction",
        "top_k_selection", "result_materialization", "full_search"
    }};

    thread_local bool g_count_allocations = false;
    thread_local std::size_t g_allocation_count = 0;
    thread_local std::size_t g_allocated_bytes = 0;

    void* allocate_bytes(const std::size_t size) {
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

    void* allocate_aligned_bytes(const std::size_t size, const std::size_t alignment) {
        const auto actual = size == 0U
            ? alignment
            : ((size + alignment - 1U) / alignment) * alignment;
        if(g_count_allocations) {
            ++g_allocation_count;
            g_allocated_bytes += actual;
        }
#if defined(_WIN32)
        if(auto* result = _aligned_malloc(actual, alignment); result != nullptr) {
            return result;
        }
#else
        void* result = nullptr;
        if(posix_memalign(&result, alignment, actual) == 0) {
            return result;
        }
#endif
        throw std::bad_alloc{};
    }

    void reset_allocation_counters() noexcept {
        g_allocation_count = 0;
        g_allocated_bytes = 0;
    }

    void begin_allocation_measurement() noexcept {
        reset_allocation_counters();
        g_count_allocations = true;
    }

    void end_allocation_measurement() noexcept {
        g_count_allocations = false;
    }

    struct AllocationSample final {
        std::size_t count = 0;
        std::size_t bytes = 0;
    };

    [[nodiscard]] AllocationSample allocation_sample() noexcept {
        return {g_allocation_count, g_allocated_bytes};
    }

} // namespace

void* operator new(const std::size_t size) {
    return allocate_bytes(size);
}

void* operator new[](const std::size_t size) {
    return allocate_bytes(size);
}

void* operator new(const std::size_t size, const std::nothrow_t&) noexcept {
    try { return allocate_bytes(size); } catch(const std::bad_alloc&) { return nullptr; }
}

void* operator new[](const std::size_t size, const std::nothrow_t&) noexcept {
    try { return allocate_bytes(size); } catch(const std::bad_alloc&) { return nullptr; }
}

void* operator new(const std::size_t size, const std::align_val_t alignment) {
    return allocate_aligned_bytes(size, static_cast<std::size_t>(alignment));
}

void* operator new[](const std::size_t size, const std::align_val_t alignment) {
    return allocate_aligned_bytes(size, static_cast<std::size_t>(alignment));
}

void* operator new(
    const std::size_t size,
    const std::align_val_t alignment,
    const std::nothrow_t&
) noexcept {
    try { return allocate_aligned_bytes(size, static_cast<std::size_t>(alignment)); }
    catch(const std::bad_alloc&) { return nullptr; }
}

void* operator new[](
    const std::size_t size,
    const std::align_val_t alignment,
    const std::nothrow_t&
) noexcept {
    try { return allocate_aligned_bytes(size, static_cast<std::size_t>(alignment)); }
    catch(const std::bad_alloc&) { return nullptr; }
}

void operator delete(void* pointer) noexcept { std::free(pointer); }
void operator delete[](void* pointer) noexcept { std::free(pointer); }
void operator delete(void* pointer, const std::size_t) noexcept { std::free(pointer); }
void operator delete[](void* pointer, const std::size_t) noexcept { std::free(pointer); }
void operator delete(void* pointer, const std::nothrow_t&) noexcept { std::free(pointer); }
void operator delete[](void* pointer, const std::nothrow_t&) noexcept { std::free(pointer); }
void operator delete(void* pointer, const std::align_val_t) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
void operator delete[](void* pointer, const std::align_val_t) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
void operator delete(void* pointer, const std::size_t, const std::align_val_t) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
void operator delete[](void* pointer, const std::size_t, const std::align_val_t) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
void operator delete(void* pointer, const std::align_val_t, const std::nothrow_t&) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
void operator delete[](void* pointer, const std::align_val_t, const std::nothrow_t&) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}

namespace {

    struct Candidate final {
        Hit hit;
        static thread_local std::size_t copies;
        static thread_local std::size_t moves;

        Candidate() = default;
        explicit Candidate(Hit value) : hit(std::move(value)) {}
        Candidate(const Candidate& other) : hit(other.hit) { ++copies; }
        Candidate(Candidate&& other) noexcept(std::is_nothrow_move_constructible_v<Hit>)
            : hit(std::move(other.hit)) { ++moves; }
        Candidate& operator=(const Candidate& other) {
            hit = other.hit;
            ++copies;
            return *this;
        }
        Candidate& operator=(Candidate&& other) noexcept(std::is_nothrow_move_assignable_v<Hit>) {
            hit = std::move(other.hit);
            ++moves;
            return *this;
        }
    };

    static_assert(std::is_nothrow_move_constructible_v<Candidate> == std::is_nothrow_move_constructible_v<Hit>);
    static_assert(std::is_nothrow_move_assignable_v<Candidate> == std::is_nothrow_move_assignable_v<Hit>);

    thread_local std::size_t Candidate::copies = 0;
    thread_local std::size_t Candidate::moves = 0;

    void reset_candidate_counters() noexcept {
        Candidate::copies = 0;
        Candidate::moves = 0;
    }

    struct Better final {
        bool operator()(const Candidate& lhs, const Candidate& rhs) const noexcept {
            if(lhs.hit.score == rhs.hit.score) {
                return lhs.hit.chunk_id < rhs.hit.chunk_id;
            }
            return lhs.hit.score > rhs.hit.score;
        }
    };

    using Selector = std::vector<Candidate>(*)(
        const std::vector<Candidate>&,
        std::size_t
    );

    [[nodiscard]] std::vector<Candidate> full_sort(
        const std::vector<Candidate>& input,
        const std::size_t limit
    ) {
        auto result = input;
        std::sort(result.begin(), result.end(), Better{});
        if(result.size() > limit) {
            result.resize(limit);
        }
        return result;
    }

    [[nodiscard]] std::vector<Candidate> partial_sort(
        const std::vector<Candidate>& input,
        const std::size_t limit
    ) {
        auto result = input;
        const auto effective = std::min(limit, result.size());
        if(effective > 0U) {
            std::partial_sort(result.begin(), result.begin() + effective, result.end(), Better{});
            result.resize(effective);
        } else {
            result.clear();
        }
        return result;
    }

    [[nodiscard]] std::vector<Candidate> nth_element_then_sort(
        const std::vector<Candidate>& input,
        const std::size_t limit
    ) {
        auto result = input;
        const auto effective = std::min(limit, result.size());
        if(effective > 0U && effective < result.size()) {
            std::nth_element(result.begin(), result.begin() + effective, result.end(), Better{});
        }
        if(effective > 0U) {
            std::sort(result.begin(), result.begin() + effective, Better{});
        }
        result.resize(effective);
        return result;
    }

    [[nodiscard]] std::vector<Candidate> bounded_heap(
        const std::vector<Candidate>& input,
        const std::size_t limit
    ) {
        const auto effective = std::min(limit, input.size());
        std::vector<Candidate> heap;
        heap.reserve(effective);
        if(effective == 0U) {
            return heap;
        }
        for(const auto& candidate : input) {
            if(heap.size() < effective) {
                heap.push_back(candidate);
                std::push_heap(heap.begin(), heap.end(), Better{});
            } else if(Better{}(candidate, heap.front())) {
                std::pop_heap(heap.begin(), heap.end(), Better{});
                heap.back() = candidate;
                std::push_heap(heap.begin(), heap.end(), Better{});
            }
        }
        std::sort(heap.begin(), heap.end(), Better{});
        return heap;
    }

    [[nodiscard]] Selector selector_for(const std::string_view name) {
        if(name == "full_sort") return full_sort;
        if(name == "partial_sort") return partial_sort;
        if(name == "nth_element_then_sort") return nth_element_then_sort;
        return bounded_heap;
    }

    [[nodiscard]] bool same_results(
        const std::vector<Candidate>& lhs,
        const std::vector<Candidate>& rhs
    ) noexcept {
        if(lhs.size() != rhs.size()) return false;
        for(std::size_t i = 0; i < lhs.size(); ++i) {
            const auto& left = lhs[i].hit;
            const auto& right = rhs[i].hit;
            if(left.chunk_id != right.chunk_id || left.score != right.score
               || left.metadata.values() != right.metadata.values()
               || left.resource_id != right.resource_id || left.section_id != right.section_id
               || left.enrichment_level != right.enrichment_level || left.result_tier != right.result_tier) {
                return false;
            }
        }
        return true;
    }

    [[nodiscard]] agent_memory::Token token(
        const std::string& text,
        const std::size_t position
    ) {
        return agent_memory::Token{
            text,
            agent_memory::TextRange{position, text.size()},
            position,
            agent_memory::TokenKind::Word
        };
    }

    [[nodiscard]] std::string chunk_name(const std::size_t index) {
        std::ostringstream stream;
        stream << "chunk:" << index;
        return stream.str();
    }

    [[nodiscard]] std::unique_ptr<agent_memory::ILexicalIndex> make_index(const std::size_t variant) {
        if(variant == 0U) return std::make_unique<agent_memory::ExactLexicalIndex>();
        if(variant == 1U) return std::make_unique<agent_memory::ResearchPartialSortExactLexicalIndex>();
        if(variant == 2U) return std::make_unique<agent_memory::ResearchNthElementExactLexicalIndex>();
        return std::make_unique<agent_memory::ResearchBoundedHeapExactLexicalIndex>();
    }

    [[nodiscard]] std::unique_ptr<agent_memory::ILexicalIndex> make_profiled_index(const std::size_t variant) {
        if(variant == 0U) return std::make_unique<agent_memory::ResearchProfiledFullSortExactLexicalIndex>();
        if(variant == 1U) return std::make_unique<agent_memory::ResearchProfiledPartialSortExactLexicalIndex>();
        if(variant == 2U) return std::make_unique<agent_memory::ResearchProfiledNthElementExactLexicalIndex>();
        return std::make_unique<agent_memory::ResearchProfiledBoundedHeapExactLexicalIndex>();
    }

    void populate_index(
        agent_memory::ILexicalIndex& index,
        const std::size_t count
    ) {
        for(std::size_t i = 0; i < count; ++i) {
            agent_memory::Metadata metadata;
            metadata.set("scope", i % 5U == 0U ? "private" : "public");
            metadata.set("tenant", i % 2U == 0U ? "alpha" : "beta");
            std::vector<agent_memory::Token> tokens;
            tokens.push_back(token("common", 0));
            tokens.push_back(token("common", 1));
            tokens.push_back(token(i % 7U == 0U ? "anchor" : "filler", 2));
            index.upsert(agent_memory::LexicalDocumentRecord{
                agent_memory::ChunkId{chunk_name(i)},
                agent_memory::ResourceRevision{
                    agent_memory::ResourceId{"resource:synthetic"}, 1, 11, 17
                },
                std::move(tokens), metadata
            });
        }
    }

    struct CaseDefinition final {
        std::string name;
        std::vector<std::string> terms;
        std::size_t limit = 0;
    };

    [[nodiscard]] agent_memory::LexicalSearchQuery query_for(
        const CaseDefinition& definition,
        const std::size_t limit
    ) {
        agent_memory::LexicalSearchQuery query;
        query.terms = definition.terms;
        query.limit = limit;
        query.metadata_filters = {agent_memory::MetadataFilter{"scope", "public"}};
        return query;
    }

    [[nodiscard]] std::vector<Candidate> candidates_from(
        const std::vector<Hit>& hits
    ) {
        std::vector<Candidate> candidates;
        candidates.reserve(hits.size());
        for(const auto& hit : hits) candidates.emplace_back(hit);
        return candidates;
    }

    [[nodiscard]] Json measure_end_to_end(
        const IndexSet& indexes,
        const agent_memory::LexicalSearchQuery& query,
        const std::vector<Candidate>& expected
    ) {
        Json variants = Json::object();
        for(const auto name : kVariants) {
            variants[std::string(name)] = Json{
                {"samples_ms", Json::array()}, {"sample_count", kRepeats},
                {"allocation_counts", Json::array()}, {"allocated_bytes", Json::array()},
                {"metadata_entries_materialized", Json::array()},
                {"result_count", expected.size()}, {"exact_parity", true},
                {"warmups", kWarmups}
            };
        }
        for(const auto& index : indexes) {
            for(std::size_t i = 0; i < kWarmups; ++i) (void)index->search(query);
        }
        Json orders = Json::array();
        for(std::size_t repeat = 0; repeat < kRepeats; ++repeat) {
            Json order = Json::array();
            for(std::size_t offset = 0; offset < kVariants.size(); ++offset) {
                const auto variant_index = (repeat + offset) % kVariants.size();
                const auto name = std::string(kVariants[variant_index]);
                order.push_back(name);
                begin_allocation_measurement();
                const auto started = Clock::now();
                const auto hits = indexes[variant_index]->search(query);
                const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
                end_allocation_measurement();
                const auto allocation = allocation_sample();
                const auto parity = same_results(candidates_from(hits), expected);
                std::size_t metadata_entries = 0;
                for(const auto& hit : hits) metadata_entries += hit.metadata.size();
                auto& result = variants[name];
                result["samples_ms"].push_back(elapsed);
                result["allocation_counts"].push_back(allocation.count);
                result["allocated_bytes"].push_back(allocation.bytes);
                result["metadata_entries_materialized"].push_back(metadata_entries);
                result["result_count"] = hits.size();
                result["exact_parity"] = result["exact_parity"].get<bool>() && parity;
            }
            orders.push_back(std::move(order));
        }
        return Json{
            {"variants", variants}, {"execution_orders", orders},
            {"warmups", kWarmups},
            {"timing_scope", "matched end_to_end ExactLexicalIndex::search; identical scoring/filter/metadata; selection block only differs; no I/O"}
        };
    }

    [[nodiscard]] Json measure_kernel(
        const std::vector<Candidate>& input,
        const std::vector<Candidate>& expected,
        const std::size_t limit
    ) {
        std::array<std::vector<double>, kVariants.size()> times;
        std::array<std::vector<std::size_t>, kVariants.size()> allocations;
        std::array<std::vector<std::size_t>, kVariants.size()> bytes;
        std::array<std::vector<std::size_t>, kVariants.size()> copies;
        std::array<std::vector<std::size_t>, kVariants.size()> moves;
        std::array<bool, kVariants.size()> parity{};
        parity.fill(true);
        for(std::size_t i = 0; i < kVariants.size(); ++i) {
            times[i].reserve(kRepeats);
            allocations[i].reserve(kRepeats);
            bytes[i].reserve(kRepeats);
            copies[i].reserve(kRepeats);
            moves[i].reserve(kRepeats);
            for(std::size_t warmup = 0; warmup < kWarmups; ++warmup) {
                (void)selector_for(kVariants[i])(input, limit);
            }
        }
        Json execution_orders = Json::array();
        std::array<std::size_t, kVariants.size()> order{};
        for(std::size_t repeat = 0; repeat < kRepeats; ++repeat) {
            Json execution_order = Json::array();
            for(std::size_t offset = 0; offset < kVariants.size(); ++offset) {
                const auto variant_index = (repeat + offset) % kVariants.size();
                order[offset] = variant_index;
                execution_order.push_back(std::string(kVariants[variant_index]));
            }
            execution_orders.push_back(std::move(execution_order));
            for(const auto variant_index : order) {
                reset_candidate_counters();
                begin_allocation_measurement();
                const auto started = Clock::now();
                const auto result = selector_for(kVariants[variant_index])(input, limit);
                const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
                end_allocation_measurement();
                const auto allocation = allocation_sample();
                parity[variant_index] = parity[variant_index] && same_results(result, expected);
                times[variant_index].push_back(elapsed);
                allocations[variant_index].push_back(allocation.count);
                bytes[variant_index].push_back(allocation.bytes);
                copies[variant_index].push_back(Candidate::copies);
                moves[variant_index].push_back(Candidate::moves);
            }
        }
        Json variants = Json::object();
        for(std::size_t i = 0; i < kVariants.size(); ++i) {
            variants[std::string(kVariants[i])] = Json{
                {"samples_ms", times[i]},
                {"sample_count", times[i].size()},
                {"allocation_counts", allocations[i]},
                {"allocated_bytes", bytes[i]},
                {"copy_counts", copies[i]},
                {"move_counts", moves[i]},
                {"exact_parity", parity[i]},
                {"interleaved", true},
                {"warmups", kWarmups}
            };
        }
        return Json{
            {"variants", variants},
            {"execution_orders", execution_orders},
            {"execution_order_policy", "cyclic rotation per repeat"},
            {"timing_scope", "kernel-only selection over filtered scored LexicalSearchResult candidates; no scoring/filter/I/O"},
            {"warmups", kWarmups},
            {"repeats", kRepeats}
        };
    }

    [[nodiscard]] Json run_case(
        const IndexSet& indexes,
        const std::size_t document_count,
        const CaseDefinition& definition
    ) {
        const auto all_query = query_for(definition, document_count);
        const auto all_hits = indexes[0]->search(all_query);
        auto input = candidates_from(all_hits);
        // search() emits ranked results. Restore its pre-selection map order
        // before comparing selectors; ranking the already-ranked output would
        // make the kernel workload artificially favorable to sorted inputs.
        std::sort(input.begin(), input.end(), [](const auto& lhs, const auto& rhs) {
            return lhs.hit.chunk_id < rhs.hit.chunk_id;
        });
        const auto expected = full_sort(input, definition.limit);
        const auto production_query = query_for(definition, definition.limit);
        return Json{
            {"name", definition.name},
            {"document_count", document_count},
            {"candidate_count", input.size()},
            {"candidate_order", "chunk_id_asc_pre_selection"},
            {"top_k", definition.limit},
            {"terms", definition.terms},
            {"metadata_filters", Json::array({Json{{"key", "scope"}, {"value", "public"}}})},
            {"oracle", "score_desc_id_asc"},
            {"io_scope", "in_memory ExactLexicalIndex; I/O bytes are zero"},
            {"matched_end_to_end", measure_end_to_end(indexes, production_query, expected)},
            {"kernel_only", measure_kernel(input, expected, definition.limit)}
        };
    }

    [[nodiscard]] std::string populate_profile(agent_memory::ILexicalIndex& index, const Json& definition) {
        const auto count = definition.at("document_count").get<std::size_t>();
        const auto token_count = definition.at("tokens_per_document").get<std::size_t>();
        const auto width = definition.at("metadata_width").get<std::size_t>();
        const auto filter_modulus = definition.at("filter_modulus").get<std::size_t>();
        const auto common_every = definition.at("common_every").get<std::size_t>();
        const auto anchor_every = definition.at("anchor_every").get<std::size_t>();
        const auto ties = definition.at("ties").get<bool>();
        agent_memory::Sha256Accumulator corpus_hash;
        for(std::size_t i = 0; i < count; ++i) {
            agent_memory::Metadata metadata;
            metadata.set("scope", i % filter_modulus == 0U ? "public" : "private");
            metadata.set("tenant", i % 2U == 0U ? "alpha" : "beta");
            for(std::size_t field = 2; field < width; ++field) {
                metadata.set("extra:" + std::to_string(field), "value:" + std::string(48, 'm'));
            }
            std::vector<agent_memory::Token> tokens;
            tokens.reserve(token_count);
            Json term_texts = Json::array();
            for(std::size_t position = 0; position < token_count; ++position) {
                std::string term;
                if(ties) term = "common";
                else if(position == 0U) term = i % common_every == 0U ? "common" : "filler";
                else if(position == 1U) term = i % anchor_every == 0U ? "anchor" : "filler";
                else term = "word:" + std::to_string((i + position) % 32U);
                tokens.push_back(token(term, position));
                term_texts.push_back(term);
            }
            const Json row{
                {"chunk_id", chunk_name(i)}, {"resource_id", "resource:synthetic-profile"},
                {"revision", {1, 11, 17}}, {"tokens", term_texts}, {"metadata", metadata.values()}
            };
            corpus_hash.update(row.dump());
            corpus_hash.update("\n");
            index.upsert(agent_memory::LexicalDocumentRecord{
                agent_memory::ChunkId{chunk_name(i)},
                agent_memory::ResourceRevision{agent_memory::ResourceId{"resource:synthetic-profile"}, 1, 11, 17},
                std::move(tokens), std::move(metadata)
            });
        }
        constexpr char hex[] = "0123456789abcdef";
        const auto bytes = corpus_hash.finish();
        std::string digest(64U, '0');
        for(std::size_t i = 0; i < bytes.size(); ++i) {
            digest[i * 2U] = hex[bytes[i] >> 4U];
            digest[i * 2U + 1U] = hex[bytes[i] & 15U];
        }
        return digest;
    }

    [[nodiscard]] Json measure_stages(
        const IndexSet& indexes,
        const agent_memory::LexicalSearchQuery& query,
        const std::vector<Candidate>& expected
    ) {
        Json variants = Json::object();
        for(const auto name : kVariants) {
            Json times = Json::object();
            Json calls = Json::object();
            for(const auto stage : kStages) {
                times[std::string(stage)] = Json::array();
                calls[std::string(stage)] = Json::array();
            }
            variants[std::string(name)] = Json{
                {"elapsed_ns", times}, {"stage_calls", calls}, {"sample_count", kRepeats},
                {"exact_parity", true}, {"result_count", expected.size()}
            };
        }
        for(const auto& index : indexes) {
            for(std::size_t repeat = 0; repeat < kWarmups; ++repeat) (void)index->search(query);
        }
        Json orders = Json::array();
        for(std::size_t repeat = 0; repeat < kRepeats; ++repeat) {
            Json order = Json::array();
            for(std::size_t offset = 0; offset < kVariants.size(); ++offset) {
                const auto variant = (repeat + offset) % kVariants.size();
                const auto name = std::string(kVariants[variant]);
                order.push_back(name);
                agent_memory::lexical_topk_research::StageTimings sample;
                std::vector<Hit> result;
                {
                    agent_memory::lexical_topk_research::ProfileBinding binding(sample);
                    result = indexes[variant]->search(query);
                }
                auto& output = variants[name];
                output["exact_parity"] = output["exact_parity"].get<bool>() && same_results(candidates_from(result), expected);
                for(std::size_t stage = 0; stage < kStages.size(); ++stage) {
                    output["elapsed_ns"][std::string(kStages[stage])].push_back(sample.elapsed_ns[stage]);
                    output["stage_calls"][std::string(kStages[stage])].push_back(sample.calls[stage]);
                }
            }
            orders.push_back(std::move(order));
        }
        return Json{
            {"timing_scope", "DIRECT_INSTRUMENTED_SEARCH_STAGES"},
            {"instrumentation", "per-record steady_clock calls; explicit timer overhead; not comparable to uninstrumented latency"},
            {"candidate_generation_scope", "map iterator dereference/increment; begin and loop-condition overhead unattributed"},
            {"result_materialization_scope", "final vector truncation/destruction only; metadata copies are candidate_construction"},
            {"warmups", kWarmups}, {"repeats", kRepeats},
            {"execution_orders", orders}, {"variants", variants}
        };
    }

    [[nodiscard]] Json run_stage_profiles() {
        const auto recipe = Json::parse(kLexicalProfileFixture);
        Json profiles = Json::array();
        for(const auto& definition : recipe.at("profiles")) {
            const auto count = definition.at("document_count").get<std::size_t>();
            IndexSet indexes;
            IndexSet profiled;
            std::string corpus_sha256;
            for(std::size_t variant = 0; variant < kVariants.size(); ++variant) {
                indexes[variant] = make_index(variant);
                profiled[variant] = make_profiled_index(variant);
                const auto original_hash = populate_profile(*indexes[variant], definition);
                const auto profile_hash = populate_profile(*profiled[variant], definition);
                if(variant == 0U) corpus_sha256 = original_hash;
                if(original_hash != corpus_sha256 || profile_hash != corpus_sha256) {
                    throw std::runtime_error("profile arms received different corpus bytes");
                }
            }
            auto query = query_for({"profile", definition.at("terms").get<std::vector<std::string>>(), count}, count);
            const auto candidate_count = indexes[0]->search(query).size();
            Json cases = Json::array();
            for(const auto kind : {"small_k", "near_n", "empty"}) {
                const std::string case_name(kind);
                query.terms = case_name == "empty" ? std::vector<std::string>{"absent:profile"}
                    : definition.at("terms").get<std::vector<std::string>>();
                query.limit = case_name == "near_n" ? (candidate_count > 1U ? candidate_count - 1U : 1U) : 16U;
                const auto expected = candidates_from(indexes[0]->search(query));
                cases.push_back(Json{
                    {"name", case_name}, {"top_k", query.limit},
                    {"candidate_count", case_name == "empty" ? 0U : candidate_count},
                    {"result_count", expected.size()},
                    {"matched_uninstrumented", measure_end_to_end(indexes, query, expected)},
                    {"direct_instrumented", measure_stages(profiled, query, expected)}
                });
            }
            profiles.push_back(Json{{"fixture", definition}, {"materialized_corpus_sha256", corpus_sha256}, {"cases", cases}});
        }
        return Json{
            {"schema_version", 1}, {"kind", "SYNTHETIC"},
            {"fixture_recipe_sha256", kLexicalProfileFixtureSha256},
            {"fixture_hash_scope", "exact committed recipe bytes; corpus expansion source is independently source-bound"},
            {"workload_distribution", "NOT_AVAILABLE"}, {"profiles", profiles}
        };
    }

    [[nodiscard]] bool hybrid_filter_preservation_test() {
        IndexSet indexes;
        std::vector<Candidate> reference;
        for(std::size_t variant = 0; variant < indexes.size(); ++variant) {
            indexes[variant] = make_index(variant);
            populate_index(*indexes[variant], 128U);
            agent_memory::HybridRetrievalEngine engine(*indexes[variant]);
            agent_memory::RetrievalRequest request;
            request.query = "common anchor";
            request.limit = 20U;
            request.metadata_filters = {{"scope", "public"}, {"tenant", "alpha"}};
            const auto response = engine.retrieve(request);
            std::vector<Hit> hits;
            for(const auto& item : response.items) {
                if(!agent_memory::matches_metadata_filters(item.lexical.metadata, request.metadata_filters)
                   || item.object.metadata.values() != item.lexical.metadata.values()
                   || item.lexical.resource_id != agent_memory::ResourceId{"resource:synthetic"}) return false;
                hits.push_back(item.lexical);
            }
            if(hits.empty()) return false;
            const auto candidates = candidates_from(hits);
            if(variant == 0U) reference = candidates;
            else if(!same_results(candidates, reference)) return false;
            request.metadata_filters.push_back({"tenant", "beta"});
            if(!engine.retrieve(request).empty()) return false;
        }
        return true;
    }

    [[nodiscard]] bool self_test() {
        if(!hybrid_filter_preservation_test()) return false;
        std::vector<Candidate> input{
            Candidate{Hit{agent_memory::ChunkId{"chunk:b"}, 1.0F}},
            Candidate{Hit{agent_memory::ChunkId{"chunk:a"}, 1.0F}},
            Candidate{Hit{agent_memory::ChunkId{"chunk:c"}, 0.5F}},
            Candidate{Hit{agent_memory::ChunkId{"chunk:d"}, 2.0F}},
            Candidate{Hit{agent_memory::ChunkId{"chunk:e"}, 1.5F}}
        };
        input[0].hit.metadata.set("scope", "public");
        for(const auto name : kVariants) {
            for(const auto limit : {0U, 1U, 2U, 3U, 5U, 10U}) {
                if(!same_results(selector_for(name)(input, limit), full_sort(input, limit))
                   || !selector_for(name)({}, limit).empty()) {
                    return false;
                }
            }
        }
        agent_memory::ExactLexicalIndex index;
        agent_memory::Metadata metadata;
        metadata.set("scope", "public");
        index.upsert(agent_memory::LexicalDocumentRecord{
            agent_memory::ChunkId{"chunk:self"},
            agent_memory::ResourceRevision{agent_memory::ResourceId{"resource:self"}, 1, 1, 1},
            {token("common", 0), token("common", 1), token("filler", 2)}, metadata
        });
        const auto empty = index.search(agent_memory::LexicalSearchQuery{
            {"missing"}, 10, {{"scope", "public"}}, {}
        });
        const auto zero = index.search(agent_memory::LexicalSearchQuery{
            {"common"}, 0, {{"scope", "public"}}, {}
        });
        if(!empty.empty() || !zero.empty()) return false;
        IndexSet indexes;
        for(std::size_t i = 0; i < indexes.size(); ++i) {
            indexes[i] = make_index(i);
            if(!indexes[i]->search(query_for({"empty-index", {"common"}, 16}, 16)).empty()) return false;
            populate_index(*indexes[i], 128);
            auto missing_metadata = query_for({"missing-metadata", {"common"}, 16}, 16);
            missing_metadata.metadata_filters = {{"absent_key", "public"}};
            if(!indexes[i]->search(missing_metadata).empty()) return false;
            auto unfiltered = query_for({"no-filter", {"common"}, 129}, 129);
            unfiltered.metadata_filters.clear();
            if(indexes[i]->search(unfiltered).size() != 128U) return false;
            // Invalid import must fail before changing the valid index.
            const auto before = candidates_from(indexes[i]->search(unfiltered));
            auto invalid = agent_memory::LexicalDocumentRecord{};
            bool rejected = false;
            try { indexes[i]->upsert(std::move(invalid)); }
            catch(const std::invalid_argument&) { rejected = true; }
            if(!rejected || indexes[i]->size() != 128U
               || !same_results(candidates_from(indexes[i]->search(unfiltered)), before)) return false;
        }
        for(const auto& terms : {std::vector<std::string>{"common", "anchor"}, std::vector<std::string>{"common"}, std::vector<std::string>{"missing"}}) {
            for(const auto limit : {0U, 1U, 16U, 101U, 102U, 128U, 129U}) {
                const auto query = query_for({"self-test", terms, limit}, limit);
                const auto reference = candidates_from(indexes[0]->search(query));
                for(const auto& candidate_index : indexes) {
                    if(!same_results(candidates_from(candidate_index->search(query)), reference)) return false;
                }
            }
        }
        for(std::size_t variant = 0; variant < kVariants.size(); ++variant) {
            auto profiled = make_profiled_index(variant);
            populate_index(*profiled, 128U);
            for(const auto limit : {0U, 1U, 16U, 127U, 129U}) {
                const auto query = query_for({"profile-control", {"common", "anchor"}, limit}, limit);
                agent_memory::lexical_topk_research::StageTimings sample;
                std::vector<Hit> result;
                {
                    agent_memory::lexical_topk_research::ProfileBinding binding(sample);
                    result = profiled->search(query);
                }
                if(agent_memory::lexical_topk_research::g_timings != nullptr
                   || !same_results(candidates_from(result), candidates_from(indexes[0]->search(query)))
                   || sample.calls[static_cast<std::size_t>(agent_memory::lexical_topk_research::Stage::FullSearch)] != 1U
                   || sample.calls[static_cast<std::size_t>(agent_memory::lexical_topk_research::Stage::CandidateGeneration)] != (limit == 0U ? 0U : 128U)) return false;
            }
        }
        return true;
    }

    [[nodiscard]] std::vector<CaseDefinition> cases_for(const std::size_t count) {
        const auto filtered_count = count - (count + 4U) / 5U;
        return {
            {"mixed_top_k", {"common", "anchor"}, std::min<std::size_t>(16U, count)},
            {"ties_k_near_n", {"common"}, filtered_count - 1U},
            {"empty_result", {"term_that_is_absent"}, std::min<std::size_t>(16U, count)},
            {"top_k_zero", {"common"}, 0U}
        };
    }

    [[nodiscard]] Json run_benchmark() {
        Json builds = Json::array();
        Json cases = Json::array();
        const std::vector<std::string> variant_names{
            "full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"
        };
        for(const auto count : kDocumentCounts) {
            IndexSet indexes;
            for(std::size_t variant = 0; variant < indexes.size(); ++variant) {
                begin_allocation_measurement();
                const auto started = Clock::now();
                indexes[variant] = make_index(variant);
                populate_index(*indexes[variant], count);
                const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
                end_allocation_measurement();
                const auto allocation = allocation_sample();
                builds.push_back(Json{
                    {"document_count", count}, {"variant", std::string(kVariants[variant])},
                    {"build_ms", elapsed}, {"allocation_count", allocation.count},
                    {"allocated_bytes", allocation.bytes}
                });
            }
            for(const auto& definition : cases_for(count)) {
                cases.push_back(run_case(indexes, count, definition));
            }
        }
        return Json{
            {"schema_version", 2},
            {"benchmark_name", "production_shaped_exact_lexical_topk_decision"},
            {"status", "EXECUTED"},
            {"source_commit", AGENT_MEMORY_LEXICAL_TOPK_SOURCE_COMMIT},
            {"actual_checkout_sha", AGENT_MEMORY_LEXICAL_TOPK_SOURCE_COMMIT},
            {"expected_pr_head_sha", AGENT_MEMORY_LEXICAL_TOPK_EXPECTED_PR_HEAD_SHA},
            {"source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"generated_source_manifest_sha256", AGENT_MEMORY_LEXICAL_TOPK_GENERATED_SOURCE_SHA256},
            {"build_identity", Json{
                {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
                {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
                {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
                {"configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
                {"base_cxx_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
                {"active_configuration_flags_sha256", AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
                {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256}
            }},
            {"fixture", Json{
                {"seed", "fixed-synthetic-lexical-v1"},
                {"document_counts", kDocumentCounts},
                {"metadata_filter", "scope=public"},
                {"metadata_and_filter_included", true},
                {"authorization", "NOT_EXERCISED"},
                {"tenant_isolation", "NOT_EXERCISED"},
                {"resource_revision_freshness", "NOT_EXERCISED"},
                {"io_scope", "in_memory_no_io"}
            }},
            {"selection_variants", variant_names},
            {"oracle", "score_desc_id_asc"},
            {"timing_semantics", "warmups excluded; raw samples are complete operations; kernel and end-to-end scopes are separate"},
            {"end_to_end_stage_breakdown", "DIRECT_INSTRUMENTED_SEPARATE_SCOPE"},
            {"stage_profiles", run_stage_profiles()},
            {"retrieval_boundary", Json{
                {"hybrid_filter_source_preservation", hybrid_filter_preservation_test() ? "PASS" : "FAIL"},
                {"authorization", "NOT_EXECUTED"}, {"tenant_authority", "NOT_EXECUTED"},
                {"revision_freshness", "NOT_EXECUTED"},
                {"reason", "HybridRetrievalEngine request has no authorization/pinned-revision context; RevisionBoundRetrieval does not consume ILexicalIndex"}
            }},
            {"production_api_changed", false},
            {"production_defaults_changed", false},
            {"cases", cases},
            {"builds", builds}
        };
    }

} // namespace

int main(int argc, char** argv) {
    bool self_test_requested = false;
    std::string output_path = "exact-lexical-topk-decision.json";
    for(int i = 1; i < argc; ++i) {
        const std::string_view argument(argv[i]);
        if(argument == "--self-test") {
            self_test_requested = true;
        } else if(argument == "--output" && i + 1 < argc) {
            output_path = argv[++i];
        } else {
            std::cerr << "unknown or incomplete argument: " << argument << '\n';
            return 2;
        }
    }
    if(self_test_requested) {
        if(!self_test()) {
            std::cerr << "exact lexical top-k decision self-test: FAIL\n";
            return 1;
        }
        std::cout << "exact lexical top-k decision self-test: PASS\n";
        return 0;
    }
    try {
        auto receipt = run_benchmark();
        std::ofstream output(output_path, std::ios::binary | std::ios::trunc);
        if(!output) {
            std::cerr << "cannot open receipt output\n";
            return 3;
        }
        output << receipt.dump(2) << '\n';
        std::cout << "wrote " << output_path << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "exact lexical top-k decision: " << error.what() << '\n';
        return 4;
    }
}
