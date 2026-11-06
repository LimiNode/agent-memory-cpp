#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <nlohmann/json.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;

constexpr std::size_t kDocumentsPerScope = 48;
constexpr std::size_t kWarmupIterations = 3;
constexpr std::size_t kTimedSamples = 12;

struct Document final {
    ScopeId scope;
    KnowledgeUnitId id;
    ResourceId resource;
    std::vector<std::string> tokens;
};

struct TimingSummary final {
    std::vector<double> samples_ms;

    [[nodiscard]] double percentile(double fraction) const {
        if(samples_ms.empty()) return 0.0;
        auto values = samples_ms;
        std::sort(values.begin(), values.end());
        const auto position = fraction * static_cast<double>(values.size() - 1U);
        const auto lower = static_cast<std::size_t>(position);
        const auto upper = std::min(lower + 1U, values.size() - 1U);
        const auto weight = position - static_cast<double>(lower);
        return values[lower] + (values[upper] - values[lower]) * weight;
    }

    [[nodiscard]] double minimum() const {
        return *std::min_element(samples_ms.begin(), samples_ms.end());
    }

    [[nodiscard]] double maximum() const {
        return *std::max_element(samples_ms.begin(), samples_ms.end());
    }
};

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::string sha256_hex(std::string_view bytes) {
    const auto digest = agent_memory::sha256(bytes);
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for(const auto byte : digest) {
        output << std::setw(2) << static_cast<unsigned>(byte);
    }
    return output.str();
}

// Reserve ownership before opening any data/lock file. Matches the B2 fixture.
class TestWorkspace final {
public:
    TestWorkspace() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = Clock::now().time_since_epoch().count();
        for(unsigned attempt = 0; attempt < 64; ++attempt) {
            const auto candidate = root / ("agent-memory-lexical-qualification-"
                + std::to_string(nonce) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                m_directory = candidate;
                return;
            }
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                throw std::runtime_error("temporary directory reservation failed: "
                    + error.message());
            }
        }
        throw std::runtime_error("temporary directory reservation exhausted");
    }
    TestWorkspace(const TestWorkspace&) = delete;
    TestWorkspace& operator=(const TestWorkspace&) = delete;
    ~TestWorkspace() { cleanup(); }
    std::filesystem::path path(const char* name) const { return m_directory / name; }
    const std::filesystem::path& directory() const { return m_directory; }
    void cleanup_or_fail() {
        const auto owned_directory = m_directory;
        cleanup();
        require(!std::filesystem::exists(owned_directory), "temporary workspace cleanup failed");
    }
private:
    void cleanup() noexcept {
        if(m_directory.empty()) return;
        // Only these known files inside our exclusively reserved directory are owned.
        for(const auto name : {"importer.mdbx", "persistent.mdbx", "segmented.mdbx",
                              "receipt.json"}) {
            for(const auto suffix : {"", "-lck"}) {
                const auto file = m_directory / (std::string{name} + suffix);
#ifdef _WIN32
                SetFileAttributesW(file.c_str(), FILE_ATTRIBUTE_NORMAL);
#endif
                std::error_code ignored;
                std::filesystem::remove(file, ignored);
            }
        }
        std::error_code ignored;
        if(std::filesystem::remove(m_directory, ignored)) m_directory.clear();
    }
    std::filesystem::path m_directory;
};

void check_exception_cleanup() {
    std::filesystem::path directory;
    struct ExpectedInterruption {};
    try {
        TestWorkspace workspace;
        directory = workspace.directory();
        auto context = MdbxStorageContext::open(workspace.path("importer.mdbx").string());
        throw ExpectedInterruption{};
    } catch(const ExpectedInterruption&) {}
    require(!std::filesystem::exists(directory), "exception path leaked MDBX data or lock files");
}

std::vector<Document> make_corpus() {
    std::vector<Document> documents;
    documents.reserve(kDocumentsPerScope * 2U);
    for(const auto scope_name : {std::string{"tenant-a"}, std::string{"tenant-b"}}) {
        for(std::size_t index = 0; index < kDocumentsPerScope; ++index) {
            const auto numeric_id = (scope_name == "tenant-a" ? 1U : 1001U)
                + static_cast<unsigned>(index);
            documents.push_back(Document{
                ScopeId{scope_name},
                KnowledgeUnitId{numeric_id},
                ResourceId{"resource-" + scope_name + "-" + std::to_string(index)},
                {"anchor", "protocol", "durable", "unit-" + std::to_string(numeric_id),
                    scope_name == "tenant-a" ? "alphaonlymarker" : "betaonlymarker"}
            });
        }
    }
    return documents;
}

