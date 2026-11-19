#include <agent_memory/domain/CanonicalNavigation.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.hpp>
#include <agent_memory/storage/CanonicalFlatStorage.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;
using storage::CanonicalFlatBlockRecord;
using storage::CanonicalFlatBlockRef;
using storage::CanonicalFlatStorageCodec;
using storage::CanonicalFlatStructureRecord;

constexpr std::uint64_t kSeed = 20261119U;
constexpr std::string_view kFamily = "mdbx_canonical_flat_benchmark_v1";
const std::vector<std::string> kWorkloads{
    "point_block", "bounded_subtree", "single_block_edit", "full_materialization"};
const std::vector<std::size_t> kScales{64U, 256U, 1024U};

struct Sample final {
    std::size_t scale = 0;
    std::size_t repeat = 0;
    std::string workload;
    double elapsed_ms = 0.0;
    std::size_t source_blocks = 0;
    std::size_t result_blocks = 0;
    std::size_t physical_bytes = 0;
    std::size_t result_text_bytes = 0;
};

struct Aggregate final {
    std::vector<double> elapsed_ms;
    std::size_t source_blocks = 0;
    std::size_t result_blocks = 0;
    std::size_t physical_bytes = 0;
    std::size_t result_text_bytes = 0;
};

struct Fixture final {
    std::filesystem::path path;
    std::unique_ptr<MdbxCanonicalFlatStorage> store;
    CanonicalDocumentRevision revision;
};

[[nodiscard]] std::string json_escape(std::string_view value) {
    std::string result;
    result.reserve(value.size() + 8U);
    for(const auto character : value) {
        switch(character) {
        case '\\': result += "\\\\"; break;
        case '"': result += "\\\""; break;
        case '\n': result += "\\n"; break;
        case '\r': result += "\\r"; break;
        case '\t': result += "\\t"; break;
        default: result.push_back(character); break;
        }
    }
    return result;
}

[[nodiscard]] std::string digest_hex(std::string_view value) {
    const auto digest = sha256(value);
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for(const auto byte : digest) output << std::setw(2) << static_cast<unsigned>(byte);
    return output.str();
}

[[nodiscard]] std::uint8_t flat_kind(ContentBlockKind kind) {
    switch(kind) {
    case ContentBlockKind::Heading: return 0U;
    case ContentBlockKind::Paragraph: return 1U;
    case ContentBlockKind::Code: return 2U;
    }
    throw std::invalid_argument("benchmark encountered an unknown block kind");
}

[[nodiscard]] CanonicalFlatBlockRecord flat_block(const ContentBlock& block) {
    CanonicalFlatBlockRecord result;
    result.id = block.id.value();
    result.revision = block.revision;
    result.kind = flat_kind(block.kind);
    if(block.parent_id) result.parent_id = block.parent_id->value();
    result.text = block.text;
    return result;
}

[[nodiscard]] CanonicalFlatBlockRef flat_ref(const ContentBlock& block) {
    CanonicalFlatBlockRef result;
    result.id = block.id.value();
    result.revision = block.revision;
    result.kind = flat_kind(block.kind);
    if(block.parent_id) result.parent_id = block.parent_id->value();
    return result;
}

[[nodiscard]] CanonicalFlatStructureRecord flat_structure(
    const CanonicalDocumentRevision& revision) {
    CanonicalFlatStructureRecord result;
    result.document_id = revision.document_id.value();
    result.document_revision = revision.revision;
    result.body_revision = revision.revision;
    for(const auto& item : revision.metadata.values()) {
        result.metadata.emplace_back(item.first, item.second);
    }
    result.blocks.reserve(revision.blocks.size());
    for(const auto& block : revision.blocks) result.blocks.push_back(flat_ref(block));
    return result;
}

struct PhysicalShape final {
    std::size_t structure_bytes = 0;
    std::vector<std::size_t> block_bytes;

    [[nodiscard]] std::size_t all_bytes() const noexcept {
        std::size_t result = structure_bytes;
        for(const auto value : block_bytes) result += value;
        return result;
    }
};

[[nodiscard]] PhysicalShape physical_shape(const CanonicalDocumentRevision& revision) {
    PhysicalShape result;
    result.structure_bytes = CanonicalFlatStorageCodec::encode_structure(
        flat_structure(revision)).size();
    result.block_bytes.reserve(revision.blocks.size());
    for(const auto& block : revision.blocks) {
        result.block_bytes.push_back(CanonicalFlatStorageCodec::encode_block(flat_block(block)).size());
    }
    return result;
}

