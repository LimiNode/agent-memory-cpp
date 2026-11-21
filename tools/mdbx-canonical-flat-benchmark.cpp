#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <thread>
#include <vector>

namespace {
using namespace agent_memory;
using Clock = std::chrono::steady_clock;
using Json = nlohmann::json;
constexpr std::uint64_t kSeed = 20261119;
constexpr const char* kFamily = "mdbx_canonical_comparison_v2";
const std::vector<std::string> kWorkloads{
    "point_block", "outline", "bounded_subtree", "single_block_edit",
    "repeated_edits", "full_materialization", "reopen"};
const std::vector<std::string> kMetrics{
    "elapsed_ms", "cpu_ms", "environment_bytes", "result_blocks", "result_text_bytes",
    "value_reads", "value_writes", "value_bytes_read", "value_bytes_written",
    "block_versions_written", "dictionary_lookups", "encode_ns", "decode_ns", "dictionary_ns"};

struct Sample final {
    std::string backend;
    std::string workload;
    std::size_t scale = 0;
    std::size_t repeat = 0;
    double elapsed_ms = 0;
    double cpu_ms = 0;
    std::uintmax_t environment_bytes = 0;
    std::size_t result_blocks = 0;
    std::size_t result_text_bytes = 0;
    MdbxCanonicalStorageMetrics metrics;
};

std::string digest_hex(std::string_view bytes) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for(const auto byte : sha256(bytes)) out << std::setw(2) << unsigned(byte);
    return out.str();
}

CanonicalDocumentRevision make_revision(std::size_t scale) {
    CanonicalDocumentRevision revision;
    revision.document_id = DocumentId{"comparison-" + std::to_string(scale)};
    revision.metadata.set("scope", "benchmark-alpha");
    revision.metadata.set("seed", std::to_string(kSeed));
    revision.metadata.set("fixture_shape", "balanced_binary_preorder_v1");
    const auto append = [&](const auto& self, std::size_t count,
                            std::optional<ContentBlockId> parent) -> void {
        if(count == 0) return;
        const auto index = revision.blocks.size();
        const ContentBlockId id{"b-" + std::to_string(index)};
        const bool branch = count > 1;
        const auto text = branch ? "Section " + std::to_string(index) :
            "seed=" + std::to_string(kSeed) + ";block=" + std::to_string(index) + ";" +
            std::string(192, static_cast<char>('a' + index % 26));
        revision.blocks.push_back({id, 1, branch ? ContentBlockKind::Heading :
            ContentBlockKind::Paragraph, std::move(parent), text});
        if(branch) {
            const auto left = count / 2;
            self(self, left, id);
            self(self, count - 1 - left, id);
        }
    };
    append(append, scale, std::nullopt);
    return revision;
}