std::vector<Document> load_corpus(const std::filesystem::path& workload_path) {
    std::ifstream input(workload_path);
    require(input.good(), "lexical workload fixture could not be opened");
    const auto workload = nlohmann::json::parse(input);
    require(workload.value("workload_version", "") == "lexical_product_path_workload_v1",
        "lexical workload version is unsupported");
    const auto& rows = workload.at("documents");
    require(rows.is_array() && rows.size() == workload.at("document_count").get<std::size_t>(),
        "lexical workload document count is inconsistent");
    StandardTokenizer tokenizer;
    std::vector<Document> documents;
    documents.reserve(rows.size());
    for(const auto& row : rows) {
        const auto id = row.at("id").get<std::string>();
        const auto separator = id.find(':');
        require(separator != std::string::npos, "lexical workload ID is malformed");
        const auto numeric_id = std::stoul(id.substr(separator + 1));
        const auto body = row.at("normalized_text").get<std::string>();
        require(row.at("body_digest").get<std::string>() == sha256_hex(body),
            "lexical workload body digest does not match normalized bytes");
        const auto tokenization = tokenizer.tokenize(body);
        std::vector<std::string> tokens;
        tokens.reserve(tokenization.tokens.size());
        for(const auto& token : tokenization.tokens) tokens.push_back(token.text);
        documents.push_back(Document{
            ScopeId{row.at("scope_id").get<std::string>()},
            KnowledgeUnitId{numeric_id},
            ResourceId{row.at("resource_id").get<std::string>()},
            std::move(tokens)
        });
    }
    require(documents.size() == kDocumentsPerScope * 2U,
        "smoke workload must contain two 48-document scopes");
    return documents;
}

ResourceImportResult import_document(
    MdbxResourceImporter& importer,
    const Document& document,
    const std::string& body
) {
    ObservedResource resource;
    resource.scope_id = document.scope;
    resource.resource_id = document.resource;
    resource.source_uri = "memory://lexical-product-path";
    resource.utf8_body = body;
    return importer.import(std::move(resource));
}

LexicalIndexDescriptor descriptor(const ScopeId& scope, std::uint8_t seed,
    std::uint32_t layout) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = seed;
    epoch[15] = static_cast<std::uint8_t>(seed + 1U);
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{scope, ProjectionKind::Original,
            LexicalStatisticsEpoch{epoch}},
        "original", 1, "tokenizer-v1", "normalization-v1", {layout}
    };
}

FlatLexicalUnitInput lexical_unit(
    const Document& document,
    std::uint64_t revision = 1,
    std::vector<std::string> tokens = {}
) {
    if(tokens.empty()) tokens = document.tokens;
    return FlatLexicalUnitInput{
        document.scope, document.id, ProjectionKind::Original, document.resource,
        revision, revision, ProjectionVersionRef{revision, revision, 1, "derivation-v1"},
        std::move(tokens)
    };
}

std::vector<FlatLexicalUnitInput> lexical_units_for_scope(
    const std::vector<Document>& corpus,
    const ScopeId& scope
) {
    std::vector<FlatLexicalUnitInput> result;
    for(const auto& document : corpus) {
        if(document.scope == scope) result.push_back(lexical_unit(document));
    }
    return result;
}

RetrievalQuery public_query() {
    RetrievalQuery query;
    query.text = "anchor protocol";
    query.limit = 8;
    return query;
}

bool matches_public_ids(const RetrievalResult& result,
    const std::set<std::string>& allowed, std::size_t expected_count) {
    std::set<std::string> observed;
    for(const auto& hit : result.chunks) {
        if(allowed.count(hit.chunk.id.value()) != 1
            || !observed.insert(hit.chunk.id.value()).second) return false;
    }
    return observed.size() == expected_count;
}

bool has_posting_for_unit(
    const FlatLexicalIndex& index,
    TokenId token,
    KnowledgeUnitId unit_id
) {
    const auto postings = index.postings(token);
    return std::any_of(postings.begin(), postings.end(),
        [unit_id](const FlatLexicalPosting& posting) {
            return posting.unit_id == unit_id;
        });
}