[[nodiscard]] CanonicalDocumentRevision make_revision(std::size_t scale, std::uint64_t seed) {
    CanonicalDocumentRevision result;
    result.document_id = DocumentId{"flat-benchmark-" + std::to_string(scale)};
    result.revision = 0U;
    result.metadata.set("seed", std::to_string(seed));
    result.metadata.set("scale", std::to_string(scale));
    result.blocks.reserve(scale);
    result.blocks.push_back(ContentBlock{
        ContentBlockId{"root"}, 1U, ContentBlockKind::Heading, std::nullopt,
        "Deterministic benchmark root"});
    result.blocks.push_back(ContentBlock{
        ContentBlockId{"subtree"}, 1U, ContentBlockKind::Heading,
        std::optional<ContentBlockId>{ContentBlockId{"root"}}, "Bounded subtree root"});
    for(std::size_t index = 2U; index < scale; ++index) {
        result.blocks.push_back(ContentBlock{
            ContentBlockId{"block-" + std::to_string(index)}, 1U,
            index % 7U == 0U ? ContentBlockKind::Code : ContentBlockKind::Paragraph,
            std::optional<ContentBlockId>{ContentBlockId{"subtree"}},
            "seed=" + std::to_string(seed) + ";block=" + std::to_string(index)});
    }
    return result;
}

[[nodiscard]] std::filesystem::path fixture_path(
    std::size_t scale, std::size_t repeat, std::string_view workload, std::uint64_t seed) {
    return std::filesystem::temp_directory_path() /
        ("agent_memory_mdbx_flat_benchmark_" + std::to_string(seed) + "_" +
         std::to_string(scale) + "_" + std::to_string(repeat) + "_" +
         std::string{workload} + ".mdbx");
}

[[nodiscard]] Fixture make_fixture(
    std::size_t scale, std::size_t repeat, std::string_view workload, std::uint64_t seed) {
    Fixture result;
    result.path = fixture_path(scale, repeat, workload, seed);
    std::error_code error;
    std::filesystem::remove(result.path, error);
    MdbxCanonicalFlatStorageOptions options;
    options.path = result.path.string();
    options.table_prefix = "flat benchmark";
    result.revision = make_revision(scale, seed);
    result.store = std::make_unique<MdbxCanonicalFlatStorage>(options);
    if(!result.store->create_document(result.revision)) {
        throw std::runtime_error("benchmark fixture creation failed");
    }
    return result;
}

void dispose_fixture(Fixture& fixture) {
    fixture.store.reset();
    std::error_code error;
    std::filesystem::remove(fixture.path, error);
}

[[nodiscard]] Sample run_sample(
    std::size_t scale, std::size_t repeat, std::string_view workload, std::uint64_t seed) {
    auto fixture = make_fixture(scale, repeat, workload, seed);
    const auto shape = physical_shape(fixture.revision);
    const auto selected_index = scale / 2U;
    const auto selected_id = ContentBlockId{"block-" + std::to_string(selected_index)};
    const auto subtree_limit = std::min<std::size_t>(32U, scale);
    Sample result{scale, repeat, std::string{workload}};
    const auto begin = Clock::now();

    if(workload == "point_block") {
        const auto block = fixture.store->read_block(
            fixture.revision.document_id, selected_id, fixture.revision.revision);
        if(!block || block->id != selected_id) throw std::runtime_error("point read failed");
        result.source_blocks = 1U;
        result.result_blocks = 1U;
        result.physical_bytes = shape.structure_bytes + shape.block_bytes[selected_index];
        result.result_text_bytes = block->text.size();
    } else if(workload == "bounded_subtree") {
        const auto revision = fixture.store->read_revision(
            fixture.revision.document_id, fixture.revision.revision);
        if(!revision) throw std::runtime_error("revision read failed");
        const auto subtree = read_canonical_subtree(
            *revision,
            CanonicalSubtreeRequest{
                std::optional<ContentBlockId>{ContentBlockId{"subtree"}},
                subtree_limit, 4096U, true});
        if(!subtree.succeeded() || subtree.blocks.empty()) {
            throw std::runtime_error("bounded subtree read failed");
        }
        result.source_blocks = revision->blocks.size();
        result.result_blocks = subtree.blocks.size();
        result.physical_bytes = shape.all_bytes();
        for(const auto& block : subtree.blocks) result.result_text_bytes += block.text.size();
    } else if(workload == "single_block_edit") {
        CanonicalEditRequest request;
        request.document_id = fixture.revision.document_id;
        request.expected_revision = fixture.revision.revision;
        request.operations.push_back(ReplaceBlockText{
            selected_id, "edited-seed=" + std::to_string(seed) + ";scale=" + std::to_string(scale)});
        const auto edit = fixture.store->commit(request);
        if(edit.status != CanonicalEditStatus::Ok || !edit.revision) {
            throw std::runtime_error("single block edit failed");
        }
        const auto edited_shape = physical_shape(*edit.revision);
        result.source_blocks = fixture.revision.blocks.size();
        result.result_blocks = edit.revision->blocks.size();
        result.physical_bytes = edited_shape.all_bytes();
        result.result_text_bytes = edit.revision->blocks[selected_index].text.size();
    } else if(workload == "full_materialization") {
        const auto markdown = fixture.store->materialize_markdown(
            fixture.revision.document_id, fixture.revision.revision);
        if(!markdown) throw std::runtime_error("full materialization failed");
        result.source_blocks = fixture.revision.blocks.size();
        result.result_blocks = fixture.revision.blocks.size();
        result.physical_bytes = shape.all_bytes();
        result.result_text_bytes = markdown->size();
    } else {
        throw std::invalid_argument("unknown canonical flat benchmark workload");
    }

    result.elapsed_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
    if(!(result.elapsed_ms > 0.0) || !std::isfinite(result.elapsed_ms)) result.elapsed_ms = 0.000001;
    dispose_fixture(fixture);
    return result;
}