template<typename Store, typename Options>
std::vector<Sample> run_backend(const std::string& backend, std::size_t scale,
                               std::size_t repeat, const std::filesystem::path& root) {
    const auto path = root / (backend + "-" + std::to_string(scale) + "-" +
                              std::to_string(repeat) + ".mdbx");
    if(std::filesystem::exists(path)) throw std::runtime_error("fixture path already exists");
    std::cerr << "start " << backend << " scale=" << scale << " repeat=" << repeat << '\n';
    MdbxCanonicalStorageMetrics counters;
    Options options;
    options.path = path.string();
    options.table_prefix = "comparison";
    options.metrics = &counters;
    auto store = std::make_unique<Store>(options);
    const auto initial = make_revision(scale);
    if(!store->create_document(initial)) throw std::runtime_error("fixture create failed");
    auto selected_index = scale / 2;
    while(initial.blocks[selected_index].kind != ContentBlockKind::Paragraph)
        selected_index = (selected_index + 1) % scale;
    const auto selected = initial.blocks[selected_index].id;
    std::uint64_t head = 0;
    std::vector<Sample> samples;
    InMemoryCanonicalContentStore reference;
    if(!reference.create_document(initial)) throw std::runtime_error("reference create failed");
    const auto expected_markdown = reference.materialize_markdown(initial.document_id, 0);
    for(const auto& workload : kWorkloads) {
        counters = {};
        Sample sample;
        sample.backend = backend;
        sample.scale = scale;
        sample.repeat = repeat;
        sample.workload = workload;
        const auto cpu_begin = std::clock();
        const auto begin = Clock::now();
        if(workload == "point_block") {
            const auto block = store->read_block(initial.document_id, selected, 0);
            if(!block || block->text != initial.blocks[selected_index].text)
                throw std::runtime_error("point read parity failed");
            sample.result_blocks = 1;
            sample.result_text_bytes = block->text.size();
        } else if(workload == "outline") {
            const auto outline = store->read_outline(initial.document_id, 0, scale);
            if(!outline || outline->entries.size() != scale)
                throw std::runtime_error("outline parity failed");
            sample.result_blocks = outline->entries.size();
        } else if(workload == "bounded_subtree") {
            const auto result = store->read_subtree(initial.document_id, 0, {selected, 1, 4096, true});
            if(!result.succeeded() || result.blocks.size() != 1 ||
               result.blocks.front().text != initial.blocks[selected_index].text)
                throw std::runtime_error("subtree parity failed");
            sample.result_blocks = result.blocks.size();
            sample.result_text_bytes = result.blocks.front().text.size();
        } else if(workload == "single_block_edit" || workload == "repeated_edits") {
            const auto count = workload == "single_block_edit" ? 1U : 3U;
            for(unsigned step = 0; step < count; ++step) {
                const auto result = store->commit({initial.document_id, head,
                    {ReplaceBlockText{selected, "edited-" + std::to_string(head + 1)}}});
                if(result.status != CanonicalEditStatus::Ok || !result.revision ||
                   result.revision->revision != head + 1)
                    throw std::runtime_error("repeated edit parity failed");
                ++head;
                sample.result_blocks = result.revision->blocks.size();
                sample.result_text_bytes += result.revision->blocks[selected_index].text.size();
            }
            if(backend == "flat" && counters.block_versions_written != count)
                throw std::runtime_error("small edit rewrote unchanged block versions");
        } else if(workload == "full_materialization") {
            const auto markdown = store->materialize_markdown(initial.document_id, 0);
            if(!markdown || markdown != expected_markdown)
                throw std::runtime_error("historical materialization parity failed");
            sample.result_blocks = scale;
            sample.result_text_bytes = markdown->size();
        } else if(workload == "reopen") {
            store.reset();
            store = std::make_unique<Store>(options);
            const auto current = store->read_current(initial.document_id);
            const auto historical = store->read_revision(initial.document_id, 0);
            if(!current || current->revision != head || !historical ||
               historical->blocks[selected_index].text != initial.blocks[selected_index].text)
                throw std::runtime_error("reopen history parity failed");
            sample.result_blocks = current->blocks.size();
        }
        sample.elapsed_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
        sample.cpu_ms = 1000.0 * static_cast<double>(std::clock() - cpu_begin) / CLOCKS_PER_SEC;
        if(!std::isfinite(sample.elapsed_ms) || sample.elapsed_ms <= 0 || sample.cpu_ms < 0)
            throw std::runtime_error("invalid timing observation");
        sample.metrics = counters;
        sample.environment_bytes = std::filesystem::file_size(path);
        samples.push_back(sample);
    }
    return samples;
}

std::vector<double> values(const Sample& s) {
    const auto& m = s.metrics;
    return {s.elapsed_ms, s.cpu_ms, double(s.environment_bytes), double(s.result_blocks),
        double(s.result_text_bytes), double(m.value_reads), double(m.value_writes),
        double(m.value_bytes_read), double(m.value_bytes_written), double(m.block_versions_written),
        double(m.dictionary_lookups), double(m.encode_ns), double(m.decode_ns), double(m.dictionary_ns)};
}

double quantile(std::vector<double> samples, double q) {
    std::sort(samples.begin(), samples.end());
    const auto position = q * double(samples.size() - 1);
    const auto lower = static_cast<std::size_t>(position);
    const auto upper = std::min(lower + 1, samples.size() - 1);
    return samples[lower] + (samples[upper] - samples[lower]) * (position - double(lower));
}

void write_file(const std::filesystem::path& path, const std::string& bytes) {
    if(path.has_parent_path()) std::filesystem::create_directories(path.parent_path());
    std::ofstream stream(path, std::ios::binary);
    stream << bytes;
    if(!stream) throw std::runtime_error("cannot write benchmark output");
}

std::string raw_observations(const std::vector<Sample>& samples) {
    std::ostringstream raw;
    raw << "backend\tscale\trepeat\tworkload";
    for(const auto& metric : kMetrics) raw << '\t' << metric;
    raw << '\n' << std::setprecision(17);
    for(const auto& sample : samples) {
        raw << sample.backend << '\t' << sample.scale << '\t' << sample.repeat << '\t' << sample.workload;
        for(const auto value : values(sample)) raw << '\t' << value;
        raw << '\n';
    }
    return raw.str();
}

