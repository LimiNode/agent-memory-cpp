#include <agent_memory/infrastructure/mdbx/MdbxExactIndexGenerationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using namespace agent_memory;
const ScopeId scope{"installed-exact"};
const std::array<const char*, 3> resources{
    "resource:exact-a", "resource:exact-b", "resource:exact-c"};
const std::array<std::vector<float>, 3> vectors{{{3, 0}, {1, 2}, {1, -2}}};
const std::vector<float> query_vector{2, 0};
using Units = std::array<KnowledgeUnitEnvelope, 3>;

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

template<class F>
void rejects(IndexGenerationFailure expected, F&& operation) {
    try {
        operation();
    } catch(const IndexGenerationError& error) {
        require(error.code() == expected, "unexpected typed exact-index rejection");
        return;
    }
    throw std::runtime_error("invalid exact-index request was accepted");
}

ExactIndexSpaceDescriptor descriptor(const std::string& metric) {
    ExactIndexSpaceDescriptor result;
    result.model_id = "synthetic-explicit-fp32-consumer-v1";
    result.recipe_id = "fixed-three-vectors-two-coordinates-v1";
    result.dimensions = 2;
    result.scope_id = scope;
    result.metric = metric;
    return result;
}

void import_source(const std::shared_ptr<MdbxStorageContext>& context,
                   const std::filesystem::path& cas, const ScopeId& source_scope,
                   const char* resource, const std::string& body, std::uint64_t time = 1) {
    RawMarkdownProductIngestionOptions options;
    options.context = context;
    options.raw_cas_root = cas;
    MdbxRawMarkdownProductIngestion importer{options};
    const auto result = importer.import(ObservedResource{
        source_scope, ResourceId{resource},
        "https://example.test/" + std::string(resource) + ".md", body,
        SourceTextOrigin::OriginalText, std::nullopt, time}, time);
    require(result.status == RawMarkdownProductImportStatus::Published,
            "canonical source/Original publication failed");
}

Units load_units(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxKnowledgeUnitStoreOptions options;
    options.context = context;
    MdbxKnowledgeUnitStore store{options};
    Units result;
    std::array<bool, 3> found{};
    const auto ids = store.scan_by_scope(scope);
    require(ids.size() == resources.size(), "unexpected canonical fixture size");
    for(const auto& id : ids) {
        const auto unit = store.find(id);
        require(unit && unit->sources.size() == 1, "missing canonical source binding");
        for(std::size_t i = 0; i < resources.size(); ++i) {
            if(unit->sources[0].resource_id == ResourceId{resources[i]}) {
                require(!found[i], "duplicate resource fixture unit");
                result[i] = *unit;
                found[i] = true;
            }
        }
    }
    require(std::all_of(found.begin(), found.end(), [](bool value) { return value; }),
            "fixture resource could not be resolved to a canonical unit ID");
    return result;
}

std::vector<ExactIndexVectorInput> inputs(const Units& units) {
    // Deliberately reverse insertion order: equal scores must use canonical IDs.
    std::vector<ExactIndexVectorInput> result;
    for(std::size_t i = units.size(); i != 0; --i) {
        result.push_back({units[i - 1].id, units[i - 1].revision, vectors[i - 1]});
    }
    return result;
}

void check_numeric(MdbxExactIndexGenerationStore& store,
                   const ExactIndexGenerationSnapshot& pinned, const Units& units,
                   const std::string& metric) {
    const auto hits = store.query(pinned, scope, query_vector, 3,
        [](const KnowledgeUnitEnvelope&) { return true; });
    const auto second = std::min(units[1].id, units[2].id);
    const auto third = std::max(units[1].id, units[2].id);
    // Independent exact arithmetic: dot={6,2,2}; -L2^2={-1,-5,-5}.
    const double best = metric == "dot" ? 6.0 : -1.0;
    const double tied = metric == "dot" ? 2.0 : -5.0;
    require(hits.size() == 3 && hits[0].unit.id == units[0].id &&
                hits[1].unit.id == second && hits[2].unit.id == third &&
                hits[0].score == best && hits[1].score == tied && hits[2].score == tied,
            "independent score golden or deterministic tie order failed");
    for(const auto& hit : hits) {
        require(hit.unit.scope_id == scope && hit.unit.sources.size() == 1 &&
                    hit.unit.sources[0].resource_revision.has_value(),
                "exact hit lost canonical scope or source revision");
    }
}