[[nodiscard]] std::string raw_text(
    const std::vector<Sample>& samples, std::uint64_t seed, std::size_t repeats) {
    std::ostringstream output;
    output << "# family=" << kFamily << "\tseed=" << seed << "\trepeats=" << repeats << '\n';
    output << "scale\trepeat\tworkload\telapsed_ms\tsource_blocks\tresult_blocks\tphysical_bytes\tresult_text_bytes\n";
    output << std::fixed << std::setprecision(6);
    for(const auto& sample : samples) {
        output << sample.scale << '\t' << sample.repeat << '\t' << sample.workload << '\t'
               << sample.elapsed_ms << '\t' << sample.source_blocks << '\t'
               << sample.result_blocks << '\t' << sample.physical_bytes << '\t'
               << sample.result_text_bytes << '\n';
    }
    return output.str();
}

[[nodiscard]] double median(std::vector<double> values) {
    if(values.empty()) throw std::runtime_error("cannot compute empty benchmark median");
    std::sort(values.begin(), values.end());
    const auto middle = values.size() / 2U;
    if(values.size() % 2U != 0U) return values[middle];
    return (values[middle - 1U] + values[middle]) / 2.0;
}

[[nodiscard]] std::string receipt_text(
    const std::vector<Sample>& samples, std::uint64_t seed, std::size_t repeats,
    const std::vector<std::size_t>& scales, std::string_view raw_path,
    std::string_view raw_sha256) {
    std::map<std::pair<std::size_t, std::string>, Aggregate> aggregate;
    for(const auto& sample : samples) {
        auto& value = aggregate[std::make_pair(sample.scale, sample.workload)];
        value.elapsed_ms.push_back(sample.elapsed_ms);
        value.source_blocks = sample.source_blocks;
        value.result_blocks = sample.result_blocks;
        value.physical_bytes = sample.physical_bytes;
        value.result_text_bytes = sample.result_text_bytes;
    }
    std::ostringstream output;
    output << "{\n  \"schema_version\": 1,\n  \"family\": \"" << kFamily
           << "\",\n  \"execution_status\": \"completed\",\n"
           << "  \"decision\": \"evidence_only\",\n  \"seed\": " << seed
           << ",\n  \"repeats\": " << repeats << ",\n  \"scales\": [";
    for(std::size_t index = 0; index < scales.size(); ++index) {
        if(index != 0U) output << ", ";
        output << scales[index];
    }
    output << "],\n"
           << "  \"workloads\": [\"point_block\", \"bounded_subtree\", "
           << "\"single_block_edit\", \"full_materialization\"],\n  \"raw\": {\n"
           << "    \"path\": \"" << json_escape(raw_path) << "\",\n    \"sha256\": \""
           << raw_sha256 << "\",\n    \"rows\": " << samples.size()
           << "\n  },\n  \"metric_provenance\": {\n"
           << "    \"elapsed_ms\": \"steady_clock_wall_time\",\n"
           << "    \"physical_bytes\": \"codec_payload_size_estimate\",\n"
           << "    \"cpu_time_ms\": null,\n    \"allocations\": null\n  },\n"
           << "  \"physical_read_model\": {\n"
           << "    \"point_block\": \"structure_plus_one_block\",\n"
           << "    \"bounded_subtree\": \"full_revision_read_then_reference_navigation\",\n"
           << "    \"single_block_edit\": \"full_revision_rewrite\",\n"
           << "    \"full_materialization\": \"full_revision_read\"\n  },\n"
           << "  \"p50\": [\n";
    bool first = true;
    output << std::fixed << std::setprecision(6);
    for(const auto scale : scales) {
        for(const auto& workload : kWorkloads) {
            const auto& value = aggregate.at(std::make_pair(scale, workload));
            if(!first) output << ",\n";
            first = false;
            output << "    {\"scale\": " << scale << ", \"workload\": \""
                   << workload << "\", \"elapsed_ms\": " << median(value.elapsed_ms)
                   << ", \"source_blocks\": " << value.source_blocks
                   << ", \"result_blocks\": " << value.result_blocks
                   << ", \"physical_bytes\": " << value.physical_bytes
                   << ", \"result_text_bytes\": " << value.result_text_bytes << "}";
        }
    }
    output << "\n  ]\n}\n";
    return output.str();
}

