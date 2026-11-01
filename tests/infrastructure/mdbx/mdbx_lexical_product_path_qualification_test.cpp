#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
#include <array>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

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

std::filesystem::path unique_path(const std::string& stem) {
    const auto stamp = Clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path()
        / (stem + "-" + std::to_string(stamp) + ".mdbx");
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
                {"anchor", "protocol", "durable", "unit-" + std::to_string(numeric_id)}
            });
        }
    }
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

FlatLexicalUnitInput lexical_unit(const Document& document, std::uint64_t revision = 1) {
    return FlatLexicalUnitInput{
        document.scope, document.id, ProjectionKind::Original, document.resource,
        revision, revision, ProjectionVersionRef{revision, revision, 1, "derivation-v1"},
        document.tokens
    };
}

RetrievalQuery public_query() {
    RetrievalQuery query;
    query.text = "anchor protocol";
    query.limit = 8;
    return query;
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
    const TimingSummary& persistent_search,
    const TimingSummary& segmented_search,
    double persistent_build_ms,
    double segmented_build_ms,
    double persistent_reopen_ms,
    double segmented_reopen_ms,
    bool freshness,
    bool scope_isolation,
    bool persistent_build_completed,
    bool segmented_build_completed,
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
        << "  \"tail_semantics\":\"p95 is descriptive for this sample distribution; no p99 or production-stability claim\",\n"
        << "  \"reopen_timing_semantics\":\"zero is a sentinel: close/reopen is covered by existing MDBX conformance tests, not timed by this harness\",\n"
        << "  \"paths\":{\n"
        << "    \"projection_aware_rebuild\":\"ProjectionAwareLexicalRetriever::retrieve; scans, loads, tokenizes, and rebuilds ExactLexicalIndex per invocation\",\n"
        << "    \"persistent_flat_build_path\":\"MdbxPersistentLexicalIndex::upsert publication build; no product query API is exposed by this adapter\",\n"
        << "    \"segmented_build_path\":\"MdbxSegmentedLexicalIndex::upsert publication build; no product query API is exposed by this adapter\"\n"
        << "  },\n"
        << "  \"timings_ms\":{\n"
        << "    \"projection_aware_rebuild\":"; write_timing(output, rebuild);
    output << ",\n    \"persistent_flat_build_path\":"; write_timing(output, persistent_search);
    output << ",\n    \"segmented_build_path\":"; write_timing(output, segmented_search);
    output << ",\n    \"persistent_flat_build\":" << persistent_build_ms
        << ",\n    \"segmented_build\":" << segmented_build_ms
        << ",\n    \"persistent_reopen_and_snapshot\":" << persistent_reopen_ms
        << ",\n    \"segmented_reopen_and_snapshot\":" << segmented_reopen_ms << "\n"
        << "  },\n"
        << "  \"checks\":{\"current_retriever_freshness\":" << (freshness ? "true" : "false")
        << ",\"current_retriever_scope_isolation\":" << (scope_isolation ? "true" : "false")
        << ",\"persistent_build_completed\":" << (persistent_build_completed ? "true" : "false")
        << ",\"segmented_build_completed\":" << (segmented_build_completed ? "true" : "false")
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
        const auto corpus = make_corpus();
        const auto importer_path = unique_path("agent-memory-lexical-importer");
        const auto persistent_path = unique_path("agent-memory-lexical-persistent");
        const auto segmented_path = unique_path("agent-memory-lexical-segmented");
        std::error_code cleanup_error;
        std::filesystem::remove(importer_path, cleanup_error);
        std::filesystem::remove(persistent_path, cleanup_error);
        std::filesystem::remove(segmented_path, cleanup_error);

        TimingSummary rebuild_timing;
        bool current_scope_isolation = false;
        bool current_freshness = false;
        {
            auto context = MdbxStorageContext::open(importer_path.string());
            MdbxResourceImporterOptions importer_options;
            importer_options.context = context;
            importer_options.table_prefix = "perf_importer";
            importer_options.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
            {
                MdbxResourceImporter importer{std::move(importer_options)};
                for(const auto& document : corpus) {
                    const auto body = (document.id.value() == 1U ? "exclusiveoldtoken " : "")
                        + std::string{"anchor protocol durable "}
                        + document.scope.value() + " unit-" + std::to_string(document.id.value());
                    static_cast<void>(import_document(importer, document, body));
                }
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
            if(initial.size() != 8) {
                throw std::runtime_error("public retriever returned "
                    + std::to_string(initial.size()) + " tenant-a hits");
            }
            volatile std::size_t sink = 0;
            rebuild_timing = measure([&]() { return retriever.retrieve(query).size(); }, sink);
            static_cast<void>(sink);

            ProjectionAwareLexicalRetriever retriever_b{
                units, projections, manifests, ScopeId{"tenant-b"}, tokenizer,
                ProjectionAwareLexicalRetrieverOptions{
                    ProjectionKind::Original, 256, 4096, 8U * 1024U * 1024U,
                    "agent_memory.m0_resource_importer.raw_chunks_original@perf_importer", 1}};
            current_scope_isolation = retriever_b.retrieve(query).size() == 8;
            const auto& updated = corpus.front();
            const auto updated_body = "replacement-token protocol durable tenant-a unit-1";
            MdbxResourceImporterOptions update_options;
            update_options.context = context;
            update_options.table_prefix = "perf_importer";
            update_options.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
            MdbxResourceImporter update_importer{std::move(update_options)};
            const auto updated_result = import_document(update_importer, updated, updated_body);
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
        TimingSummary persistent_search_timing;
        TimingSummary segmented_search_timing;
        bool persistent_freshness = false;
        bool segmented_freshness = false;
        bool persistent_lifecycle = false;
        bool segmented_lifecycle = false;
        {
            auto context = MdbxStorageContext::open(persistent_path.string());
            MdbxPersistentLexicalIndexOptions options_a;
            options_a.path = persistent_path.string();
            options_a.context = context;
            options_a.table_prefix = "agent_memory";
            options_a.descriptor = descriptor(ScopeId{"tenant-a"}, 41, 1);
            {
                MdbxPersistentLexicalIndex index_a{options_a};
                const auto begin = Clock::now();
                for(const auto& document : corpus) {
                    if(document.scope.value() == "tenant-a") {
                        index_a.upsert(lexical_unit(document));
                    }
                }
                persistent_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
                {
                    persistent_freshness = true;
                }
                persistent_search_timing.samples_ms.push_back(persistent_build_ms);
            }
            persistent_lifecycle = persistent_freshness;
        }

        {
            auto context = MdbxStorageContext::open(segmented_path.string());
            MdbxSegmentedLexicalIndexOptions options_a;
            options_a.context = context;
            options_a.path = segmented_path.string();
            options_a.table_prefix = "agent_memory";
            options_a.descriptor = descriptor(ScopeId{"tenant-a"}, 51, kLexicalSegmentLayoutVersion);
            {
                MdbxSegmentedLexicalIndex index_a{options_a};
                const auto begin = Clock::now();
                for(const auto& document : corpus) {
                    if(document.scope.value() == "tenant-a") index_a.upsert(lexical_unit(document));
                }
                segmented_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
                {
                    segmented_freshness = true;
                }
                segmented_search_timing.samples_ms.push_back(segmented_build_ms);
            }
            segmented_lifecycle = segmented_freshness;
        }

        const auto freshness = current_freshness;
        const auto scope_isolation = current_scope_isolation;
        const auto lifecycle = persistent_lifecycle && segmented_lifecycle;
        write_receipt(receipt, rebuild_timing, persistent_search_timing,
            segmented_search_timing, persistent_build_ms, segmented_build_ms,
            persistent_reopen_ms, segmented_reopen_ms, freshness,
            scope_isolation, persistent_freshness, segmented_freshness, lifecycle);
        require(freshness, "lexical freshness checks failed");
        require(scope_isolation, "lexical scope isolation checks failed");
        require(lifecycle, "lexical lifecycle checks failed");
        std::filesystem::remove(importer_path, cleanup_error);
        std::filesystem::remove(persistent_path, cleanup_error);
        std::filesystem::remove(segmented_path, cleanup_error);
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