void check_index(const FlatLexicalIndex& index, const ScopeId& scope,
    const std::vector<FlatLexicalUnitInput>& expected) {
    require(index.descriptor().ref.scope_id == scope, "snapshot scope mismatch");
    require(index.collection_stats().unit_count == expected.size(), "snapshot unit count mismatch");
    std::map<std::string, std::map<KnowledgeUnitId, std::uint32_t>> postings_by_text;
    for(const auto& input : expected) {
        const auto stats = index.find_unit_stats(input.unit_id);
        require(stats.has_value(), "snapshot omitted expected unit");
        require(stats->resource_id == input.resource_id, "snapshot resource identity mismatch");
        require(stats->resource_generation == input.resource_generation,
            "snapshot resource generation mismatch");
        require(stats->unit_revision == input.unit_revision,
            "snapshot unit revision mismatch");
        for(const auto& token : input.normalized_tokens) ++postings_by_text[token][input.unit_id];
    }
    // Include retired dictionary entries: they must have no unexpected postings.
    for(const auto& entry : index.token_dictionary_for_storage()) {
        const auto& expected_postings = postings_by_text[entry.first];
        const auto actual = index.postings(entry.second);
        require(actual.size() == expected_postings.size(), "unexpected or missing scope postings");
        for(const auto& posting : actual) {
            const auto found = expected_postings.find(posting.unit_id);
            require(found != expected_postings.end() && found->second == posting.term_frequency,
                "posting unit identity/frequency mismatch");
            const auto input = std::find_if(expected.begin(), expected.end(), [&](const auto& unit) {
                return unit.unit_id == posting.unit_id;
            });
            require(input != expected.end() && posting.resource_id == input->resource_id
                && posting.resource_generation == input->resource_generation,
                "posting resource/projection revision mismatch");
        }
    }
    for(const auto& entry : postings_by_text) {
        if(!entry.second.empty()) require(index.find_token_id(entry.first).has_value(),
            "snapshot omitted expected token");
    }
}

template <typename Callable>
TimingSummary measure(Callable&& callable, volatile std::size_t& sink) {
    for(std::size_t iteration = 0; iteration < kWarmupIterations; ++iteration) {
        sink += callable();
    }
    TimingSummary result;
    result.samples_ms.reserve(kTimedSamples);
    for(std::size_t iteration = 0; iteration < kTimedSamples; ++iteration) {
        const auto begin = Clock::now();
        sink += callable();
        const auto elapsed = Clock::now() - begin;
        result.samples_ms.push_back(
            std::chrono::duration<double, std::milli>(elapsed).count());
    }
    return result;
}

void write_timing(std::ostream& output, const TimingSummary& timing) {
    output << "{\"sample_count\":" << timing.samples_ms.size()
        << ",\"min_ms\":" << timing.minimum()
        << ",\"median_ms\":" << timing.percentile(0.5)
        << ",\"p95_ms\":" << timing.percentile(0.95)
        << ",\"max_ms\":" << timing.maximum() << "}";
}