void write_file(const std::filesystem::path& path, std::string_view content) {
    if(path.has_parent_path()) std::filesystem::create_directories(path.parent_path());
    std::ofstream output(path, std::ios::binary);
    if(!output) throw std::runtime_error("cannot write benchmark output: " + path.string());
    output.write(content.data(), static_cast<std::streamsize>(content.size()));
    if(!output) throw std::runtime_error("cannot finish benchmark output: " + path.string());
}

void run(const std::filesystem::path& raw_path, const std::filesystem::path& receipt_path,
         const std::vector<std::size_t>& scales, std::size_t repeats, std::uint64_t seed) {
    if(repeats == 0U || scales.empty()) throw std::invalid_argument("benchmark matrix is empty");
    std::vector<Sample> samples;
    samples.reserve(scales.size() * repeats * kWorkloads.size());
    for(const auto scale : scales) {
        if(scale < 3U) throw std::invalid_argument("benchmark scale must be at least three blocks");
        for(std::size_t repeat = 0; repeat < repeats; ++repeat) {
            for(const auto& workload : kWorkloads) {
                samples.push_back(run_sample(scale, repeat, workload, seed));
            }
        }
    }
    const auto raw = raw_text(samples, seed, repeats);
    const auto receipt = receipt_text(
        samples, seed, repeats, scales, raw_path.generic_string(), digest_hex(raw));
    write_file(raw_path, raw);
    write_file(receipt_path, receipt);
    std::cout << "mdbx canonical flat benchmark: " << samples.size()
              << " rows, raw=" << raw_path << ", receipt=" << receipt_path << '\n';
}

void self_test() {
    const auto root = std::filesystem::temp_directory_path() /
        "agent_memory_mdbx_canonical_flat_benchmark_self_test";
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::filesystem::create_directories(root);
    const auto raw = root / "raw.tsv";
    const auto receipt = root / "receipt.json";
    run(raw, receipt, {8U, 16U}, 2U, kSeed);
    std::ifstream input(raw, std::ios::binary);
    if(!input || input.peek() == std::ifstream::traits_type::eof()) {
        throw std::runtime_error("benchmark self-test did not write raw observations");
    }
    std::filesystem::remove_all(root, error);
    std::cout << "mdbx canonical flat benchmark: PASS\n";
}

} // namespace

int main(int argc, char** argv) {
    try {
        bool self = false;
        std::filesystem::path raw = "mdbx-canonical-flat.raw.tsv";
        std::filesystem::path receipt = "mdbx-canonical-flat.receipt.json";
        std::size_t repeats = 5U;
        for(int index = 1; index < argc; ++index) {
            const std::string argument = argv[index];
            if(argument == "--self-test") self = true;
            else if(argument == "--raw" && index + 1 < argc) raw = argv[++index];
            else if(argument == "--receipt" && index + 1 < argc) receipt = argv[++index];
            else if(argument == "--repeats" && index + 1 < argc) repeats = std::stoull(argv[++index]);
            else throw std::invalid_argument("unknown or incomplete benchmark argument: " + argument);
        }
        if(self) self_test();
        else run(raw, receipt, kScales, repeats, kSeed);
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}

#else

int main() { return 0; }

#endif
