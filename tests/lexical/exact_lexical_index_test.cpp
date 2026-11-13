#include <agent_memory.hpp>

#include <atomic>
#include <cstdlib>
#include <initializer_list>
#include <iostream>
#include <cstddef>
#include <new>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {

    std::atomic<std::ptrdiff_t> g_allocation_budget{-1};

    void* allocate_for_test(const std::size_t size) {
        auto budget = g_allocation_budget.load(std::memory_order_relaxed);
        if(budget >= 0) {
            if(budget == 0) {
                throw std::bad_alloc{};
            }
            g_allocation_budget.store(budget - 1, std::memory_order_relaxed);
        }
        if(auto* result = std::malloc(size == 0 ? 1 : size)) {
            return result;
        }
        throw std::bad_alloc{};
    }

    void* allocate_aligned_for_test(
        const std::size_t size,
        const std::size_t alignment
    ) {
        auto budget = g_allocation_budget.load(std::memory_order_relaxed);
        if(budget >= 0) {
            if(budget == 0) {
                throw std::bad_alloc{};
            }
            g_allocation_budget.store(budget - 1, std::memory_order_relaxed);
        }

        // C++17 requires the aligned allocation size to be a multiple of the
        // alignment.  Use the platform allocator paired with the matching
        // aligned delete below; passing an aligned pointer to std::free is
        // undefined on Windows and caused the CI-only 0xc0000409 failure.
        const auto rounded_size = size == 0
            ? alignment
            : ((size + alignment - 1) / alignment) * alignment;
#if defined(_WIN32)
        if(auto* result = _aligned_malloc(rounded_size, alignment)) {
            return result;
        }
#else
        void* result = nullptr;
        if(posix_memalign(&result, alignment, rounded_size) == 0) {
            return result;
        }
#endif
        throw std::bad_alloc{};
    }

    void fail_allocations_after(const std::ptrdiff_t successful_allocations) {
        g_allocation_budget.store(
            successful_allocations,
            std::memory_order_relaxed
        );
    }

    void disable_allocation_failures() {
        g_allocation_budget.store(-1, std::memory_order_relaxed);
    }

} // namespace

void* operator new(const std::size_t size) {
    return allocate_for_test(size);
}

void* operator new[](const std::size_t size) {
    return allocate_for_test(size);
}

void* operator new(const std::size_t size, const std::align_val_t alignment) {
    return allocate_aligned_for_test(
        size,
        static_cast<std::size_t>(alignment)
    );
}

void* operator new[](
    const std::size_t size,
    const std::align_val_t alignment
) {
    return allocate_aligned_for_test(
        size,
        static_cast<std::size_t>(alignment)
    );
}

void operator delete(void* pointer) noexcept {
    std::free(pointer);
}

void operator delete[](void* pointer) noexcept {
    std::free(pointer);
}

void operator delete(void* pointer, const std::size_t) noexcept {
    std::free(pointer);
}

void operator delete[](void* pointer, const std::size_t) noexcept {
    std::free(pointer);
}

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

void operator delete(
    void* pointer,
    const std::size_t,
    const std::align_val_t
) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}

void operator delete[](
    void* pointer,
    const std::size_t,
    const std::align_val_t
) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}

namespace {