void write_receipt(
    const std::filesystem::path& path,
    const TimingSummary& rebuild,
    const TimingSummary& persistent_build,
    const TimingSummary& segmented_build,
    double persistent_build_ms,
    double segmented_build_ms,
    double persistent_reopen_ms,
    double segmented_reopen_ms,
    double source_import_ms,
    double targeted_update_ms,
    bool freshness,
    bool scope_isolation,
    bool tenant_a_marker_identity,
    bool tenant_a_rejected_foreign_marker,
    bool tenant_b_marker_identity,
    bool tenant_b_rejected_foreign_marker,
    bool persistent_correctness,
    bool segmented_correctness,
    bool lifecycle
) {
    std::ofstream output(path, std::ios::binary);
    if(!output) throw std::runtime_error("cannot write lexical qualification receipt");
    output << std::setprecision(10)
        << "{\n"
        << "  \"schema_version\":1,\n"
        << "  \"family\":\"lexical_product_path_performance_qualification_v1\",\n"
        << "  \"measurement_status\":\"directional_local_snapshot\",\n"
        << "  \"comparability\":\"PERFORMANCE_INCONCLUSIVE\",\n"
        << "  \"corpus_documents_per_scope\":" << kDocumentsPerScope << ",\n"
        << "  \"warmup_iterations\":" << kWarmupIterations << ",\n"
        << "  \"projection_rebuild_sample_count\":" << kTimedSamples << ",\n"
        << "  \"sample_count_semantics\":\"timed invocations in one process; persistent paths use one bounded publication-build sample\",\n"
        << "  \"component_timing_semantics\":\"this harness measures public end-to-end retrieval; internal scan/read/tokenization/scoring stages are not separately instrumented\",\n"
        << "  \"scaling_semantics\":\"one bounded 48-document-per-scope smoke workload; no multi-size scaling claim\",\n"
        << "  \"tail_semantics\":\"p95 is descriptive for this sample distribution; no p99 or production-stability claim\",\n"
        << "  \"reopen_timing_semantics\":\"measures one close/reopen snapshot read for each adapter; not product query latency\",\n"
        << "  \"workload_binding\":{\"executed_profile\":\"lexical_product_path_workload_v1:smoke\",\"documents\":"
        << (kDocumentsPerScope * 2U)
        << ",\"scopes\":2,\"generated_fixture_status\":\"EXECUTED\",\"generated_fixture_is_executed\":true},\n"
        << "  \"execution_identity\":{\"git_sha\":\""
        << (std::getenv("GITHUB_SHA") != nullptr ? std::getenv("GITHUB_SHA") : "LOCAL")
        << "\",\"ci_run_id\":\""
        << (std::getenv("GITHUB_RUN_ID") != nullptr ? std::getenv("GITHUB_RUN_ID") : "LOCAL")
        << "\",\"stage_names\":[\"source_import\",\"public_rebuild_query\",\"targeted_update\",\"persistent_publication\",\"segmented_publication\",\"persistent_reopen\",\"segmented_reopen\"]},\n"
        << "  \"paths\":{\n"
        << "    \"projection_aware_rebuild\":\"ProjectionAwareLexicalRetriever::retrieve; scans, loads, tokenizes, and rebuilds ExactLexicalIndex per invocation\",\n"
        << "    \"persistent_flat_build_path\":\"MdbxPersistentLexicalIndex::upsert publication build; no product query API is exposed by this adapter\",\n"
        << "    \"segmented_build_path\":\"MdbxSegmentedLexicalIndex::upsert publication build; no product query API is exposed by this adapter\"\n"
        << "  },\n"
        << "  \"timings_ms\":{\n"
        << "    \"projection_aware_rebuild\":"; write_timing(output, rebuild);
    output << ",\n    \"persistent_flat_build_path\":"; write_timing(output, persistent_build);
    output << ",\n    \"segmented_build_path\":"; write_timing(output, segmented_build);
    output << ",\n    \"persistent_flat_build\":" << persistent_build_ms
        << ",\n    \"segmented_build\":" << segmented_build_ms
        << ",\n    \"persistent_reopen_and_snapshot\":" << persistent_reopen_ms
        << ",\n    \"segmented_reopen_and_snapshot\":" << segmented_reopen_ms
        << ",\n    \"source_import\":" << source_import_ms
        << ",\n    \"targeted_update\":" << targeted_update_ms << "\n"
        << "  },\n"
        << "  \"checks\":{\"current_retriever_freshness\":" << (freshness ? "true" : "false")
        << ",\"current_retriever_scope_isolation\":" << (scope_isolation ? "true" : "false")
        << ",\"tenant_a_marker_identity\":" << (tenant_a_marker_identity ? "true" : "false")
        << ",\"tenant_a_rejected_foreign_marker\":"
        << (tenant_a_rejected_foreign_marker ? "true" : "false")
        << ",\"tenant_b_marker_identity\":" << (tenant_b_marker_identity ? "true" : "false")
        << ",\"tenant_b_rejected_foreign_marker\":"
        << (tenant_b_rejected_foreign_marker ? "true" : "false")
        << ",\"persistent_correctness_gates\":" << (persistent_correctness ? "true" : "false")
        << ",\"segmented_correctness_gates\":" << (segmented_correctness ? "true" : "false")
        << ",\"lifecycle_smoke\":" << (lifecycle ? "true" : "false") << "}\n"
        << "}\n";
}

} // namespace

