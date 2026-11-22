#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalCandidateAdapter.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <new>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <malloc.h>
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

// Executable-local diagnostic only. MDBX malloc/mmap and process RSS are not
// intercepted. Counters are disabled during every latency measurement.
namespace allocation_diagnostic {
thread_local bool enabled = false;
thread_local std::size_t count = 0;
thread_local std::size_t bytes = 0;

void record(const std::size_t size) noexcept {
    if(enabled) { ++count; bytes += size; }
}

void* allocate(const std::size_t size) {
    const auto actual = std::max<std::size_t>(size, 1U);
    if(auto* pointer = std::malloc(actual)) { record(actual); return pointer; }
    throw std::bad_alloc{};
}

void* allocate_aligned(const std::size_t size, const std::size_t alignment) {
    if(size > std::numeric_limits<std::size_t>::max() - (alignment - 1U))
        throw std::bad_alloc{};
    const auto actual = size == 0U ? alignment : ((size + alignment - 1U) / alignment) * alignment;
    void* pointer = nullptr;
#if defined(_WIN32)
    pointer = _aligned_malloc(actual, alignment);
#else
    if(posix_memalign(&pointer, alignment, actual) != 0) pointer = nullptr;
#endif
    if(pointer) { record(actual); return pointer; }
    throw std::bad_alloc{};
}

void free_aligned(void* pointer) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}

struct Scope final {
    Scope() { count = 0; bytes = 0; enabled = true; }
    ~Scope() { enabled = false; }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
};
} // namespace allocation_diagnostic