    int fail(const std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    [[nodiscard]] agent_memory::Token make_token(
        std::string text,
        const std::size_t position
    ) {
        return agent_memory::Token{
            std::move(text),
            agent_memory::TextRange{position, 1},
            position,
            agent_memory::TokenKind::Word
        };
    }

    [[nodiscard]] std::vector<agent_memory::Token> make_tokens(
        std::initializer_list<std::string_view> terms
    ) {
        std::vector<agent_memory::Token> tokens;
        std::size_t position = 0;
        for(const auto term : terms) {
            tokens.push_back(make_token(std::string{term}, position));
            ++position;
        }
        return tokens;
    }

    [[nodiscard]] agent_memory::LexicalDocumentRecord make_record(
        const std::string& chunk_id,
        const std::string& resource_id,
        std::vector<agent_memory::Token> tokens,
        const std::string& scope
    ) {
        agent_memory::Metadata metadata;
        metadata.set("scope", scope);

        return agent_memory::LexicalDocumentRecord{
            agent_memory::ChunkId{chunk_id},
            agent_memory::ResourceRevision{
                agent_memory::ResourceId{resource_id},
                1,
                11,
                17
            },
            std::move(tokens),
            metadata
        };
    }

    [[nodiscard]] agent_memory::LexicalDocumentRecord make_record_with_meta(
        const std::string& chunk_id,
        const std::string& resource_id,
        std::vector<agent_memory::Token> tokens,
        std::initializer_list<std::pair<std::string, std::string>> kv
    ) {
        agent_memory::Metadata metadata;
        for(const auto& item : kv) {
            metadata.set(item.first, item.second);
        }

        return agent_memory::LexicalDocumentRecord{
            agent_memory::ChunkId{chunk_id},
            agent_memory::ResourceRevision{
                agent_memory::ResourceId{resource_id},
                1,
                11,
                17
            },
            std::move(tokens),
            metadata
        };
    }

    [[nodiscard]] bool same_stats(
        const std::optional<agent_memory::LexicalDocumentStats>& left,
        const std::optional<agent_memory::LexicalDocumentStats>& right
    ) {
        if(left.has_value() != right.has_value()) {
            return false;
        }
        if(!left.has_value()) {
            return true;
        }
        return left->chunk_id == right->chunk_id &&
            left->revision.resource_id == right->revision.resource_id &&
            left->revision.generation == right->revision.generation &&
            left->revision.content_hash == right->revision.content_hash &&
            left->revision.pipeline_config_hash == right->revision.pipeline_config_hash &&
            left->token_count == right->token_count &&
            left->unique_token_count == right->unique_token_count;
    }

    [[nodiscard]] bool same_hits(
        const std::vector<agent_memory::LexicalSearchResult>& left,
        const std::vector<agent_memory::LexicalSearchResult>& right
    ) {
        if(left.size() != right.size()) {
            return false;
        }
        for(std::size_t i = 0; i < left.size(); ++i) {
            if(left[i].chunk_id != right[i].chunk_id ||
               left[i].score != right[i].score ||
               left[i].resource_id != right[i].resource_id ||
               left[i].section_id != right[i].section_id ||
               left[i].enrichment_level != right[i].enrichment_level) {
                return false;
            }
        }
        return true;
    }

} // namespace

int main() {
    agent_memory::ExactLexicalIndex index;

    index.upsert(make_record(
        "chunk:a",
        "resource:alpha",
        make_tokens({"agent", "memory", "agent", "system"}),
        "public"
    ));
    index.upsert(make_record(
        "chunk:b",
        "resource:alpha",
        make_tokens({"memory", "system"}),
        "private"
    ));
    index.upsert(make_record(
        "chunk:c",
        "resource:beta",
        make_tokens({"agent"}),
        "public"
    ));

    if(index.size() != 3) {
        return fail("exact lexical index must store upserted records");
    }

    const auto stats = index.find_stats(agent_memory::ChunkId{"chunk:a"});
    if(!stats || stats->token_count != 4 || stats->unique_token_count != 3) {
        return fail("exact lexical index must expose token stats");
    }

    const auto ranked = index.search(agent_memory::LexicalSearchQuery{
        {"agent", "memory"},
        10,
        {},
        {}
    });

    if(ranked.empty() || ranked[0].chunk_id != agent_memory::ChunkId{"chunk:a"}) {
        return fail("BM25 search must rank the best matching chunk first");
    }

    const auto filtered = index.search(agent_memory::LexicalSearchQuery{
        {"system"},
        10,
        {agent_memory::MetadataFilter{"scope", "public"}},
        {}
    });

    if(filtered.size() != 1 || filtered[0].chunk_id != agent_memory::ChunkId{"chunk:a"}) {
        return fail("exact lexical index must apply metadata filters");
    }

    const auto limited = index.search(agent_memory::LexicalSearchQuery{
        {"agent"},
        1,
        {},
        {}
    });

    if(limited.size() != 1) {
        return fail("exact lexical index must honor result limit");
    }

    const auto zero_limit = index.search(agent_memory::LexicalSearchQuery{
        {"agent"},
        0,
        {},
        {}
    });

    if(!zero_limit.empty()) {
        return fail("limit zero must return no results");
    }

    index.upsert(make_record(
        "chunk:a",
        "resource:alpha",
        make_tokens({"updated"}),
        "public"
    ));

    const auto replaced = index.search(agent_memory::LexicalSearchQuery{
        {"agent", "memory"},
        10,
        {},
        {}
    });

    if(replaced.empty() || replaced[0].chunk_id == agent_memory::ChunkId{"chunk:a"}) {
        return fail("upsert must replace old lexical terms");
    }

    agent_memory::ExactLexicalIndex tie_index;
    tie_index.upsert(make_record(
        "chunk:b",
        "resource:tie",
        make_tokens({"tie"}),
        "public"
    ));
    tie_index.upsert(make_record(
        "chunk:a",
        "resource:tie",
        make_tokens({"tie"}),
        "public"
    ));

    const auto tie_results = tie_index.search(agent_memory::LexicalSearchQuery{
        {"tie"},
        2,
        {},
        {}
    });

    if(
        tie_results.size() != 2 ||
        tie_results[0].chunk_id != agent_memory::ChunkId{"chunk:a"} ||
        tie_results[1].chunk_id != agent_memory::ChunkId{"chunk:b"}
    ) {
        return fail("exact lexical index must break score ties by chunk id");
    }

    if(index.erase_resource(agent_memory::ResourceId{"resource:alpha"}) != 2) {
        return fail("erase_resource must remove all chunks owned by the resource");
    }

    if(index.erase_resource(agent_memory::ResourceId{"resource:alpha"}) != 0) {
        return fail("erase_resource must be idempotent (return 0 on second call)");
    }

    if(index.find_stats(agent_memory::ChunkId{"chunk:b"})) {
        return fail("erase_resource must remove stats for owned chunks");
    }

    if(!index.erase(agent_memory::ChunkId{"chunk:c"}) || index.erase(agent_memory::ChunkId{"chunk:c"})) {
        return fail("erase must report removed and missing chunks");
    }

    index.upsert(make_record(
        "chunk:idem",
        "resource:idem",
        make_tokens({"alpha"}),
        "public"
    ));
    if(!index.erase(agent_memory::ChunkId{"chunk:idem"})) {
        return fail("first erase must return true");
    }
    if(index.erase(agent_memory::ChunkId{"chunk:idem"})) {
        return fail("second erase must return false (idempotent)");
    }
    if(index.erase_resource(agent_memory::ResourceId{"resource:idem"}) != 0) {
        return fail("erase_resource on idempotency-test chunk must return 0");
    }

    index.clear();
    if(index.size() != 0) {
        return fail("clear must remove all records");
    }

    // Fix 3: regression test — explicit query BM25 numerically equal to
    // the library default (k1=1.5, b=0.75) must DIFFER from "fall back to
    // index options" when the index has a non-default BM25 configuration.
    // Use a longer document so the TF-saturation factor (k1 * (1 - b + b *
    // length_ratio)) actually depends on k1 and b (with a single-token
    // document whose length equals avgdl, the factor is 1.0 regardless).
    {
        agent_memory::ExactLexicalIndex bm25_override_index{
            agent_memory::ExactLexicalIndexOptions{
                agent_memory::Bm25Options{2.0F, 0.3F}
            }
        };
        // 5 tokens; avgdl across the index is 5.0, so |D|/avgdl = 1.0
        // and (1 - b + b * 1.0) = 1.0, making k1 still factor in. We
        // need |D| != avgdl to exercise the b parameter; we also add
        // a longer chunk to make avgdl diverge from this chunk's length.
        bm25_override_index.upsert(make_record(
            "chunk:long",
            "resource:alpha",
            make_tokens({"filler", "filler", "filler", "filler", "filler",
                         "filler", "filler", "filler", "filler", "filler"}),
            "public"
        ));
        bm25_override_index.upsert(make_record(
            "chunk:a",
            "resource:beta",
            make_tokens({"foo", "bar", "baz"}),
            "public"
        ));

        agent_memory::LexicalSearchQuery fallback_query;
        fallback_query.terms = {"foo"};
        fallback_query.limit = 10;
        fallback_query.bm25 = std::nullopt;
        const auto fallback_scores = bm25_override_index.search(fallback_query);

        agent_memory::LexicalSearchQuery explicit_query;
        explicit_query.terms = {"foo"};
        explicit_query.limit = 10;
        explicit_query.bm25 = agent_memory::Bm25Options{1.5F, 0.75F};
        const auto explicit_scores = bm25_override_index.search(explicit_query);

        if(fallback_scores.empty() || explicit_scores.empty()) {
            return fail("Fix3: both searches should return hits");
        }
        if(fallback_scores.front().score == explicit_scores.front().score) {
            return fail("Fix3: explicit query BM25 default must differ from index BM25 fallback");
        }
    }

    // Fix 7: metadata filters must use AND semantics — all filters
    // must match a chunk for it to be returned.
    {
        agent_memory::ExactLexicalIndex and_index;
        and_index.upsert(make_record_with_meta(
            "chunk:a",
            "resource:alpha",
            make_tokens({"foo"}),
            {{"scope", "public"}, {"lang", "en"}}
        ));
        and_index.upsert(make_record_with_meta(
            "chunk:b",
            "resource:alpha",
            make_tokens({"foo"}),
            {{"scope", "private"}, {"lang", "en"}}
        ));

        agent_memory::LexicalSearchQuery q;
        q.terms = {"foo"};
        q.limit = 10;
        q.metadata_filters = {
            agent_memory::MetadataFilter{"scope", "public"},
            agent_memory::MetadataFilter{"lang", "en"}
        };
        const auto and_results = and_index.search(q);
        if(and_results.size() != 1) {
            return fail("Fix7: AND filters must return chunks matching all filters");
        }
        if(and_results[0].chunk_id != agent_memory::ChunkId{"chunk:a"}) {
            return fail("Fix7: must match chunk:a (scope=public+lang=en), not chunk:b");
        }
    }

    // Fix 8: upsert can re-parent a chunk to a different resource_id;
    // the old resource must drop the chunk id, the new resource must
    // own it, and erase_resource on the old resource must return 0.
    {
        agent_memory::ExactLexicalIndex reparent_index;
        reparent_index.upsert(make_record(
            "chunk:a",
            "resource:alpha",
            make_tokens({"foo"}),
            "public"
        ));
        reparent_index.upsert(make_record(
            "chunk:a",
            "resource:beta",
            make_tokens({"foo"}),
            "public"
        ));

        if(reparent_index.erase_resource(agent_memory::ResourceId{"resource:alpha"}) != 0) {
            return fail("Fix8: alpha must be empty after re-parent");
        }
        if(reparent_index.size() != 1) {
            return fail("Fix8: total size must be 1 after re-parent (beta owns chunk:a)");
        }
        if(reparent_index.erase_resource(agent_memory::ResourceId{"resource:beta"}) != 1) {
            return fail("Fix8: beta must own 1 chunk");
        }
        if(reparent_index.size() != 0) {
            return fail("Fix8: total size must be 0 after erasing beta");
        }
    }

    // Fix 9a: df counters and stale dictionary entries must remain
    // consistent across mixed upsert/erase sequences.
    {
        agent_memory::ExactLexicalIndex df_index;
        df_index.upsert(make_record(
            "chunk:a",
            "resource:alpha",
            make_tokens({"foo", "bar"}),
            "public"
        ));
        df_index.upsert(make_record(
            "chunk:b",
            "resource:beta",
            make_tokens({"foo"}),
            "public"
        ));
        if(!df_index.erase(agent_memory::ChunkId{"chunk:a"})) {
            return fail("Fix9a: erase of chunk:a must return true");
        }
        if(df_index.size() != 1) {
            return fail("Fix9a: size after erase must be 1");
        }
        // At this point df(foo)=1, df(bar) reached 0 and its dictionary
        // entry must have been erased (stale-entry cleanup). Search for
        // 'bar' must still return 0 hits — we cannot inspect
        // m_dictionary_by_text directly, but a search for 'bar' yields
        // no result and the index remains functional.
        agent_memory::LexicalSearchQuery bar_q;
        bar_q.terms = {"bar"};
        bar_q.limit = 10;
        if(!df_index.search(bar_q).empty()) {
            return fail("Fix9a: 'bar' must produce no hits after its only chunk is erased");
        }
        // And 'foo' still has 1 hit (chunk:b).
        agent_memory::LexicalSearchQuery foo_q;
        foo_q.terms = {"foo"};
        foo_q.limit = 10;
        const auto foo_results = df_index.search(foo_q);
        if(foo_results.size() != 1 || foo_results[0].chunk_id != agent_memory::ChunkId{"chunk:b"}) {
            return fail("Fix9a: 'foo' must still match chunk:b after erasing chunk:a");
        }
    }

    // Fix 9b: search on an empty corpus must return no results and
    // must not throw.
    {
        agent_memory::ExactLexicalIndex empty_index;
        agent_memory::LexicalSearchQuery q;
        q.terms = {"foo"};
        q.limit = 10;
        const auto empty_results = empty_index.search(q);
        if(!empty_results.empty()) {
            return fail("Fix9b: empty corpus must return no results");
        }
    }

    // v6++: precise rollback contract for upsert(). The previous
    // implementation admitted best-effort rollback with possible
    // double-decrement of df and stale m_total_token_count on
    // std::bad_alloc. The new contract guarantees that any throwing
    // commit leaves the index in EXACTLY its pre-upsert state.
    //
    // We cannot inject std::bad_alloc without modifying the
    // implementation, so we verify the contract through two
    // observable scenarios:
    //   (a) is_valid() failure (empty tokens) must throw BEFORE any
    //       mutation; observable state is byte-for-byte identical
    //       before and after.
    //   (b) A successful replace exercises the same code path that a
    //       failed replace's rollback restore would use. If the
    //       replace succeeds, then the rollback restore path (which
    //       re-inserts the old snapshot into m_records, re-increments
    //       df for old terms, re-adds resource membership, and
    //       restores m_total_token_count) is sound by the same
    //       construction.
    {
        agent_memory::ExactLexicalIndex rollback_index;

        rollback_index.upsert(make_record(
            "chunk:alpha",
            "resource:shared",
            make_tokens({"foo", "bar", "baz"}),
            "public"
        ));
        rollback_index.upsert(make_record(
            "chunk:beta",
            "resource:shared",
            make_tokens({"bar", "baz", "qux"}),
            "public"
        ));

        const auto size_before = rollback_index.size();

        agent_memory::LexicalSearchQuery foo_q;
        foo_q.terms = {"foo"};
        foo_q.limit = 10;
        if(rollback_index.search(foo_q).size() != 1) {
            return fail("v6++: 'foo' must match chunk:alpha pre-upsert");
        }

        // (a) is_valid() failure -- throw BEFORE any mutation.
        bool threw = false;
        try {
            // Empty tokens -> is_valid(record) is false.
            rollback_index.upsert(make_record(
                "chunk:bad",
                "resource:other",
                {},
                "public"
            ));
        } catch(const std::invalid_argument&) {
            threw = true;
        }
        if(!threw) {
            return fail("v6++: invalid upsert must throw invalid_argument");
        }
        if(rollback_index.size() != size_before) {
            return fail("v6++: invalid upsert must not change size");
        }
        if(rollback_index.search(foo_q).size() != 1) {
            return fail("v6++: invalid upsert must not change df");
        }
        if(rollback_index.erase_resource(agent_memory::ResourceId{"resource:other"}) != 0) {
            return fail("v6++: invalid upsert must not create resource entry");
        }

        // (b) Successful replace -- exercises the same code path that
        // a failed replace's rollback restore would use. chunk:alpha
        // was [foo,bar,baz] under resource:shared; replace with
        // [token5] under resource:other.
        rollback_index.upsert(make_record(
            "chunk:alpha",
            "resource:other",
            make_tokens({"token5"}),
            "public"
        ));

        if(rollback_index.size() != size_before) {
            return fail("v6++: replace must not change size");
        }
        if(rollback_index.search(foo_q).size() != 0) {
            return fail("v6++: 'foo' must disappear after chunk:alpha replaced");
        }

        agent_memory::LexicalSearchQuery token5_q;
        token5_q.terms = {"token5"};
        token5_q.limit = 10;
        if(rollback_index.search(token5_q).size() != 1) {
            return fail("v6++: 'token5' must match new chunk:alpha");
        }

        agent_memory::LexicalSearchQuery qux_q;
        qux_q.terms = {"qux"};
        qux_q.limit = 10;
        if(rollback_index.search(qux_q).size() != 1) {
            return fail("v6++: 'qux' must still match chunk:beta");
        }

        // Resource membership: resource:shared no longer owns chunk:alpha;
        // resource:other now owns chunk:alpha.
        if(rollback_index.erase_resource(agent_memory::ResourceId{"resource:shared"}) != 1) {
            return fail("v6++: shared must own only chunk:beta after replace");
        }
        if(rollback_index.erase_resource(agent_memory::ResourceId{"resource:other"}) != 1) {
            return fail("v6++: other must own chunk:alpha after replace");
        }
        if(rollback_index.size() != 0) {
            return fail("v6++: total size must be 0 after fully cleaning up");
        }
    }

    // A1: deterministic allocation failures cover both the new-record and
    // replacement paths. The operation is allowed to throw, but every
    // observable part of the old index must remain unchanged.
    {
        const auto run_fault_matrix = [](const bool replacement) {
            bool observed_failure = false;
            bool observed_success = false;

            for(std::ptrdiff_t budget = 0; budget < 512; ++budget) {
                disable_allocation_failures();
                agent_memory::ExactLexicalIndex index;
                index.upsert(make_record(
                    "chunk:base",
                    "resource:base",
                    make_tokens({"base", "shared", "shared"}),
                    "public"
                ));
                index.upsert(make_record(
                    "chunk:other",
                    "resource:base",
                    make_tokens({"shared", "other"}),
                    "public"
                ));

                const auto base_stats = index.find_stats(
                    agent_memory::ChunkId{"chunk:base"}
                );
                const auto other_stats = index.find_stats(
                    agent_memory::ChunkId{"chunk:other"}
                );
                const auto base_hits = index.search(
                    agent_memory::LexicalSearchQuery{{"base"}, 10, {}, {}}
                );
                const auto shared_hits = index.search(
                    agent_memory::LexicalSearchQuery{{"shared"}, 10, {}, {}}
                );
                const auto base_resource_count = [&index] {
                    auto copy = index;
                    return copy.erase_resource(
                        agent_memory::ResourceId{"resource:base"}
                    );
                }();
                const auto replacement_resource_count = [&index] {
                    auto copy = index;
                    return copy.erase_resource(
                        agent_memory::ResourceId{"resource:replacement"}
                    );
                }();

                auto candidate = make_record(
                    replacement ? "chunk:base" : "chunk:new",
                    "resource:replacement",
                    make_tokens({"replacement", "shared", "replacement"}),
                    "public"
                );

                fail_allocations_after(budget);
                bool threw = false;
                try {
                    index.upsert(std::move(candidate));
                } catch(const std::bad_alloc&) {
                    threw = true;
                }
                disable_allocation_failures();

                if(!threw) {
                    observed_success = true;
                    break;
                }

                observed_failure = true;
                if(index.size() != 2 ||
                   !same_stats(
                       index.find_stats(agent_memory::ChunkId{"chunk:base"}),
                       base_stats
                   ) ||
                   !same_stats(
                       index.find_stats(agent_memory::ChunkId{"chunk:other"}),
                       other_stats
                   ) ||
                   !same_hits(
                       index.search(agent_memory::LexicalSearchQuery{
                           {"base"}, 10, {}, {}
                       }),
                       base_hits
                   ) ||
                   !same_hits(
                       index.search(agent_memory::LexicalSearchQuery{
                           {"shared"}, 10, {}, {}
                       }),
                       shared_hits
                   )) {
                    return false;
                }

                auto base_copy = index;
                auto replacement_copy = index;
                if(base_copy.erase_resource(
                       agent_memory::ResourceId{"resource:base"}
                   ) != base_resource_count ||
                   replacement_copy.erase_resource(
                       agent_memory::ResourceId{"resource:replacement"}
                   ) != replacement_resource_count) {
                    return false;
                }
                if(!replacement && index.find_stats(
                       agent_memory::ChunkId{"chunk:new"}
                   )) {
                    return false;
                }
            }

            return observed_failure && observed_success;
        };

        if(!run_fault_matrix(false)) {
            return fail("A1: insertion allocation failures changed index state");
        }
        if(!run_fault_matrix(true)) {
            return fail("A1: replacement allocation failures changed index state");
        }
    }

    try {
        agent_memory::ExactLexicalIndex invalid_index{
            agent_memory::ExactLexicalIndexOptions{
                agent_memory::Bm25Options{0.0F, 0.75F}
            }
        };
        (void)invalid_index;
        return fail("exact lexical index must reject invalid BM25 options");
    } catch(const std::invalid_argument&) {
    }

    try {
        index.upsert(agent_memory::LexicalDocumentRecord{});
        return fail("exact lexical index must reject invalid records");
    } catch(const std::invalid_argument&) {
    }

    try {
        (void)index.search(agent_memory::LexicalSearchQuery{});
        return fail("exact lexical index must reject invalid non-zero-limit queries");
    } catch(const std::invalid_argument&) {
    }

    return 0;
}