std::vector<double> verify_metric(const std::shared_ptr<MdbxStorageContext>& context,
                                 const Units& units, const std::string& metric) {
    const auto space = descriptor(metric);
    MdbxExactIndexGenerationStore store{context, "installed-" + metric};
    const auto pinned = store.pin(space);
    require(pinned.generation_id() == "selected-v1" && pinned.size() == 3 &&
                pinned.source_frontier().size() == 32,
            "fresh process lost the selected complete generation");
    rejects(IndexGenerationFailure::Incomplete, [&] { store.complete("interrupted"); });
    rejects(IndexGenerationFailure::Incomplete, [&] { store.activate("interrupted", space); });
    require(store.pin(space).generation_id() == "selected-v1",
            "interrupted build changed the selected generation");
    check_numeric(store, pinned, units, metric);

    const auto allow = [](const KnowledgeUnitEnvelope&) { return true; };
    const auto denied_best = store.query(pinned, scope, query_vector, 1,
        [&](const KnowledgeUnitEnvelope& unit) { return unit.id != units[0].id; });
    require(denied_best.size() == 1 &&
                denied_best[0].unit.id == std::min(units[1].id, units[2].id) &&
                denied_best[0].score == (metric == "dot" ? 2.0 : -5.0),
            "authorization was applied after top-K");
    require(store.query(pinned, scope, query_vector, 3,
                [](const KnowledgeUnitEnvelope&) { return false; }).empty(),
            "deny-all policy exposed a hit");
    rejects(IndexGenerationFailure::InvalidInput, [&] {
        (void)store.query(pinned, scope, query_vector, 1, {});
    });
    rejects(IndexGenerationFailure::Incompatible, [&] {
        (void)store.query(pinned, ScopeId{"foreign"}, query_vector, 1, allow);
    });
    rejects(IndexGenerationFailure::Incompatible, [&] {
        (void)store.query(pinned, scope, {2}, 1, allow);
    });
    rejects(IndexGenerationFailure::InvalidInput, [&] {
        (void)store.query(pinned, scope, {std::numeric_limits<float>::quiet_NaN(), 0}, 1, allow);
    });
    std::size_t zero_limit_calls = 0;
    require(store.query(pinned, scope, query_vector, 0,
                [&](const KnowledgeUnitEnvelope&) { ++zero_limit_calls; return true; }).empty() &&
                zero_limit_calls == 0, "zero-limit query scanned canonical candidates");
    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = context;
    MdbxKnowledgeUnitStore unit_store{unit_options};
    const auto foreign_ids = unit_store.scan_by_scope(ScopeId{"foreign"});
    require(foreign_ids.size() == 1, "missing independent foreign-scope fixture");
    const auto foreign = unit_store.find(foreign_ids.front());
    require(foreign.has_value(), "missing foreign canonical unit");
    store.begin("foreign-scope", space, 1);
    rejects(IndexGenerationFailure::StaleBinding, [&] {
        store.append("foreign-scope", {{foreign->id, foreign->revision, {3, 0}}});
    });
    for(int field = 0; field != 2; ++field) {
        auto mismatch = space;
        if(field == 0) mismatch.model_id += "-other";
        else mismatch.recipe_id += "-other";
        rejects(IndexGenerationFailure::Incompatible, [&] { (void)store.pin(mismatch); });
        rejects(IndexGenerationFailure::Incompatible, [&] { store.activate("replacement-v2", mismatch); });
    }
    store.begin("invalid-input", space, 2);
    rejects(IndexGenerationFailure::InvalidInput, [&] {
        store.append("invalid-input", {
            {units[0].id, units[0].revision, vectors[0]},
            {units[1].id, units[1].revision, {std::numeric_limits<float>::quiet_NaN(), 0}}});
    });
    // A rejected later input must roll back the first row and progress too.
    store.append("invalid-input", {
        {units[0].id, units[0].revision, vectors[0]},
        {units[1].id, units[1].revision, vectors[1]}});
    store.complete("invalid-input");

    bool hook_entered = false;
    store.set_before_activation_commit_hook_for_testing([&] {
        hook_entered = true;
        throw std::runtime_error("injected activation failure");
    });
    try { store.activate("replacement-v2", space); }
    catch(const std::runtime_error&) { }
    store.set_before_activation_commit_hook_for_testing({});
    require(hook_entered && store.pin(space).generation_id() == "selected-v1",
            "failed pointer commit replaced the active generation");
    store.activate("replacement-v2", space);
    require(pinned.generation_id() == "selected-v1" &&
                store.pin(space).generation_id() == "replacement-v2",
            "activation changed immutable pinned data");
    check_numeric(store, pinned, units, metric);
    store.activate("selected-v1", space);

    std::vector<double> samples;
    volatile double consumed_score = 0;
    for(int repeat = 0; repeat != 7; ++repeat) {
        const auto start = std::chrono::steady_clock::now();
        {
            const auto hits = store.query(pinned, scope, query_vector, 3, allow);
            consumed_score = consumed_score + hits.front().score;
        }
        const auto stop = std::chrono::steady_clock::now();
        samples.push_back(std::chrono::duration<double, std::micro>(stop - start).count());
    }
    require(std::isfinite(consumed_score), "query sample checksum is not finite");
    return samples;
}