int main(int argc, char** argv) {
    try {
        const auto receipt = argc > 1
            ? std::filesystem::path{argv[1]}
            : std::filesystem::temp_directory_path()
                / "agent_memory_lexical_product_path_qualification.json";
        const auto corpus = argc > 2
            ? load_corpus(std::filesystem::path{argv[2]})
            : make_corpus();
        TestWorkspace workspace;
        check_exception_cleanup();
        const auto importer_path = workspace.path("importer.mdbx");
        const auto persistent_path = workspace.path("persistent.mdbx");
        const auto segmented_path = workspace.path("segmented.mdbx");

        TimingSummary rebuild_timing;
        bool current_scope_isolation = false;
        bool tenant_a_marker_identity = false;
        bool tenant_a_rejected_foreign_marker = false;
        bool tenant_b_marker_identity = false;
        bool tenant_b_rejected_foreign_marker = false;
        bool current_freshness = false;
        double source_import_ms = 0.0;
        double targeted_update_ms = 0.0;
        std::set<std::string> tenant_a_ids;
        std::set<std::string> tenant_b_ids;
        {
            auto context = MdbxStorageContext::open(importer_path.string());
            MdbxResourceImporterOptions importer_options;
            importer_options.context = context;
            importer_options.table_prefix = "perf_importer";
            importer_options.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
            {
                MdbxResourceImporter importer{std::move(importer_options)};
                const auto import_begin = Clock::now();
                for(const auto& document : corpus) {
                    const auto body = (document.id.value() == 1U ? "exclusiveoldtoken " : "")
                        + std::string{"anchor protocol durable "}
                        + document.scope.value() + " unit-" + std::to_string(document.id.value()) + " "
                        + (document.scope == ScopeId{"tenant-a"} ? "alphaonlymarker" : "betaonlymarker");
                    const auto imported = import_document(importer, document, body);
                    require(!imported.raw_units.empty(), "importer returned no raw unit identity");
                    auto& ids = document.scope == ScopeId{"tenant-a"}
                        ? tenant_a_ids : tenant_b_ids;
                    ids.insert("knowledge-unit:" +
                        std::to_string(imported.raw_units.front().value()));
                }
                source_import_ms = std::chrono::duration<double, std::milli>(
                    Clock::now() - import_begin).count();
            }

            {
            MdbxKnowledgeUnitStoreOptions unit_options;
            unit_options.context = context;
            unit_options.table_prefix = "perf_importer";
            MdbxKnowledgeUnitStore units{std::move(unit_options)};
            MdbxProjectionStoreOptions projection_options;
            projection_options.context = context;
            projection_options.table_prefix = "perf_importer";
            MdbxProjectionStore projections{std::move(projection_options)};
            MdbxResourceManifestStorageOptions manifest_options;
            manifest_options.context = context;
            manifest_options.table_prefix = "perf_importer";
            MdbxResourceManifestStorage manifests{std::move(manifest_options)};
            StandardTokenizer tokenizer;
            ProjectionAwareLexicalRetriever retriever{
                units, projections, manifests, ScopeId{"tenant-a"}, tokenizer,
                ProjectionAwareLexicalRetrieverOptions{
                    ProjectionKind::Original, 256, 4096, 8U * 1024U * 1024U,
                    "agent_memory.m0_resource_importer.raw_chunks_original@perf_importer", 1}};
            const auto query = public_query();
            const auto initial = retriever.retrieve(query);
            require(matches_public_ids(initial, tenant_a_ids, 8),
                "public retriever returned a foreign or duplicate tenant-a identity");
            volatile std::size_t sink = 0;
            rebuild_timing = measure([&]() { return retriever.retrieve(query).size(); }, sink);
            static_cast<void>(sink);

            ProjectionAwareLexicalRetriever retriever_b{
                units, projections, manifests, ScopeId{"tenant-b"}, tokenizer,
                ProjectionAwareLexicalRetrieverOptions{
                    ProjectionKind::Original, 256, 4096, 8U * 1024U * 1024U,
                    "agent_memory.m0_resource_importer.raw_chunks_original@perf_importer", 1}};
            const auto tenant_b_result = retriever_b.retrieve(query);
            current_scope_isolation = matches_public_ids(tenant_b_result, tenant_b_ids, 8);
            RetrievalQuery alpha_query;
            alpha_query.text = "alphaonlymarker";
            alpha_query.limit = 8;
            RetrievalQuery beta_query;
            beta_query.text = "betaonlymarker";
            beta_query.limit = 8;
            tenant_a_marker_identity =
                matches_public_ids(retriever.retrieve(alpha_query), tenant_a_ids, 8);
            tenant_a_rejected_foreign_marker =
                retriever.retrieve(beta_query).empty();
            const auto tenant_b_marker_result = retriever_b.retrieve(beta_query);
            tenant_b_marker_identity =
                matches_public_ids(tenant_b_marker_result, tenant_b_ids, 8);
            tenant_b_rejected_foreign_marker =
                retriever_b.retrieve(alpha_query).empty();
            current_scope_isolation = current_scope_isolation
                && tenant_a_marker_identity
                && tenant_a_rejected_foreign_marker
                && tenant_b_marker_identity
                && tenant_b_rejected_foreign_marker;
            const auto& updated = corpus.front();
            const auto updated_body = "replacement-token protocol durable tenant-a unit-1";
            MdbxResourceImporterOptions update_options;
            update_options.context = context;
            update_options.table_prefix = "perf_importer";
            update_options.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
            MdbxResourceImporter update_importer{std::move(update_options)};
            const auto update_begin = Clock::now();
            const auto updated_result = import_document(update_importer, updated, updated_body);
            targeted_update_ms = std::chrono::duration<double, std::milli>(
                Clock::now() - update_begin).count();
            require(updated_result.revision.generation > 1,
                "importer did not create a new revision for freshness check");
            RetrievalQuery replacement_query;
            replacement_query.text = "replacement-token";
            replacement_query.limit = 8;
            RetrievalQuery old_query;
            old_query.text = "exclusiveoldtoken";
            old_query.limit = 8;
            const auto replacement_result = retriever.retrieve(replacement_query);
            const auto old_result = retriever.retrieve(old_query);
            current_freshness = replacement_result.size() == 1 && old_result.empty();
            }
            context.reset();
        }

        double persistent_build_ms = 0.0;
        double segmented_build_ms = 0.0;
        double persistent_reopen_ms = 0.0;
        double segmented_reopen_ms = 0.0;
        TimingSummary persistent_build_timing;
        TimingSummary segmented_build_timing;
        bool persistent_freshness = false;
        bool segmented_freshness = false;
        bool persistent_lifecycle = false;
        bool segmented_lifecycle = false;
        {
            auto context = MdbxStorageContext::open(persistent_path.string());
            const auto tenant_a_inputs = lexical_units_for_scope(corpus, ScopeId{"tenant-a"});
            MdbxPersistentLexicalIndexOptions options_a;
            options_a.path = persistent_path.string();
            options_a.context = context;
            options_a.table_prefix = "agent_memory";
            options_a.descriptor = descriptor(ScopeId{"tenant-a"}, 41, 1);
            std::vector<FlatLexicalUnitInput> persistent_expected_after_erase;
            {
                MdbxPersistentLexicalIndex index_a{options_a};
                const auto begin = Clock::now();
                for(const auto& document : corpus) {
                    if(document.scope.value() == "tenant-a") {
                        index_a.upsert(lexical_unit(document));
                    }
                }
                persistent_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
                std::optional<TokenId> old_token;
                std::vector<FlatLexicalUnitInput> expected_after_update;
                {
                    const auto initial = index_a.read_snapshot();
                    check_index(initial.index, ScopeId{"tenant-a"}, tenant_a_inputs);
                    persistent_freshness =
                        initial.index.collection_stats().unit_count == kDocumentsPerScope
                        && initial.index.find_unit_stats(KnowledgeUnitId{1})->unit_revision == 1;
                    old_token = initial.index.find_token_id("anchor");
                    require(old_token.has_value(), "persistent snapshot omitted anchor token");
                    require(!initial.index.postings(*old_token).empty(),
                        "persistent snapshot omitted anchor postings");
                }
                const auto& updated = corpus.front();
                const auto updated_unit = lexical_unit(updated, 2,
                    {"replacement-token", "protocol", "durable", "unit-1"});
                expected_after_update = tenant_a_inputs;
                const auto updated_input = std::find_if(expected_after_update.begin(),
                    expected_after_update.end(), [&](const auto& input) {
                        return input.unit_id == updated.id;
                    });
                require(updated_input != expected_after_update.end(),
                    "persistent expected state omitted replaced unit");
                *updated_input = updated_unit;
                index_a.upsert(updated_unit,
                    FlatLexicalExpectedState{
                        updated.id, 1, 1, ProjectionVersionRef{1, 1, 1, "derivation-v1"}});
                {
                    const auto after_update = index_a.read_snapshot();
                    check_index(after_update.index, ScopeId{"tenant-a"}, expected_after_update);
                    const auto replacement = after_update.index.find_token_id("replacement-token");
                    require(replacement.has_value()
                        && has_posting_for_unit(after_update.index, *replacement,
                            KnowledgeUnitId{1}),
                        "persistent replacement posting missing");
                    require(!has_posting_for_unit(after_update.index, *old_token,
                        KnowledgeUnitId{1}),
                        "persistent old posting survived replacement");
                }
                require(index_a.erase(KnowledgeUnitId{2}),
                    "persistent erase did not remove an active unit");
                persistent_expected_after_erase = expected_after_update;
                persistent_expected_after_erase.erase(std::remove_if(
                    persistent_expected_after_erase.begin(),
                    persistent_expected_after_erase.end(), [](const auto& input) {
                        return input.unit_id == KnowledgeUnitId{2};
                    }), persistent_expected_after_erase.end());
                const auto after_erase = index_a.read_snapshot();
                check_index(after_erase.index, ScopeId{"tenant-a"},
                    persistent_expected_after_erase);
                require(after_erase.index.find_unit_stats(KnowledgeUnitId{2}) == std::nullopt,
                    "persistent erased unit remained in stats");
                persistent_freshness = true;
                persistent_build_timing.samples_ms.push_back(persistent_build_ms);
            }
            const auto reopen_begin = Clock::now();
            MdbxPersistentLexicalIndex reopened{options_a};
            {
                const auto reopened_snapshot = reopened.read_snapshot();
                check_index(reopened_snapshot.index, ScopeId{"tenant-a"},
                    persistent_expected_after_erase);
                require(reopened_snapshot.index.find_unit_stats(KnowledgeUnitId{2})
                    == std::nullopt, "persistent reopen retained erased unit");
            }
            persistent_reopen_ms = std::chrono::duration<double, std::milli>(
                Clock::now() - reopen_begin).count();
            persistent_lifecycle = persistent_freshness;
            require(persistent_freshness, "persistent update/erase checks failed");
            MdbxPersistentLexicalIndexOptions options_b = options_a;
            options_b.descriptor = descriptor(ScopeId{"tenant-b"}, 42, 1);
            MdbxPersistentLexicalIndex other_scope{options_b};
            other_scope.upsert(lexical_unit(corpus.back()));
            check_index(other_scope.read_snapshot().index, ScopeId{"tenant-b"},
                {lexical_unit(corpus.back())});
            persistent_lifecycle = persistent_lifecycle &&
                other_scope.read_snapshot().index.collection_stats().unit_count == 1;
        }

        {
            auto context = MdbxStorageContext::open(segmented_path.string());
            const auto tenant_a_inputs = lexical_units_for_scope(corpus, ScopeId{"tenant-a"});
            MdbxSegmentedLexicalIndexOptions options_a;
            options_a.context = context;
            options_a.path = segmented_path.string();
            options_a.table_prefix = "agent_memory";
            options_a.descriptor = descriptor(ScopeId{"tenant-a"}, 51, kLexicalSegmentLayoutVersion);
            std::vector<FlatLexicalUnitInput> segmented_expected_after_erase;
            {
                MdbxSegmentedLexicalIndex index_a{options_a};
                std::vector<FlatLexicalUnitInput> expected_after_update;
                const auto begin = Clock::now();
                for(const auto& document : corpus) {
                    if(document.scope.value() == "tenant-a") index_a.upsert(lexical_unit(document));
                }
                segmented_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
                {
                    const auto initial = index_a.read_snapshot();
                    check_index(initial.index, ScopeId{"tenant-a"}, tenant_a_inputs);
                    segmented_freshness =
                        initial.index.collection_stats().unit_count == kDocumentsPerScope
                        && initial.index.find_unit_stats(KnowledgeUnitId{1})->unit_revision == 1;
                }
                const auto& updated = corpus.front();
                const auto updated_unit = lexical_unit(updated, 2,
                    {"replacement-token", "protocol", "durable", "unit-1"});
                expected_after_update = tenant_a_inputs;
                const auto updated_input = std::find_if(expected_after_update.begin(),
                    expected_after_update.end(), [&](const auto& input) {
                        return input.unit_id == updated.id;
                    });
                require(updated_input != expected_after_update.end(),
                    "segmented expected state omitted replaced unit");
                *updated_input = updated_unit;
                index_a.upsert(updated_unit,
                    FlatLexicalExpectedState{
                        updated.id, 1, 1, ProjectionVersionRef{1, 1, 1, "derivation-v1"}});
                {
                    const auto after_update = index_a.read_snapshot();
                    check_index(after_update.index, ScopeId{"tenant-a"}, expected_after_update);
                    const auto replacement = after_update.index.find_token_id("replacement-token");
                    const auto old_token = after_update.index.find_token_id("anchor");
                    require(replacement.has_value()
                        && has_posting_for_unit(after_update.index, *replacement,
                            KnowledgeUnitId{1}),
                        "segmented replacement posting missing");
                    require(old_token.has_value()
                        && !has_posting_for_unit(after_update.index, *old_token,
                            KnowledgeUnitId{1}),
                        "segmented old posting survived replacement");
                }
                require(index_a.erase(KnowledgeUnitId{2}),
                    "segmented erase did not remove an active unit");
                segmented_expected_after_erase = expected_after_update;
                segmented_expected_after_erase.erase(std::remove_if(
                    segmented_expected_after_erase.begin(),
                    segmented_expected_after_erase.end(), [](const auto& input) {
                        return input.unit_id == KnowledgeUnitId{2};
                    }), segmented_expected_after_erase.end());
                const auto after_erase = index_a.read_snapshot();
                check_index(after_erase.index, ScopeId{"tenant-a"},
                    segmented_expected_after_erase);
                require(after_erase.index.find_unit_stats(KnowledgeUnitId{2}) == std::nullopt,
                    "segmented erased unit remained in stats");
                segmented_freshness = true;
                segmented_build_timing.samples_ms.push_back(segmented_build_ms);
            }
            const auto reopen_begin = Clock::now();
            MdbxSegmentedLexicalIndex reopened{options_a};
            {
                const auto reopened_snapshot = reopened.read_snapshot();
                check_index(reopened_snapshot.index, ScopeId{"tenant-a"},
                    segmented_expected_after_erase);
                require(reopened_snapshot.index.find_unit_stats(KnowledgeUnitId{2})
                    == std::nullopt, "segmented reopen retained erased unit");
            }
            segmented_reopen_ms = std::chrono::duration<double, std::milli>(
                Clock::now() - reopen_begin).count();
            segmented_lifecycle = segmented_freshness;
            MdbxSegmentedLexicalIndexOptions options_b = options_a;
            options_b.descriptor = descriptor(ScopeId{"tenant-b"}, 52, kLexicalSegmentLayoutVersion);
            MdbxSegmentedLexicalIndex other_scope{options_b};
            other_scope.upsert(lexical_unit(corpus.back()));
            check_index(other_scope.read_snapshot().index, ScopeId{"tenant-b"},
                {lexical_unit(corpus.back())});
            segmented_lifecycle = segmented_lifecycle &&
                other_scope.read_snapshot().index.collection_stats().unit_count == 1;
        }

        const auto freshness = current_freshness;
        const auto scope_isolation = current_scope_isolation;
        const auto lifecycle = persistent_lifecycle && segmented_lifecycle;
        write_receipt(receipt, rebuild_timing, persistent_build_timing,
            segmented_build_timing, persistent_build_ms, segmented_build_ms,
            persistent_reopen_ms, segmented_reopen_ms, source_import_ms,
            targeted_update_ms, freshness,
            scope_isolation, tenant_a_marker_identity,
            tenant_a_rejected_foreign_marker, tenant_b_marker_identity,
            tenant_b_rejected_foreign_marker, persistent_freshness,
            segmented_freshness, lifecycle);
        require(freshness, "lexical freshness checks failed");
        require(scope_isolation, "lexical scope isolation checks failed");
        require(persistent_lifecycle, "persistent lexical lifecycle checks failed");
        require(segmented_lifecycle, "segmented lexical lifecycle checks failed");
        require(lifecycle, "lexical lifecycle checks failed");
        workspace.cleanup_or_fail();
        std::cout << "lexical product-path qualification receipt: " << receipt.string() << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else

int main(int, char**) { return 0; }

#endif