void* operator new(std::size_t n) { return allocation_diagnostic::allocate(n); }
void* operator new[](std::size_t n) { return allocation_diagnostic::allocate(n); }
void* operator new(std::size_t n, const std::nothrow_t&) noexcept {
    try { return allocation_diagnostic::allocate(n); } catch(...) { return nullptr; }
}
void* operator new[](std::size_t n, const std::nothrow_t&) noexcept {
    try { return allocation_diagnostic::allocate(n); } catch(...) { return nullptr; }
}
void* operator new(std::size_t n, std::align_val_t a) {
    return allocation_diagnostic::allocate_aligned(n, static_cast<std::size_t>(a));
}
void* operator new[](std::size_t n, std::align_val_t a) {
    return allocation_diagnostic::allocate_aligned(n, static_cast<std::size_t>(a));
}
void* operator new(std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept {
    try { return allocation_diagnostic::allocate_aligned(n, static_cast<std::size_t>(a)); }
    catch(...) { return nullptr; }
}
void* operator new[](std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept {
    try { return allocation_diagnostic::allocate_aligned(n, static_cast<std::size_t>(a)); }
    catch(...) { return nullptr; }
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete(void* p, std::align_val_t) noexcept { allocation_diagnostic::free_aligned(p); }
void operator delete[](void* p, std::align_val_t) noexcept { allocation_diagnostic::free_aligned(p); }
void operator delete(void* p, std::size_t, std::align_val_t) noexcept { allocation_diagnostic::free_aligned(p); }
void operator delete[](void* p, std::size_t, std::align_val_t) noexcept { allocation_diagnostic::free_aligned(p); }
void operator delete(void* p, std::align_val_t, const std::nothrow_t&) noexcept { allocation_diagnostic::free_aligned(p); }
void operator delete[](void* p, std::align_val_t, const std::nothrow_t&) noexcept { allocation_diagnostic::free_aligned(p); }

namespace {
using namespace agent_memory;
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr std::size_t kWarmups = 2;
constexpr std::size_t kRepeats = 7;
constexpr double kAbsoluteTolerance = 1e-5;
constexpr double kRelativeTolerance = 1e-5;
const ScopeId kScope{"candidate-qualification"};
volatile std::size_t g_sink = 0;

void require(const bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::string digest(const std::string& text) {
    std::ostringstream stream;
    stream << std::hex << std::setfill('0');
    for(const auto byte : sha256(text)) stream << std::setw(2) << static_cast<unsigned>(byte);
    return stream.str();
}

std::vector<std::string> tokenize(const StandardTokenizer& tokenizer, const std::string& text) {
    std::vector<std::string> terms;
    for(const auto& token : tokenizer.tokenize(text).tokens) terms.push_back(token.text);
    return terms;
}

struct Document final {
    std::uint64_t id;
    std::uint64_t revision;
    std::vector<std::string> tokens;
};

std::vector<Document> corpus(const std::size_t count, const bool mixed,
                             const StandardTokenizer& tokenizer) {
    std::vector<Document> documents;
    for(std::size_t index = 0; index < count; ++index) {
        std::string text = "common common ";
        if(index % 64U == 0U) text += "rare ";
        if(index % 4U == 0U) text += "MdbxStorageContext open ";
        if(index % 8U == 0U) text += u8"память protocol ";
        auto terms = tokenize(tokenizer, text);
        const auto length = mixed && index % 2U != 0U ? 128U : 16U;
        while(terms.size() < length) terms.push_back("filler");
        documents.push_back(Document{index + 1U, 1U, std::move(terms)});
    }
    return documents;
}

std::string corpus_digest(const std::vector<Document>& documents) {
    Json rows = Json::array();
    for(const auto& document : documents)
        rows.push_back(Json::array({document.id, document.revision, document.tokens}));
    return digest(rows.dump());
}

FlatLexicalUnitInput input(const Document& document) {
    return FlatLexicalUnitInput{
        kScope, KnowledgeUnitId{document.id}, ProjectionKind::Original,
        ResourceId{"candidate-resource-" + std::to_string(document.id)},
        document.revision, document.revision,
        ProjectionVersionRef{document.revision, document.revision, 1U,
                             "m0.original.primary_text.v1"}, document.tokens};
}

MdbxPersistentLexicalIndexOptions options(const std::filesystem::path& path,
    const std::shared_ptr<MdbxStorageContext>& context) {
    std::array<std::uint8_t, 16> epoch{};
    epoch.front() = 41U;
    epoch.back() = 19U;
    MdbxPersistentLexicalIndexOptions result;
    result.path = path.string();
    result.context = context;
    result.table_prefix = "candidate_qualification";
    result.descriptor = LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{kScope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
        "original", 1U, "standard-v1", "lowercase-v1", {1U}};
    return result;
}

class Workspace final {
public:
    explicit Workspace(const std::filesystem::path& parent) {
        const auto stamp = Clock::now().time_since_epoch().count();
        for(unsigned attempt = 0; attempt < 128U; ++attempt) {
            const auto candidate = parent / ("candidate-qualification-" + std::to_string(stamp)
                                              + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) { directory_ = candidate; return; }
            if(error && error != std::make_error_code(std::errc::file_exists))
                throw std::runtime_error("cannot reserve candidate database directory");
        }
        throw std::runtime_error("candidate database directory attempts exhausted");
    }
    ~Workspace() {
        // The explicit normal-path check reports cleanup failure. During stack
        // unwinding this fallback must not replace the original exception.
        try { cleanup(); } catch(const std::exception&) {}
    }
    void cleanup_or_fail() {
        cleanup();
        require(!std::filesystem::exists(directory_), "candidate workspace cleanup failed");
    }
    Workspace(const Workspace&) = delete;
    Workspace& operator=(const Workspace&) = delete;
    std::filesystem::path database() const { return directory_ / "index.mdbx"; }
private:
    void cleanup() {
        // Only fixed files in this exclusively created directory are owned.
        for(const auto* name : {"index.mdbx", "index.mdbx-lck"}) {
            const auto path = directory_ / name;
#if defined(_WIN32)
            SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
#endif
            std::error_code error;
            std::filesystem::remove(path, error);
        }
        std::error_code error;
        std::filesystem::remove(directory_, error);
    }
    std::filesystem::path directory_;
};

struct Hit final { std::uint64_t id; double score; };

// Independent arithmetic and collection statistics from input tokens, never
// the production scorer or the reconstructed snapshot statistics.
std::vector<Hit> oracle(const std::vector<Document>& documents,
    const std::vector<std::string>& terms, const bool bm25) {
    std::size_t total_length = 0;
    std::map<std::string, std::size_t> df;
    for(const auto& document : documents) {
        total_length += document.tokens.size();
        std::map<std::string, std::size_t> counts;
        for(const auto& term : document.tokens) ++counts[term];
        for(const auto& entry : counts) ++df[entry.first];
    }
    std::vector<Hit> hits;
    for(const auto& document : documents) {
        double score = 0.0;
        for(const auto& term : terms) {
            const auto tf = static_cast<double>(std::count(document.tokens.begin(), document.tokens.end(), term));
            if(tf == 0.0) continue;
            if(!bm25) { score += tf; continue; }
            const auto count = static_cast<double>(documents.size());
            const auto frequency = static_cast<double>(df.at(term));
            const auto idf = std::log(1.0 + (count - frequency + 0.5) / (frequency + 0.5));
            const auto ratio = static_cast<double>(document.tokens.size()) / (static_cast<double>(total_length) / count);
            score += idf * (tf * 2.5) / (tf + 1.5 * (0.25 + 0.75 * ratio));
        }
        if(score > 0.0) hits.push_back(Hit{document.id, score});
    }
    std::sort(hits.begin(), hits.end(), [](const auto& left, const auto& right) {
        return left.score == right.score ? left.id < right.id : left.score > right.score;
    });
    return hits;
}

std::vector<Hit> observed(const PersistentLexicalCandidateResponse& response) {
    std::vector<Hit> hits;
    for(const auto& candidate : response.candidates)
        hits.push_back(Hit{candidate.identity.knowledge_unit_id.value(), candidate.identity.score});
    return hits;
}

bool matches(const std::vector<Hit>& actual, const std::vector<Hit>& expected) {
    if(actual.size() != expected.size()) return false;
    for(std::size_t index = 0; index < actual.size(); ++index) {
        if(actual[index].id != expected[index].id || !std::isfinite(actual[index].score)
           || !std::isfinite(expected[index].score)
           || std::abs(actual[index].score - expected[index].score)
              > std::max(kAbsoluteTolerance, kRelativeTolerance *
                    std::max(std::abs(actual[index].score), std::abs(expected[index].score)))) return false;
    }
    return true;
}

void add_hits(Json& row, const char* ids_key, const char* scores_key, const std::vector<Hit>& hits) {
    row[ids_key] = Json::array(); row[scores_key] = Json::array();
    for(const auto& hit : hits) { row[ids_key].push_back(hit.id); row[scores_key].push_back(hit.score); }
}

PersistentLexicalCandidateOptions ranking_options(const std::size_t count, const bool bm25) {
    PersistentLexicalCandidateOptions result{count, count, 32U};
    if(bm25) result.scoring = PersistentLexicalScoring::Bm25;
    return result;
}

Json evidence(MdbxPersistentLexicalCandidateAdapter& adapter, const RetrievalQuery& query,
    const std::vector<Document>& documents, const std::vector<std::string>& terms, const bool bm25) {
    const auto response = adapter.generate_window(query);
    require(response.completeness == PersistentLexicalCandidateCompleteness::Complete,
            "unbounded qualification window was incomplete");
    const auto expected = oracle(documents, terms, bm25);
    const auto actual = observed(response);
    require(matches(actual, expected), "candidate score/ranking differs from independent oracle");
    Json row{{"completeness", "Complete"}, {"examined_postings", response.examined_postings},
             {"corpus_sha256", corpus_digest(documents)}};
    add_hits(row, "expected_ids", "expected_scores", expected);
    add_hits(row, "actual_ids", "actual_scores", actual);
    return row;
}

template<class Callable> double elapsed(Callable&& operation) {
    const auto start = Clock::now();
    operation();
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

std::array<Json, 2> measure_pair(MdbxPersistentLexicalIndex& index, StandardTokenizer& tokenizer,
    const std::vector<Document>& documents, const std::string& query_id, const std::string& text,
    const std::size_t limit, const std::string& length_profile) {
    const auto terms = tokenize(tokenizer, text);
    RetrievalQuery query; query.text = text; query.limit = limit;
    MdbxPersistentLexicalCandidateAdapter tf{index, tokenizer, ranking_options(documents.size(), false)};
    MdbxPersistentLexicalCandidateAdapter bm25{index, tokenizer, ranking_options(documents.size(), true)};
    std::array<MdbxPersistentLexicalCandidateAdapter*, 2> adapters{{&tf, &bm25}};
    std::array<Json, 2> rows;
    for(std::size_t policy = 0; policy < 2U; ++policy) {
        rows[policy] = evidence(*adapters[policy], query, documents, terms, policy != 0U);
        auto& row = rows[policy];
        row["id"] = std::to_string(documents.size()) + ":" + length_profile + ":" + query_id
            + ":" + std::to_string(limit) + ":" + (policy == 0U ? "tf" : "bm25");
        row["N"] = documents.size(); row["length_profile"] = length_profile;
        row["query_id"] = query_id; row["query_text"] = text; row["query_terms"] = terms;
        row["K"] = limit; row["policy"] = policy == 0U ? "TF" : "BM25";
        for(const auto* key : {"samples_ms", "allocation_counts", "allocated_bytes", "snapshot_ms", "tokenization_ms"})
            row[key] = Json::array();
    }
    const auto invoke = [&](const std::size_t policy) {
        // Response destruction and its pinned-view work remain inside this call.
        const auto response = adapters[policy]->generate_window(query);
        g_sink += response.candidates.size();
    };
    for(std::size_t iteration = 0; iteration < kWarmups; ++iteration)
        for(std::size_t arm = 0; arm < 2U; ++arm) invoke((iteration + arm) % 2U);
    for(std::size_t sample = 0; sample < kRepeats; ++sample) {
        for(std::size_t arm = 0; arm < 2U; ++arm) {
            const auto policy = (sample + arm) % 2U;
            rows[policy]["samples_ms"].push_back(elapsed([&]() { invoke(policy); }));
        }
        for(std::size_t arm = 0; arm < 2U; ++arm) {
            const auto policy = (sample + arm) % 2U;
            {
                allocation_diagnostic::Scope allocations;
                invoke(policy);
            }
            rows[policy]["allocation_counts"].push_back(allocation_diagnostic::count);
            rows[policy]["allocated_bytes"].push_back(allocation_diagnostic::bytes);
            rows[policy]["snapshot_ms"].push_back(elapsed([&]() {
                const auto snapshot = index.read_snapshot(); g_sink += snapshot.index.size();
            }));
            rows[policy]["tokenization_ms"].push_back(elapsed([&]() {
                const auto tokenized = tokenizer.tokenize(text); g_sink += tokenized.tokens.size();
            }));
        }
    }
    return rows;
}

void append_lifecycle(Json& receipt, MdbxPersistentLexicalIndex& index,
    StandardTokenizer& tokenizer, const std::vector<Document>& documents, const std::string& phase) {
    RetrievalQuery query; query.text = "common"; query.limit = 1U;
    for(const bool bm25 : {false, true}) {
        MdbxPersistentLexicalCandidateAdapter adapter{index, tokenizer, ranking_options(documents.size(), bm25)};
        auto row = evidence(adapter, query, documents, {"common"}, bm25);
        row["phase"] = phase; row["policy"] = bm25 ? "BM25" : "TF";
        receipt["lifecycle"].push_back(std::move(row));
    }
}

void check_budgets_and_defaults(Json& receipt, MdbxPersistentLexicalIndex& index,
    StandardTokenizer& tokenizer, const std::size_t count) {
    RetrievalQuery query; query.text = "common"; query.limit = 1U;
    MdbxPersistentLexicalCandidateAdapter legacy{index, tokenizer};
    PersistentLexicalCandidateOptions aggregate{count, count, 32U};
    require(aggregate.scoring == PersistentLexicalScoring::TermFrequency,
            "legacy aggregate changed its default scoring policy");
    MdbxPersistentLexicalCandidateAdapter explicit_tf{index, tokenizer, aggregate};
    require(matches(observed(legacy.generate_window(query)), observed(explicit_tf.generate_window(query))),
            "legacy default TF differs from explicit TF");
    require(legacy.generate(query).candidates.size() == 1U,
            "legacy generate no longer applies its pre-admission query limit");
    receipt["controls"]["default_tf"] = true;
    for(const bool posting_budget : {false, true}) {
        auto bounded = ranking_options(count, true);
        if(posting_budget) bounded.max_postings_per_term = 1U; else bounded.max_candidates = 1U;
        MdbxPersistentLexicalCandidateAdapter adapter{index, tokenizer, bounded};
        require(adapter.generate_window(query).completeness == PersistentLexicalCandidateCompleteness::Truncated,
                "budget-limited window was represented as complete");
        receipt["controls"][posting_budget ? "posting_budget_truncated" : "candidate_budget_truncated"] = true;
    }
}

Json run(const std::filesystem::path& output, const bool smoke) {
    const auto parent = output.has_parent_path() ? output.parent_path() : std::filesystem::current_path();
    std::filesystem::create_directories(parent);
    Json receipt{
        {"schema_version", 1}, {"protocol", "persistent_lexical_candidate_qualification_v1"},
        {"smoke", smoke}, {"recipe_version", "technical_fixed_tokens_v1"},
        {"source", {{"compiled_commit", AGENT_MEMORY_CANDIDATE_SOURCE_COMMIT},
                    {"expected_head", AGENT_MEMORY_CANDIDATE_EXPECTED_HEAD},
                    {"source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
                    {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
                    {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
                    {"json_version", std::to_string(NLOHMANN_JSON_VERSION_MAJOR) + "."
                        + std::to_string(NLOHMANN_JSON_VERSION_MINOR) + "."
                        + std::to_string(NLOHMANN_JSON_VERSION_PATCH)},
                    {"build_configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
                    {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
                    {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
                    {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
                    {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS},
                    {"base_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
                    {"configuration_flags_sha256", AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
                    {"libmdbx_revision", AGENT_MEMORY_CANDIDATE_LIBMDBX_REVISION},
                    {"mdbx_containers_revision", AGENT_MEMORY_CANDIDATE_MDBX_CONTAINERS_REVISION}}},
        {"hardware", {{"hardware_threads", std::thread::hardware_concurrency()},
                      {"processor_identifier_diagnostic", std::getenv("PROCESSOR_IDENTIFIER")
                          ? std::getenv("PROCESSOR_IDENTIFIER") : "unavailable"}}},
        {"measurement", {{"warmups", kWarmups}, {"repeats", kRepeats},
                         {"scope", "inclusive_generate_window_response_lifetime"},
                         {"observation_unit", "repeated_fixed_query_in_one_process"},
                         {"allocation_scope", "cxx_new_only_not_mdbx_malloc_rss_or_peak"},
                         {"allocations_in_latency_samples", false},
                         {"arm_order", "alternate_tf_bm25_by_sample"},
                         {"diagnostic_semantics", "independent_operations_not_subtractable"}}},
        {"score_tolerance", {{"absolute", kAbsoluteTolerance}, {"relative", kRelativeTolerance}}},
        {"bm25_options", {{"k1", 1.5}, {"b", 0.75}}},
        {"quality_status", "SYNTHETIC_MECHANICS_NOT_APPLIED_QUALITY"},
        {"qualification_status", "DIRECTIONAL_BASELINE_NOT_RELEASE_QUALIFIED"},
        {"cases", Json::array()}, {"lifecycle", Json::array()},
        {"controls", {{"default_tf", false}, {"k_window_invariance", false},
                      {"candidate_budget_truncated", false}, {"posting_budget_truncated", false},
                      {"update", false}, {"erase", false}, {"reopen", false}}}};
    StandardTokenizer tokenizer;
    const std::array<std::pair<std::string, std::string>, 6> queries{{
        {"common", "common"}, {"rare", "rare"}, {"absent", "absenttoken"},
        {"repeated", "rare rare common"}, {"identifier", "MdbxStorageContext open"},
        {"ru_en", u8"память protocol"}}};
    bool first = true;
    for(const auto count : {32U, 128U, 512U}) {
        if(smoke && count > 128U) continue;
        for(const bool mixed : {false, true}) {
            auto documents = corpus(count, mixed, tokenizer);
            Workspace workspace{parent};
            const std::string profile = mixed ? "mixed" : "short";
            {
                auto context = MdbxStorageContext::open(workspace.database().string());
                MdbxPersistentLexicalIndex index{options(workspace.database(), context)};
                for(const auto& document : documents) index.upsert(input(document));
                for(const auto& query : queries) {
                    auto one = measure_pair(index, tokenizer, documents, query.first, query.second, 1U, profile);
                    auto ten = measure_pair(index, tokenizer, documents, query.first, query.second, 10U, profile);
                    for(std::size_t policy = 0; policy < 2U; ++policy) {
                        require(one[policy]["actual_ids"] == ten[policy]["actual_ids"]
                             && one[policy]["actual_scores"] == ten[policy]["actual_scores"],
                                "query limit changed full candidate window");
                        receipt["cases"].push_back(std::move(one[policy]));
                        receipt["cases"].push_back(std::move(ten[policy]));
                    }
                }
                receipt["controls"]["k_window_invariance"] = true;
                if(first) {
                    check_budgets_and_defaults(receipt, index, tokenizer, count);
                    const auto old = input(documents.front());
                    documents.front().revision = 2U;
                    documents.front().tokens = tokenize(tokenizer, "common replacement changed");
                    index.upsert(input(documents.front()), FlatLexicalExpectedState{
                        old.unit_id, old.resource_generation, old.unit_revision, old.projection_version});
                    append_lifecycle(receipt, index, tokenizer, documents, "update");
                    receipt["controls"]["update"] = true;
                    require(index.erase(KnowledgeUnitId{documents[1].id}), "erase did not remove active unit");
                    documents.erase(documents.begin() + 1);
                    append_lifecycle(receipt, index, tokenizer, documents, "erase");
                    receipt["controls"]["erase"] = true;
                }
            }
            {
                auto context = MdbxStorageContext::open(workspace.database().string());
                MdbxPersistentLexicalIndex reopened{options(workspace.database(), context)};
                append_lifecycle(receipt, reopened, tokenizer, documents,
                    "reopen-" + std::to_string(count) + "-" + profile);
            }
            receipt["controls"]["reopen"] = true;
            workspace.cleanup_or_fail();
            first = false;
        }
    }
    for(const auto& control : receipt["controls"].items())
        require(control.value().get<bool>(), "qualification control did not execute");
    receipt["correctness_status"] = "PASS";
    return receipt;
}

void self_test() {
    std::vector<Document> documents{{1U, 1U, {"alpha", "alpha"}},
        {2U, 1U, {"alpha", "alpha", "alpha", "filler", "filler", "filler"}},
        {3U, 1U, {"alpha", "alpha"}}, {4U, 1U, {"beta", "filler", "filler", "filler"}}};
    const auto bm25 = oracle(documents, {"alpha"}, true);
    require(bm25.size() == 3U && bm25[0].id == 1U && bm25[1].id == 3U && bm25[2].id == 2U,
            "independent oracle tie/ranking self-test failed");
    require(std::abs(bm25[0].score - 0.5909407355197933) < 1e-12
         && std::abs(bm25[2].score - 0.5043888096103286) < 1e-12,
            "independent BM25 analytic golden self-test failed");
    require(oracle(documents, {"alpha"}, false).front().id == 2U, "TF self-test failed");
    require(oracle(documents, {"absent"}, true).empty(), "absent term self-test failed");
    require(std::abs(oracle(documents, {"alpha", "alpha"}, true)[0].score - 2.0 * bm25[0].score) < 1e-12,
            "repeated-term self-test failed");
    auto mutated = bm25; mutated[0].score += 0.1;
    require(!matches(mutated, bm25), "score mutation was accepted");
    mutated = bm25; mutated[0].id = 99U;
    require(!matches(mutated, bm25), "identity mutation was accepted");
    mutated = bm25; mutated[0].score = std::numeric_limits<double>::quiet_NaN();
    require(!matches(bm25, mutated), "non-finite oracle score was accepted");
    const auto before = corpus_digest(documents);
    documents.front().tokens.push_back("changed");
    require(corpus_digest(documents) != before, "corpus digest mutation was accepted");
    {
        allocation_diagnostic::Scope measured;
        void* ordinary = ::operator new(17U);
        void* aligned = ::operator new(65U, std::align_val_t{64U});
        ::operator delete(ordinary); ::operator delete(aligned, std::align_val_t{64U});
    }
    require(!allocation_diagnostic::enabled && allocation_diagnostic::count == 2U
         && allocation_diagnostic::bytes >= 82U, "allocation diagnostic self-test failed");
    std::cout << "persistent lexical candidate qualification self-test: PASS (10 controls)\n";
}
} // namespace

int main(int argc, char** argv) {
    try {
        std::filesystem::path output;
        bool smoke = false;
        bool run_self_test = false;
        for(int index = 1; index < argc; ++index) {
            const std::string argument{argv[index]};
            if(argument == "--smoke") smoke = true;
            else if(argument == "--self-test") run_self_test = true;
            else if(argument == "--output" && index + 1 < argc) output = argv[++index];
            else throw std::invalid_argument("expected --smoke, --self-test or --output <path>");
        }
        if(run_self_test) { self_test(); return 0; }
        require(!output.empty(), "--output is required");
        const auto receipt = run(output, smoke);
        std::ofstream stream(output, std::ios::binary);
        require(stream.good(), "cannot create qualification receipt");
        stream << receipt.dump(2) << '\n';
        stream.close();
        require(stream.good(), "cannot complete qualification receipt");
        std::cout << "persistent lexical candidate qualification: PASS cases="
                  << receipt.at("cases").size() << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "persistent lexical candidate qualification: FAIL: " << error.what() << '\n';
        return 1;
    }
}