void write_receipt(const std::filesystem::path& path, const std::string& phase,
                   const Units& units, const std::array<std::vector<double>, 2>& samples = {}) {
    std::ofstream out{path, std::ios::binary | std::ios::trunc};
    require(out.good(), "could not open exact consumer receipt");
    out << std::setprecision(17)
        << "{\n\"schema_version\":1,\"phase\":\"" << phase << "\",\n"
        << "\"quality_status\":\"SYNTHETIC_NUMERICAL_NOT_SEMANTIC_QUALITY\",\n"
        << "\"reopen_profile\":\"CURRENT_TO_CURRENT_SEPARATE_PROCESSES\",\n"
        << "\"model_id\":\"synthetic-explicit-fp32-consumer-v1\","
        << "\"recipe_id\":\"fixed-three-vectors-two-coordinates-v1\",\n"
        << "\"canonical_unit_ids\":[" << units[0].id.value() << ','
        << units[1].id.value() << ',' << units[2].id.value() << "],\n"
        << "\"vectors\":[[3,0],[1,2],[1,-2]],\"query\":[2,0],"
        << "\"dot_goldens\":[6,2,2],\"negative_squared_l2_goldens\":[-1,-5,-5],\n"
        << "\"migration_proof\":false,\"uncommitted_pointer_crash_tested\":false,"
        << "\"stable_p99_claim\":false,\"rss_measured\":false,\n";
    if(phase == "verify") {
        out << "\"gates\":{\"numeric_scores_and_ties\":true,\"authorization_before_k\":true,"
            << "\"scope_and_space_mismatch\":true,\"missing_policy\":true,\"nonfinite_input\":true,"
            << "\"atomic_append\":true,\"failed_activation\":true,\"immutable_pin\":true,"
            << "\"interrupted_build_reopen\":true,\"source_update\":true,\"manifest_erase\":true,"
            << "\"unit_erase\":true},\n";
    }
    out << "\"sample_count_per_metric\":" << samples[0].size()
        << ",\"independent_query_vectors_per_metric\":1,"
        << "\"samples_are_independent_queries\":false,"
        << "\"measurement_process_count\":" << (phase == "verify" ? 1 : 0) << ','
        << "\"warmup_policy\":\"preceding_correctness_queries_no_fixed_count\","
        << "\"pin_and_embedding_generation_included\":false,"
        << "\"measurement_scope\":\"query_and_response_lifetime_including_canonical_admission\",\n"
        << "\"dot_samples_us\":[";
    for(std::size_t metric = 0; metric != samples.size(); ++metric) {
        if(metric != 0) out << "],\"negative_squared_l2_samples_us\":[";
        for(std::size_t i = 0; i < samples[metric].size(); ++i) {
            if(i != 0) out << ',';
            out << samples[metric][i];
        }
    }
    out << "]\n}\n";
    out.close();
    require(out.good(), "exact consumer receipt write failed");
}
} // namespace

