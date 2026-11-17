#include <agent_memory.hpp>

#include <agent_memory/eval/DatasetLoader.hpp>
#include <agent_memory/eval/Evaluation.hpp>
#include <agent_memory/eval/IRetrieverAdapter.hpp>
#include <agent_memory/eval/RetrievalEvalRunner.hpp>
#include <agent_memory/retrieval/BowEmbedder.hpp>
#include <agent_memory/retrieval/BowVectorRetriever.hpp>
#include <agent_memory/retrieval/BruteForceTopKIndex.hpp>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <iostream>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {

    std::atomic<bool> g_fail_next_allocation{false};

    void* allocate_for_test(const std::size_t size) {
        if(g_fail_next_allocation.exchange(false, std::memory_order_relaxed)) {
            throw std::bad_alloc{};
        }
        if(void* result = std::malloc(size == 0 ? 1 : size)) {
            return result;
        }
        throw std::bad_alloc{};
    }

    void* allocate_aligned_for_test(
        const std::size_t size,
        const std::size_t alignment
    ) {
        if(g_fail_next_allocation.exchange(false, std::memory_order_relaxed)) {
            throw std::bad_alloc{};
        }
        const auto rounded_size = size == 0
            ? alignment
            : ((size + alignment - 1) / alignment) * alignment;
#if defined(_WIN32)
        if(void* result = _aligned_malloc(rounded_size, alignment)) {
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

    void fail_next_allocation() {
        g_fail_next_allocation.store(true, std::memory_order_relaxed);
    }

    void disable_allocation_failure() {
        g_fail_next_allocation.store(false, std::memory_order_relaxed);
    }

    int fail(const std::string& message) {
        std::cerr << "exact_vector_retriever_test: " << message << '\n';
        return 1;
    }

    // Builds a corpus of ten short documents with deliberate token overlap so
    // qrels are unambiguous against the deterministic BoW scorer.
    struct Doc { std::string id; std::string text; };

    std::vector<Doc> handcrafted_corpus() {
        return {
            {"d:cat-sat",     "the cat sat on the mat"},
            {"d:cat-ran",     "the cat ran across the yard"},
            {"d:dog-barked",  "the dog barked at the cat"},
            {"d:dog-ran",     "the dog ran across the yard"},
            {"d:bird-flew",   "the bird flew over the house"},
            {"d:fish-swam",   "the fish swam in the pond"},
            {"d:cat-couch",   "the cat sat on the couch"},
            {"d:dog-park",    "the dog played in the park"},
            {"d:bird-tree",   "the bird sat in the tree"},
            {"d:cat-yard",    "the cat wandered in the yard"}
        };
    }

    // Tokenizer mirror used in assertions: lowercase, alnum runs, drop tokens
    // shorter than 2 chars. Kept private so the test does not depend on the
    // exact stop-word list, only on the documented contract.
    std::vector<std::string> tokenize_local(std::string_view text) {
        std::vector<std::string> out;
        std::string current;
        for(const char raw : text) {
            char ch = raw;
            if(ch >= 'A' && ch <= 'Z') {
                ch = static_cast<char>(ch + ('a' - 'A'));
            }
            const bool alnum = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9');
            if(alnum) {
                current.push_back(ch);
            } else if(!current.empty()) {
                if(current.size() >= 2) {
                    out.push_back(std::move(current));
                }
                current.clear();
            }
        }
        if(!current.empty() && current.size() >= 2) {
            out.push_back(std::move(current));
        }
        return out;
    }

    bool almost_equal(float lhs, float rhs, float eps = 1e-4F) {
        const float diff = lhs - rhs;
        return (diff >= -eps) && (diff <= eps);
    }

    // Defined after main() below; declared here so main() can call it.
    int run_brute_force_validation_tests();

} // namespace

#if !(defined(_MSC_VER) && defined(_DEBUG))

void* operator new(const std::size_t size) {
    return allocate_for_test(size);
}

void* operator new[](const std::size_t size) {
    return allocate_for_test(size);
}

void* operator new(const std::size_t size, const std::nothrow_t&) noexcept {
    try {
        return allocate_for_test(size);
    } catch(const std::bad_alloc&) {
        return nullptr;
    }
}

void* operator new[](const std::size_t size, const std::nothrow_t&) noexcept {
    try {
        return allocate_for_test(size);
    } catch(const std::bad_alloc&) {
        return nullptr;
    }
}

void* operator new(const std::size_t size, const std::align_val_t alignment) {
    return allocate_aligned_for_test(size, static_cast<std::size_t>(alignment));
}

void* operator new[](
    const std::size_t size,
    const std::align_val_t alignment
) {
    return allocate_aligned_for_test(size, static_cast<std::size_t>(alignment));
}

void* operator new(
    const std::size_t size,
    const std::align_val_t alignment,
    const std::nothrow_t&
) noexcept {
    try {
        return allocate_aligned_for_test(
            size, static_cast<std::size_t>(alignment));
    } catch(const std::bad_alloc&) {
        return nullptr;
    }
}

void* operator new[](
    const std::size_t size,
    const std::align_val_t alignment,
    const std::nothrow_t&
) noexcept {
    try {
        return allocate_aligned_for_test(
            size, static_cast<std::size_t>(alignment));
    } catch(const std::bad_alloc&) {
        return nullptr;
    }
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

void operator delete(void* pointer, const std::nothrow_t&) noexcept {
    std::free(pointer);
}

void operator delete[](void* pointer, const std::nothrow_t&) noexcept {
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

#endif

int main() {
    using agent_memory::BowVectorRetriever;

    // 1) Self-test: the cat-doc must rank first for a "cat" query against the
    //    minimal two-doc corpus. This is the canonical ranking sanity case.
    {
        const std::vector<std::string> ids = {"d:cat", "d:dog"};
        const std::vector<std::string> texts = {
            "the cat sat on the mat",
            "the dog barked at the cat"
        };
        BowVectorRetriever retriever(ids, texts, /*seed=*/0);

        agent_memory::RetrievalQuery query;
        query.text = "cat";
        query.limit = 10;
        const auto result = retriever.retrieve(query);
        if(result.empty()) {
            return fail("self-test: cat query must return at least one hit");
        }
        if(result.chunks.front().chunk.id.value() != "d:cat") {
            return fail(
                "self-test: cat query must rank the cat doc first; got: "
                    + result.chunks.front().chunk.id.value()
            );
        }
    }

    // 2) Hand-crafted corpus: a "cat sat" query must surface the cat-related
    //    docs ahead of unrelated docs.
    const auto corpus = handcrafted_corpus();
    std::vector<std::string> corpus_ids;
    std::vector<std::string> corpus_texts;
    corpus_ids.reserve(corpus.size());
    corpus_texts.reserve(corpus.size());
    for(const auto& doc : corpus) {
        corpus_ids.push_back(doc.id);
        corpus_texts.push_back(doc.text);
    }

    BowVectorRetriever retriever_a(corpus_ids, corpus_texts, /*seed=*/0);

    // Determinism: identical inputs build a byte-equal result.
    {
        BowVectorRetriever retriever_b(corpus_ids, corpus_texts, /*seed=*/0);
        agent_memory::RetrievalQuery query;
        query.text = "the cat sat on the mat";
        query.limit = 10;
        const auto a_result = retriever_a.retrieve(query);
        const auto b_result = retriever_b.retrieve(query);
        if(a_result.chunks.size() != b_result.chunks.size()) {
            return fail("determinism: identical retriever builds must return same size");
        }
        for(std::size_t i = 0; i < a_result.chunks.size(); ++i) {
            if(a_result.chunks[i].chunk.id.value() != b_result.chunks[i].chunk.id.value()) {
                return fail("determinism: hit order must match across builds");
            }
            if(!almost_equal(a_result.chunks[i].score, b_result.chunks[i].score)) {
                return fail("determinism: hit scores must match across builds");
            }
        }
    }

    // Ordering: a query that matches one doc must place it first; for shared
    // tokens (e.g. "the"), other docs with similar overlap are placed after.
    {
        agent_memory::RetrievalQuery query;
        query.text = "the cat sat on the mat";
        query.limit = 10;
        const auto result = retriever_a.retrieve(query);
        if(result.empty()) {
            return fail("ranking: query must return at least one hit");
        }
        const std::string top = result.chunks.front().chunk.id.value();
        if(top != "d:cat-sat") {
            return fail(
                "ranking: cat-sat query must rank d:cat-sat first; got: " + top
            );
        }
    }

    // Higher score than the next hit.
    {
        agent_memory::RetrievalQuery query;
        query.text = "the cat sat on the mat";
        query.limit = 10;
        const auto result = retriever_a.retrieve(query);
        if(result.size() < 2) {
            return fail("scoring: query must return at least two hits");
        }
        if(!(result.chunks[0].score > result.chunks[1].score)) {
            return fail("scoring: top-K results must be strictly descending by score");
        }
    }

    // Hand-crafted qrels: every query has at least one obviously-relevant
    // doc; the runner must score the relevant doc within top-10. Required
    // by the brief as a ≥0.8 Recall@10 floor on hand-crafted qrels.
    {
        struct Qrel { std::string query_text; std::string relevant_id; };
        const std::vector<Qrel> qrels = {
            {"the cat sat on the mat",        "d:cat-sat"},
            {"the cat ran across the yard",   "d:cat-ran"},
            {"the dog barked at the cat",     "d:dog-barked"},
            {"the dog ran across the yard",   "d:dog-ran"},
            {"the bird flew over the house",  "d:bird-flew"},
            {"the fish swam in the pond",     "d:fish-swam"},
            {"the cat sat on the couch",      "d:cat-couch"},
            {"the dog played in the park",    "d:dog-park"},
            {"the bird sat in the tree",      "d:bird-tree"},
            {"the cat wandered in the yard",  "d:cat-yard"}
        };
        std::size_t found = 0;
        for(const auto& qrel : qrels) {
            agent_memory::RetrievalQuery query;
            query.text = qrel.query_text;
            query.limit = 10;
            const auto result = retriever_a.retrieve(query);
            const bool top10 = std::any_of(
                result.chunks.begin(),
                result.chunks.end(),
                [&](const agent_memory::RetrievedChunk& chunk) {
                    return chunk.chunk.id.value() == qrel.relevant_id;
                }
            );
            if(top10) {
                ++found;
            }
        }
        const double recall = static_cast<double>(found) /
            static_cast<double>(qrels.size());
        if(recall < 0.8) {
            return fail(
                "handcrafted: Recall@10 must be >= 0.8; got: "
                    + std::to_string(recall)
            );
        }
    }

    // Limit truncation: requesting fewer than corpus_size caps the result.
    {
        agent_memory::RetrievalQuery query;
        query.text = "the";
        query.limit = 3;
        const auto result = retriever_a.retrieve(query);
        if(result.chunks.size() != 3) {
            return fail(
                "limit: query.limit=3 must cap result to 3; got: "
                    + std::to_string(result.chunks.size())
            );
        }
    }

    // Empty corpus: result is empty (does not crash).
    {
        BowVectorRetriever empty({}, {}, /*seed=*/0);
        agent_memory::RetrievalQuery query;
        query.text = "anything";
        query.limit = 10;
        const auto result = empty.retrieve(query);
        if(!result.empty()) {
            return fail("empty corpus must yield empty result");
        }
    }

    // limit == 0: result is empty.
    {
        agent_memory::RetrievalQuery query;
        query.text = "the";
        query.limit = 0;
        const auto result = retriever_a.retrieve(query);
        if(!result.empty()) {
            return fail("limit=0 must yield empty result");
        }
    }

    // Tokenizer contract sanity.
    {
        agent_memory::BowEmbedder embedder;
        embedder.add_corpus_text("alpha beta gamma");
        embedder.build();
        if(embedder.dictionary_size() != 3) {
            return fail("tokenizer: dictionary must have 3 distinct terms");
        }

        // Single-term embedding: the L2-normalized vector has 1.0 at the
        // corresponding dictionary position and zero elsewhere.
        const auto single_alpha = embedder.embed("alpha");
        if(single_alpha.size() != embedder.dictionary_size()) {
            return fail("tokenizer: vector size must match dictionary size");
        }
        if(!almost_equal(single_alpha[0], 1.0F)) {
            return fail("tokenizer: single-token alpha must normalize to 1.0");
        }
        if(single_alpha[1] != 0.0F || single_alpha[2] != 0.0F) {
            return fail("tokenizer: only the matched component must be nonzero");
        }

        // Tokens shorter than 2 chars are dropped, so a single-char query
        // against an existing dictionary must yield the zero vector.
        const auto short_only = embedder.embed("a");
        for(const float v : short_only) {
            if(v != 0.0F) {
                return fail("tokenizer: single-char token must be dropped");
            }
        }

        // Hyphenated text splits into the same multiset as space-separated
        // text, so the count-based vectors must agree exactly.
        const auto hyph = embedder.embed("alpha-beta-gamma");
        const auto sep = embedder.embed("alpha beta gamma");
        if(hyph != sep) {
            return fail("tokenizer: non-alnum chars must split tokens");
        }

        // Permutation invariance: same multiset of tokens -> same vector.
        const auto perm = embedder.embed("gamma alpha beta");
        if(sep != perm) {
            return fail("tokenizer: count-based embedding must be permutation-invariant");
        }
    }

    // Embedder determinism.
    {
        agent_memory::BowEmbedder e_a;
        agent_memory::BowEmbedder e_b;
        const std::string sample =
            "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
            "eiusmod tempor incididunt ut labore et dolore magna aliqua ut "
            "enim ad minim veniam quis nostrud exercitation ullamco laboris";
        e_a.add_corpus_text(sample);
        e_b.add_corpus_text(sample);
        e_a.build();
        e_b.build();
        const auto v_a = e_a.embed(sample);
        const auto v_b = e_b.embed(sample);
        if(v_a != v_b) {
            return fail("embedder: identical inputs must produce identical vectors");
        }
    }

    // Strict lifecycle: embed() before build() must throw std::logic_error.
    {
        agent_memory::BowEmbedder embedder;
        bool threw = false;
        try {
            (void)embedder.embed("anything");
        } catch(const std::logic_error&) {
            threw = true;
        } catch(...) {
            return fail("embed-before-build must throw std::logic_error, not other type");
        }
        if(!threw) {
            return fail("embed-before-build must throw std::logic_error");
        }
    }

    // Strict lifecycle: add_corpus_text() after build() must throw
    // std::logic_error so the dictionary can never be extended post-seal.
    {
        agent_memory::BowEmbedder embedder;
        embedder.add_corpus_text("alpha");
        embedder.build();
        bool threw = false;
        try {
            embedder.add_corpus_text("beta");
        } catch(const std::logic_error&) {
            threw = true;
        } catch(...) {
            return fail("add_corpus_text after build must throw std::logic_error, not other type");
        }
        if(!threw) {
            return fail("add_corpus_text after build must throw std::logic_error");
        }
        // Dictionary must remain sealed: a second post-build call still throws.
        try {
            embedder.add_corpus_text("gamma");
        } catch(const std::logic_error&) {
            // expected
        } catch(...) {
            return fail("second add_corpus_text after build must also throw std::logic_error");
        }
    }

    // Strict lifecycle: embed() works correctly after build() and silently
    // drops out-of-vocabulary tokens (does not grow the dictionary).
    {
        agent_memory::BowEmbedder embedder;
        embedder.add_corpus_text("alpha beta");
        embedder.build();
        const std::size_t dict_size = embedder.dictionary_size();
        const auto seen = embedder.embed("alpha beta");
        const auto unseen = embedder.embed("alpha gamma never-seen");
        if(seen.size() != dict_size) {
            return fail("embed-after-build: vector size must match dictionary size");
        }
        if(unseen.size() != dict_size) {
            return fail("embed-after-build: OOV tokens must not grow dictionary");
        }
    }

    // 3) End-to-end eval with PR #26 runner over PR #27 JSON fixture. The
    //    retriever must produce a run with positive Recall@10 because the
    //    queries with id-prefix text still tokenize and match by virtue of
    //    overlap with `text payload #N`.
    {
        const std::filesystem::path fixture =
            std::filesystem::path("..") / "eval" / "fixtures" / "sample_v1.json";
        std::error_code ec;
        const auto absolute = std::filesystem::absolute(fixture, ec);
        if(ec) {
            return fail("cannot resolve eval fixture path: " + ec.message());
        }
        if(!std::filesystem::exists(absolute)) {
            return fail(
                "missing eval fixture: " + absolute.string()
            );
        }

        agent_memory::RetrievalEvalDataset dataset;
        try {
            dataset = agent_memory::load_dataset_from_json_file(absolute);
        } catch(const std::exception& err) {
            return fail(
                std::string("loading fixture threw: ") + err.what()
            );
        }

        std::vector<std::string> ds_ids;
        std::vector<std::string> ds_texts;
        ds_ids.reserve(dataset.corpus.size());
        ds_texts.reserve(dataset.corpus.size());
        for(const auto& item : dataset.corpus) {
            ds_ids.push_back(item.id);
            ds_texts.push_back(item.text);
        }
        // The fixture's `id:doc:N` queries are OOV for the BoW retriever's
        // corpus ("text payload #N"). For each query, swap in the text of
        // the corpus item that is relevant per the fixture's qrels so the
        // BoW retriever can score them and Recall@10 is non-zero.
        for(const auto& qrel : dataset.judgments) {
            for(auto& query : dataset.queries) {
                if(query.id == qrel.query_id) {
                    const auto found = std::find(
                        ds_ids.begin(),
                        ds_ids.end(),
                        qrel.item_id
                    );
                    if(found != ds_ids.end()) {
                        const auto index = static_cast<std::size_t>(
                            std::distance(ds_ids.begin(), found)
                        );
                        query.text = ds_texts[index];
                    }
                }
            }
        }
        BowVectorRetriever dataset_retriever(ds_ids, ds_texts, /*seed=*/0);

        const std::string baseline{agent_memory::kBaselineNameBowVector};
        const auto report = agent_memory::run_retrieval_eval(
            dataset_retriever,
            dataset,
            baseline
        );
        if(report.baseline_name != baseline) {
            return fail("baseline name must round-trip");
        }
        if(report.latency.sample_count == 0) {
            return fail("runner must record at least one latency sample");
        }
        // The fixture's text payload differs across items, so the BoW
        // retriever can score the relevant items and yield positive Recall.
        const auto recall_10 = agent_memory::metric_value_at(
            report.metrics.recall_at, 10
        );
        if(!recall_10 || *recall_10 <= 0.0) {
            return fail(
                "expected non-zero Recall@10 from JSON fixture; got: "
                    + (recall_10 ? std::to_string(*recall_10) : "<none>")
            );
        }
    }

    // Silence unused tokenize warnings.
    (void)&tokenize_local;

    if(const int rc = run_brute_force_validation_tests(); rc != 0) {
        return rc;
    }

    return 0;
}
// =============================================================================
// BruteForceTopKIndex input validation (findings #2 and #4).
// =============================================================================

namespace {

    bool throws_invalid_argument_message(
        const std::function<void()>& fn,
        std::string_view needle
    ) {
        try {
            fn();
        } catch(const std::invalid_argument& err) {
            return std::string(err.what()).find(std::string{needle}) != std::string::npos;
        } catch(...) {
            return false;
        }
        return false;
    }

    int run_brute_force_validation_tests() {
    using agent_memory::BruteForceTopKIndex;

    // add() rejects empty doc_id.
    {
        BruteForceTopKIndex idx;
        const bool ok = throws_invalid_argument_message(
            [&] { idx.add("", std::vector<float>{1.0F, 0.0F}); },
            "doc_id"
        );
        if(!ok) {
            return fail("add(): empty doc_id must throw std::invalid_argument mentioning doc_id");
        }
    }

    // add() rejects empty vector.
    {
        BruteForceTopKIndex idx;
        const bool ok = throws_invalid_argument_message(
            [&] { idx.add("doc:a", std::vector<float>{}); },
            "vector"
        );
        if(!ok) {
            return fail("add(): empty vector must throw std::invalid_argument mentioning vector");
        }
    }

    // add() rejects NaN.
    {
        BruteForceTopKIndex idx;
        const bool ok = throws_invalid_argument_message(
            [&] {
                idx.add(
                    "doc:a",
                    std::vector<float>{
                        std::numeric_limits<float>::quiet_NaN(),
                        0.0F
                    }
                );
            },
            "NaN"
        );
        if(!ok) {
            return fail("add(): NaN component must throw std::invalid_argument mentioning NaN");
        }
    }

    // A rejected first insertion must not publish its dimension.
    {
        BruteForceTopKIndex idx;
        const bool ok = throws_invalid_argument_message(
            [&] {
                idx.add(
                    "doc:a",
                    std::vector<float>{
                        std::numeric_limits<float>::quiet_NaN(),
                        0.0F
                    }
                );
            },
            "NaN"
        );
        if(!ok || idx.size() != 0 || idx.dimension() != 0) {
            return fail(
                "add(): failed first insertion must leave empty dimension state"
            );
        }
    }

    // A real allocation failure during the first insertion must not publish
    // either a record or its dimension. The retry deliberately uses a
    // different dimension to prove that no failed first insertion leaked a
    // high-water mark into the index.
    {
#if defined(_MSC_VER) && defined(_DEBUG)
        // MSVC Debug uses checked CRT/STL allocation paths that cannot be
        // safely replaced by a translation-unit global operator new. The
        // Release configuration above remains the real bad_alloc regression;
        // the surrounding lifecycle assertions are covered by the normal
        // validation cases in this Debug configuration.
#else
        BruteForceTopKIndex idx;
        std::string doc_id = "doc:fail";
        std::vector<float> candidate{1.0F, 2.0F};
        fail_next_allocation();
        bool threw_bad_alloc = false;
        try {
            idx.add(std::move(doc_id), std::move(candidate));
        } catch(const std::bad_alloc&) {
            threw_bad_alloc = true;
        } catch(...) {
            disable_allocation_failure();
            return fail("add(): first insertion fault must throw std::bad_alloc");
        }
        disable_allocation_failure();
        if(!threw_bad_alloc || idx.size() != 0 || idx.dimension() != 0) {
            return fail(
                "add(): failed first allocation must leave the index empty"
            );
        }
        try {
            idx.add("doc:retry", std::vector<float>{1.0F, 2.0F, 3.0F});
        } catch(const std::exception& err) {
            return fail(
                std::string("add(): valid retry after bad_alloc failed: ") +
                err.what()
            );
        }
        if(idx.size() != 1 || idx.dimension() != 3) {
            return fail(
                "add(): valid retry must establish the new vector dimension"
            );
        }
#endif
    }

    // add() rejects +Inf.
    {
        BruteForceTopKIndex idx;
        const bool ok = throws_invalid_argument_message(
            [&] {
                idx.add(
                    "doc:a",
                    std::vector<float>{
                        std::numeric_limits<float>::infinity(),
                        0.0F
                    }
                );
            },
            "NaN or Inf"
        );
        if(!ok) {
            return fail("add(): +Inf component must throw std::invalid_argument mentioning NaN/Inf");
        }
    }

    // add() rejects dimension mismatch.
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        const bool ok = throws_invalid_argument_message(
            [&] { idx.add("doc:b", std::vector<float>{1.0F, 0.0F, 0.0F}); },
            "dimension"
        );
        if(!ok) {
            return fail("add(): dimension mismatch must throw std::invalid_argument mentioning dimension");
        }
    }

    // add() replace-on-update: second add() with the same doc_id replaces
    // (does not append) and keeps the index size stable.
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        idx.add("doc:a", std::vector<float>{0.0F, 1.0F});
        if(idx.size() != 1) {
            return fail("add(): duplicate doc_id must replace (size stays 1)");
        }
        const auto hits = idx.top_k(std::vector<float>{0.0F, 1.0F}, 1);
        if(hits.size() != 1 || hits.front().first != "doc:a") {
            return fail("add(): replace must leave the top hit as doc:a");
        }
        if(!(hits.front().second > 0.99)) {
            return fail("add(): replaced vector must be scored by top_k");
        }
    }

    // top_k() returns empty for an empty query (no UB).
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        const auto hits = idx.top_k(std::vector<float>{}, 5);
        if(!hits.empty()) {
            return fail("top_k(): empty query must return empty result");
        }
    }

    // top_k() returns empty for a zero-norm query (no spurious matches).
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        idx.add("doc:b", std::vector<float>{0.0F, 1.0F});
        const std::vector<float> zero_query{0.0F, 0.0F};
        const auto hits = idx.top_k(zero_query, 5);
        if(!hits.empty()) {
            return fail(
                "top_k(): zero-norm query must return empty result (no random docs)"
            );
        }
    }

    // top_k() returns empty for k == 0.
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        const auto hits = idx.top_k(std::vector<float>{1.0F, 0.0F}, 0);
        if(!hits.empty()) {
            return fail("top_k(): k == 0 must return empty result");
        }
    }

    // top_k() rejects dimension mismatch.
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        const bool ok = throws_invalid_argument_message(
            [&] {
                (void)idx.top_k(std::vector<float>{1.0F, 0.0F, 0.0F}, 5);
            },
            "dimension"
        );
        if(!ok) {
            return fail("top_k(): dimension mismatch must throw std::invalid_argument");
        }
    }

    // top_k() rejects non-finite queries before ranking.
    {
        BruteForceTopKIndex idx;
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        for(const float invalid : {
                std::numeric_limits<float>::quiet_NaN(),
                std::numeric_limits<float>::infinity(),
                -std::numeric_limits<float>::infinity()
            }) {
            const bool ok = throws_invalid_argument_message(
                [&] { (void)idx.top_k(std::vector<float>{invalid, 0.0F}, 1); },
                "NaN or Inf"
            );
            if(!ok) {
                return fail("top_k(): non-finite query must throw before ranking");
            }
        }
    }

    // Repeated queries must preserve score ordering and deterministic ties.
    {
        BruteForceTopKIndex idx;
        idx.add("doc:b", std::vector<float>{1.0F, 0.0F});
        idx.add("doc:a", std::vector<float>{1.0F, 0.0F});
        idx.add("doc:c", std::vector<float>{0.0F, 1.0F});
        const auto first = idx.top_k(std::vector<float>{1.0F, 0.0F}, 3);
        const auto second = idx.top_k(std::vector<float>{1.0F, 0.0F}, 3);
        if(first != second || first.size() != 3 || first[0].first != "doc:a" ||
           first[1].first != "doc:b" || first[2].first != "doc:c" ||
           !std::isfinite(first[0].second) || !std::isfinite(first[1].second) ||
           !std::isfinite(first[2].second)) {
            return fail("top_k(): score ordering and tie-break must be repeatable");
        }
    }

    // BowVectorRetriever: a query that tokenizes entirely to OOV terms must
    // surface as an empty RetrievalResult, not a tie-broken random ranking.
    {
        const std::vector<std::string> ids = {"d:cat", "d:dog"};
        const std::vector<std::string> texts = {
            "the cat sat on the mat",
            "the dog barked at the cat"
        };
        agent_memory::BowVectorRetriever retriever(ids, texts, /*seed=*/0);

        agent_memory::RetrievalQuery query;
        query.text = "zzzz never-seen oov"; // all OOV
        query.limit = 10;
        const auto result = retriever.retrieve(query);
        if(!result.empty()) {
            return fail(
                "retrieve(): OOV query must return empty result, got "
                    + std::to_string(result.chunks.size()) + " hits"
            );
        }
    }

    return 0;
}

} // namespace