void run(const std::filesystem::path& raw_path, const std::filesystem::path& receipt_path,
         const std::vector<std::size_t>& scales, std::size_t repeats) {
    if(repeats < 2 || repeats > 100 || scales.empty())
        throw std::invalid_argument("benchmark requires 2..100 repeats and nonempty scales");
    const auto root = std::filesystem::temp_directory_path() /
        ("agent-memory-canonical-comparison-" + std::to_string(Clock::now().time_since_epoch().count()));
    if(!std::filesystem::create_directory(root)) throw std::runtime_error("cannot create fixture root");
    std::vector<Sample> all;
    for(const auto scale : scales) {
        if(scale < 3 || scale > 16384) throw std::invalid_argument("scale is outside fixture bounds");
        // One unrecorded warmup per backend/scale, then alternating pair order.
        for(std::size_t repeat = 0; repeat <= repeats; ++repeat) {
            for(unsigned position = 0; position < 2; ++position) {
                const bool flat = (repeat + position) % 2 == 0;
                auto samples = flat
                    ? run_backend<MdbxCanonicalFlatStorage, MdbxCanonicalFlatStorageOptions>(
                          "flat", scale, repeat, root)
                    : run_backend<MdbxCanonicalContentStore, MdbxCanonicalContentStoreOptions>(
                          "c1", scale, repeat, root);
                if(repeat != 0) for(auto& sample : samples) {
                    sample.repeat = repeat - 1;
                    all.push_back(std::move(sample));
                }
                // Retain completed observations if a later corpus hits the external timeout.
                // Only the complete matrix below can produce a completed receipt.
                if(repeat != 0) write_file(raw_path, raw_observations(all));
            }
        }
    }
    const auto raw = raw_observations(all);
    using Group = std::tuple<std::string, std::size_t, std::string>;
    std::map<Group, std::vector<std::vector<double>>> groups;
    for(const auto& sample : all) {
        const auto row = values(sample);
        groups[{sample.backend, sample.scale, sample.workload}].push_back(row);
    }
    Json aggregates = Json::array();
    for(const auto& group : groups) {
        Json item{{"backend", std::get<0>(group.first)}, {"scale", std::get<1>(group.first)},
                  {"workload", std::get<2>(group.first)}};
        for(std::size_t column = 0; column < kMetrics.size(); ++column) {
            std::vector<double> samples;
            for(const auto& row : group.second) samples.push_back(row[column]);
            item["p50"][kMetrics[column]] = quantile(samples, 0.5);
            item["p95"][kMetrics[column]] = quantile(samples, 0.95);
        }
        aggregates.push_back(std::move(item));
    }
    Json receipt{{"schema_version", 2}, {"family", kFamily}, {"execution_status", "completed"},
        {"decision", "evidence_only"}, {"seed", kSeed}, {"repeats", repeats}, {"scales", scales},
        {"fixture_shape", "balanced_binary_preorder_v1"},
        {"backends", {"c1", "flat"}}, {"workloads", kWorkloads}, {"warmup_pairs", 1},
        {"raw", {{"path", raw_path.generic_string()}, {"sha256", digest_hex(raw)}, {"rows", all.size()}}},
        {"metrics", kMetrics}, {"quantile", "linear_interpolation_n_minus_one"},
        {"source_revision", AGENT_MEMORY_CANONICAL_BENCH_REVISION},
        {"source_dirty", AGENT_MEMORY_CANONICAL_BENCH_DIRTY != 0},
        {"build", AGENT_MEMORY_CANONICAL_BENCH_BUILD},
        {"compiler", AGENT_MEMORY_CANONICAL_BENCH_COMPILER},
        {"system", AGENT_MEMORY_CANONICAL_BENCH_SYSTEM},
        {"hardware_threads", std::thread::hardware_concurrency()},
        {"metric_provenance", {{"value_counters", "adapter_table_operations_not_page_io"},
            {"dictionary", "mapping_calls_and_time_separate_from_canonical_values"},
            {"elapsed_ms", "steady_clock_wall_time"}, {"cpu_ms", "process_clock"},
            {"environment_bytes", "allocated_file_size_not_live_payload_bytes"},
            {"codec_payload_estimates", nullptr}, {"page_io", nullptr}, {"allocations", nullptr}}},
        {"limitations", {"synthetic_warm_workload", "not_a_production_activation_gate",
            "process_cpu_includes_other_threads", "reopen_is_not_cold_os_cache",
            "structure_reads_remain_linear_in_block_count"}}, {"aggregates", aggregates}};
    write_file(raw_path, raw);
    write_file(receipt_path, receipt.dump(2) + "\n");
    // This fresh directory is owned exclusively by the current invocation.
    std::filesystem::remove_all(root);
    std::cout << "comparison completed: " << all.size() << " raw observations\n";
}
} // namespace

int main(int argc, char** argv) {
    try {
        std::filesystem::path raw = "mdbx-canonical-flat.raw.tsv";
        std::filesystem::path receipt = "mdbx-canonical-flat.receipt.json";
        std::size_t repeats = 9;
        bool self = false;
        for(int index = 1; index < argc; ++index) {
            const std::string argument = argv[index];
            if(argument == "--self-test") self = true;
            else if(argument == "--raw" && index + 1 < argc) raw = argv[++index];
            else if(argument == "--receipt" && index + 1 < argc) receipt = argv[++index];
            else if(argument == "--repeats" && index + 1 < argc) repeats = std::stoull(argv[++index]);
            else throw std::invalid_argument("unknown benchmark argument: " + argument);
        }
        run(raw, receipt, self ? std::vector<std::size_t>{8, 16} :
                                std::vector<std::size_t>{64, 1024, 16384}, self ? 2 : repeats);
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