int main(int argc, char** argv) try {
    require(argc == 5, "usage: exact_semantic_consumer create|interrupt|verify DB CAS RECEIPT");
    const std::string mode = argv[1];
    require(mode == "create" || mode == "interrupt" || mode == "verify", "unknown consumer mode");
    const std::filesystem::path db{argv[2]}, cas{argv[3]}, receipt{argv[4]};
    if(mode == "create") require(!std::filesystem::exists(db), "create requires a fresh database");
    else require(std::filesystem::exists(db), "reopen requires the existing database");
    auto context = MdbxStorageContext::open(db.string());
    if(mode == "create") {
        for(std::size_t i = 0; i < resources.size(); ++i) {
            import_source(context, cas, scope, resources[i],
                "# Fixed source " + std::to_string(i) + "\n\nSynthetic numerical fixture.\n");
        }
        import_source(context, cas, ScopeId{"foreign"}, "resource:exact-foreign",
            "# Foreign\n\nSeparate-scope synthetic fixture.\n");
    }
    const auto units = load_units(context);
    const std::array<std::string, 2> metrics{{"dot", "negative_squared_l2"}};
    std::array<std::vector<double>, 2> samples;
    for(std::size_t i = 0; i < metrics.size(); ++i) {
        const auto space = descriptor(metrics[i]);
        MdbxExactIndexGenerationStore store{context, "installed-" + metrics[i]};
        if(mode == "create") {
            for(const auto* generation : {"selected-v1", "replacement-v2"}) {
                store.begin(generation, space, units.size());
                store.append(generation, inputs(units));
                store.complete(generation);
            }
            store.activate("selected-v1", space);
            check_numeric(store, store.pin(space), units, metrics[i]);
        } else if(mode == "interrupt") {
            require(store.pin(space).generation_id() == "selected-v1", "selected generation changed");
            store.begin("interrupted", space, units.size());
            store.append("interrupted", {{units[0].id, units[0].revision, vectors[0]}});
        } else {
            samples[i] = verify_metric(context, units, metrics[i]);
        }
    }
    if(mode == "verify") {
        import_source(context, cas, scope, resources[0], "# Updated\n\nReplacement evidence.\n", 2);
        for(const auto& metric : metrics) {
            MdbxExactIndexGenerationStore store{context, "installed-" + metric};
            const auto selected = store.pin(descriptor(metric));
            const auto hits = store.query(selected, scope, query_vector, 3,
                [](const KnowledgeUnitEnvelope&) { return true; });
            require(hits.size() == 2 && hits[0].unit.id != units[0].id &&
                        hits[1].unit.id != units[0].id, "stale source update survived admission");
            rejects(IndexGenerationFailure::StaleBinding, [&] { store.activate("selected-v1", descriptor(metric)); });
        }
        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = context;
        MdbxResourceManifestStorage manifests{manifest_options};
        require(manifests.erase_manifest(ResourceId{resources[1]}), "manifest erase failed");
        for(const auto& metric : metrics) {
            MdbxExactIndexGenerationStore store{context, "installed-" + metric};
            const auto hits = store.query(store.pin(descriptor(metric)), scope, query_vector, 3,
                [](const KnowledgeUnitEnvelope&) { return true; });
            require(hits.size() == 1 && hits[0].unit.id == units[2].id,
                    "erased manifest retained a source-bound hit");
        }
        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = context;
        MdbxKnowledgeUnitStore unit_store{unit_options};
        require(unit_store.erase(units[2].id), "canonical unit erase failed");
        for(const auto& metric : metrics) {
            MdbxExactIndexGenerationStore store{context, "installed-" + metric};
            require(store.query(store.pin(descriptor(metric)), scope, query_vector, 3,
                        [](const KnowledgeUnitEnvelope&) { return true; }).empty(),
                    "erased canonical unit retained a hit");
        }
    }
    write_receipt(receipt, mode, units, samples);
    if(mode == "interrupt") std::_Exit(24); // Append committed; stack cleanup is deliberately skipped.
    std::cout << "exact semantic installed consumer: PASS " << mode << '\n';
    return 0;
} catch(const std::exception& error) {
    std::cerr << "exact semantic installed consumer: " << error.what() << '\n';
    return 1;
}
