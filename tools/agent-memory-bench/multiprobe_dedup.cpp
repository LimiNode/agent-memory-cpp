#include <agent_memory/index/MultiProbeHammingIndex.hpp>
#include "DenseResearchMultiProbe.hpp"
#include "SparseResearchMultiProbe.hpp"
#include "CompactResearchMultiProbe.hpp"
#include "MultiProbeResearchSupport.hpp"

#include <nlohmann/json.hpp>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <new>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

// This entire allocation hook is absent from the uninstrumented executable.
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
namespace allocation_diagnostic {
struct Entry final { void* pointer; std::size_t bytes; };
constexpr std::size_t kTrackingCapacity = 1U << 20;
struct Session final {
    bool enabled = false;
    std::size_t calls = 0, requested = 0, live = 0, peak = 0;
    std::size_t fail_after = std::numeric_limits<std::size_t>::max();
    Entry* entries = nullptr;
};
inline thread_local Session session;
std::size_t slot(void* pointer) noexcept {
    return (reinterpret_cast<std::uintptr_t>(pointer) >> 4U) * UINT64_C(0x9e3779b97f4a7c15) & (kTrackingCapacity - 1);
}
void start(std::size_t failure = std::numeric_limits<std::size_t>::max()) {
    std::free(session.entries);
    session = {};
    session.entries = static_cast<Entry*>(std::calloc(kTrackingCapacity, sizeof(Entry)));
    if(!session.entries) throw std::bad_alloc();
    session.fail_after = failure;
    session.enabled = true;
}
void stop() noexcept {
    session.enabled = false;
    std::free(session.entries);
    session.entries = nullptr;
}
}
void* operator new(std::size_t bytes) {
    using namespace allocation_diagnostic;
    if(session.enabled && session.calls == session.fail_after) throw std::bad_alloc();
    auto* pointer = std::malloc(bytes == 0 ? 1 : bytes);
    if(!pointer) throw std::bad_alloc();
    if(session.enabled) {
        auto index = slot(pointer);
        std::size_t traversed = 0;
        while(session.entries[index].pointer && session.entries[index].pointer != reinterpret_cast<void*>(std::uintptr_t{1})) {
            index = (index + 1) & (kTrackingCapacity - 1);
            if(++traversed == kTrackingCapacity) { std::free(pointer); throw std::bad_alloc(); }
        }
        session.entries[index] = {pointer, bytes};
        ++session.calls;
        session.requested += bytes;
        session.live += bytes;
        session.peak = std::max(session.peak, session.live);
    }
    return pointer;
}
void operator delete(void* pointer) noexcept {
    if(!pointer) return;
    using namespace allocation_diagnostic;
    if(session.enabled) {
        auto index = slot(pointer);
        std::size_t traversed = 0;
        while(session.entries[index].pointer) {
            if(session.entries[index].pointer == pointer) {
                session.live -= session.entries[index].bytes;
                session.entries[index].pointer = reinterpret_cast<void*>(std::uintptr_t{1});
                break;
            }
            index = (index + 1) & (kTrackingCapacity - 1);
            if(++traversed == kTrackingCapacity) break;
        }
    }
    std::free(pointer);
}
void* operator new[](std::size_t bytes) { return ::operator new(bytes); }
void operator delete[](void* pointer) noexcept { ::operator delete(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { ::operator delete(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { ::operator delete(pointer); }
#endif

namespace {
using namespace agent_memory;
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
using Index = std::unique_ptr<IBinarySignatureIndex>;
constexpr std::array<const char*, 4> kArms{{"production", "dense_copy", "sparse_set", "compact_set"}};
constexpr std::array<const char*, 3> kWorkloads{{"sparse_uniform", "broad_uniform", "projected_skew"}};

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
double milliseconds(Clock::time_point first, Clock::time_point last) {
    return std::chrono::duration<double, std::milli>(last - first).count();
}
BinarySignatureInfo identity(std::size_t width) {
    BinarySignatureInfo info;
    info.encoder_id = "r6_multiprobe_synthetic";
    info.encoder_version = "v1";
    info.encoder_config_fingerprint = "r6_multiprobe:" + std::to_string(width);
    info.source_model_id = "synthetic:binary";
    info.projection_kind = "symmetric";
    info.source_dimension = width;
    info.bit_count = width;
    info.source_similarity_metric = SimilarityMetric::Cosine;
    info.source_normalized = true;
    info.seed = 20261122;
    return info;
}
Index make_index(std::size_t arm, const MultiProbeHammingIndexOptions& options) {
    switch(arm) {
        case 0: return std::make_unique<MultiProbeHammingIndex>(options);
        case 1: return std::make_unique<DenseResearchMultiProbe>(options);
        case 2: return std::make_unique<SparseResearchMultiProbe>(options);
        case 3: return std::make_unique<CompactResearchMultiProbe>(options);
    }
    throw std::invalid_argument("unknown research arm");
}
MultiProbeHammingSearchResult inspect(const Index& index, std::size_t arm,
                                     const BinarySignatureSearchQuery& query) {
    switch(arm) {
        case 0: return static_cast<MultiProbeHammingIndex&>(*index).search_with_diagnostics(query);
        case 1: return static_cast<DenseResearchMultiProbe&>(*index).search_with_diagnostics(query);
        case 2: return static_cast<SparseResearchMultiProbe&>(*index).search_with_diagnostics(query);
        case 3: return static_cast<CompactResearchMultiProbe&>(*index).search_with_diagnostics(query);
    }
    throw std::invalid_argument("unknown diagnostic arm");
}
void inject(Index& index, std::size_t arm, MultiProbeHammingIndexFaultPoint point) {
    switch(arm) {
        case 0: static_cast<MultiProbeHammingIndex&>(*index).set_failure_injection_for_testing(point); break;
        case 1: static_cast<DenseResearchMultiProbe&>(*index).set_failure_injection_for_testing(point); break;
        case 2: static_cast<SparseResearchMultiProbe&>(*index).set_failure_injection_for_testing(point); break;
        case 3: static_cast<CompactResearchMultiProbe&>(*index).set_failure_injection_for_testing(point); break;
        default: throw std::invalid_argument("unknown fault arm");
    }
}
Json hits(const std::vector<BinarySignatureSearchResult>& results) {
    Json value = Json::array();
    for(const auto& result : results)
        value.push_back({{"id", result.chunk_id.value()}, {"distance", result.hamming_distance},
                         {"metadata", result.metadata.values()}});
    return value;
}
Json evidence(const MultiProbeHammingSearchResult& result) {
    return {{"results", hits(result.results)}, {"candidates", result.candidate_count},
            {"buckets", result.probed_bucket_count}, {"postings", result.visited_posting_count}};
}
BinarySignatureRecord record(std::string id, std::size_t width,
                             std::vector<std::uint64_t> words, std::string cohort) {
    Metadata metadata;
    metadata.set("cohort", std::move(cohort));
    return {ChunkId{std::move(id)}, BinarySignature(width, std::move(words)), identity(width), std::move(metadata)};
}

#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
using Trace = multiprobe_research::Trace;
bool same_trace(const Trace& first, const Trace& second) {
    return first.buckets == second.buckets && first.postings == second.postings &&
           first.first_seen == second.first_seen && first.accepted == second.accepted &&
           first.distances == second.distances && first.radii == second.radii;
}
// A non-cryptographic diagnostic checksum; retained files use SHA-256 separately.
Json trace_evidence(const Trace& trace) {
    std::uint64_t state = UINT64_C(14695981039346656037);
    const auto word = [&](std::uint64_t value) {
        for(unsigned byte = 0; byte < 8; ++byte) {
            state ^= static_cast<unsigned char>(value >> (byte * 8));
            state *= UINT64_C(1099511628211);
        }
    };
    word(1); word(trace.buckets.size());
    for(const auto& item : trace.buckets) { word(item.first); word(item.second); }
    word(2); word(trace.postings.size()); for(const auto item : trace.postings) word(item);
    word(3); word(trace.first_seen.size()); for(const auto item : trace.first_seen) word(item);
    word(4); word(trace.accepted.size()); for(const auto item : trace.accepted) word(item);
    word(5); word(trace.distances.size());
    for(const auto& item : trace.distances) { word(item.first); word(item.second); }
    word(6); word(trace.radii.size());
    for(const auto& item : trace.radii) { word(item.first); word(item.second); }
    std::ostringstream digest;
    digest << std::hex << std::setw(16) << std::setfill('0') << state;
    return {{"fnv1a64", digest.str()}, {"first_seen", trace.first_seen.size()},
            {"accepted", trace.accepted.size()}, {"buckets", trace.buckets.size()},
            {"postings", trace.postings.size()}, {"distance_evaluations", trace.distances.size()},
            {"completed_radii", trace.radii.size()}, {"visited_capacity", trace.visited_capacity},
            {"visited_payload_lower_bound", trace.visited_payload_lower_bound}};
}
Trace traced(const Index& index, std::size_t arm, const BinarySignatureSearchQuery& query,
             const Json& expected) {
    Trace trace;
    multiprobe_research::active_trace = &trace;
    try {
        const auto result = inspect(index, arm, query);
        multiprobe_research::active_trace = nullptr;
        require(evidence(result) == expected, "traced results/counters differ from production");
    } catch(...) {
        multiprobe_research::active_trace = nullptr;
        throw;
    }
    return trace;
}
Json memory(const Index& index, const BinarySignatureSearchQuery& query, const Json& expected) {
    allocation_diagnostic::start();
    try {
        const auto result = index->search(query);
        allocation_diagnostic::stop();
        const auto snapshot = allocation_diagnostic::session;
        require(hits(result) == expected.at("results"), "allocation diagnostic result differs");
        return {{"ordinary_allocations", snapshot.calls}, {"ordinary_requested_bytes", snapshot.requested},
                {"ordinary_peak_live_bytes", snapshot.peak}, {"ordinary_retained_bytes", snapshot.live}};
    } catch(...) {
        allocation_diagnostic::stop();
        throw;
    }
}
#endif

std::size_t verify(std::array<Index, 4>& indexes, const BinarySignatureSearchQuery& query) {
    const auto expected = evidence(inspect(indexes[0], 0, query));
    for(std::size_t arm = 0; arm < 4; ++arm) {
        require(evidence(inspect(indexes[arm], arm, query)) == expected, "full production evidence differs");
        require(hits(indexes[arm]->search(query)) == expected.at("results"), "search/full diagnostic drift");
    }
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
    const auto baseline = traced(indexes[1], 1, query, expected);
    for(std::size_t arm = 2; arm < 4; ++arm)
        require(same_trace(baseline, traced(indexes[arm], arm, query, expected)), "discovered identity/order trace differs");
#endif
    return 4;
}

Json self_test() {
    std::size_t controls = 0;
    MultiProbeHammingIndexOptions options;
    options.signature_info = identity(4);
    options.table_count = 2; options.bits_per_table = 1; options.max_probe_radius = 1;
    options.candidate_multiplier = 1; options.minimum_candidate_count = 1;
    std::array<Index, 4> indexes;
    for(std::size_t arm = 0; arm < 4; ++arm) indexes[arm] = make_index(arm, options);
    BinarySignatureSearchQuery query{BinarySignature(4, {0}), identity(4), 2, {}};
    controls += verify(indexes, query);
    const std::array<std::uint64_t, 6> words{{0, 1, 4, 5, 15, 10}};
    const std::array<const char*, 6> ids{{"z", "a", "b", "c", "d", "e"}};
    for(std::size_t position = 0; position < words.size(); ++position)
        for(auto& index : indexes)
            index->upsert(record(ids[position], 4, {words[position]}, position == 3 || position == 4 ? "1" : "0"));
    controls += verify(indexes, query);
    auto golden = inspect(indexes[0], 0, query);
    require(golden.candidate_count == 4 && golden.probed_bucket_count == 2 && golden.visited_posting_count == 6,
            "literal radius-zero oracle counters");
    require(golden.results.size() == 2 && golden.results[0].chunk_id.value() == "z" &&
            golden.results[0].hamming_distance == 0 && golden.results[1].chunk_id.value() == "a" &&
            golden.results[1].hamming_distance == 1, "literal radius-zero/tie oracle");
    query.metadata_filters = {{"cohort", "1"}};
    controls += verify(indexes, query);
    golden = inspect(indexes[0], 0, query);
    require(golden.candidate_count == 2 && golden.probed_bucket_count == 4 && golden.visited_posting_count == 12,
            "literal selective radius-one oracle counters");
    require(golden.results.size() == 2 && golden.results[0].chunk_id.value() == "c" &&
            golden.results[0].hamming_distance == 2 && golden.results[1].chunk_id.value() == "d" &&
            golden.results[1].hamming_distance == 4, "literal selective golden results");
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
    const auto trace = traced(indexes[1], 1, query, evidence(golden));
    require(trace.first_seen == std::vector<std::size_t>({0, 2, 5, 1, 3, 4}) &&
            trace.accepted == std::vector<std::size_t>({3, 4}), "literal discovered identity golden");
#endif
    query.metadata_filters = {{"cohort", "missing"}};
    controls += verify(indexes, query);
    query.metadata_filters.clear();
    query.limit = std::numeric_limits<std::size_t>::max();
    controls += verify(indexes, query);
    query.limit = 0;
    query.signature_info = {};
    controls += verify(indexes, query);
    query.limit = 2;
    for(auto& index : indexes) {
        bool rejected = false;
        try { (void)index->search(query); } catch(const std::invalid_argument&) { rejected = true; }
        require(rejected, "invalid nonzero identity accepted"); ++controls;
    }
    query.signature_info = identity(4);
    for(const auto word : {std::uint64_t{0}, std::uint64_t{15}}) {
        for(auto& index : indexes) index->upsert(record("a", 4, {word}, "replacement"));
        controls += verify(indexes, query);
    }
    for(auto& index : indexes) require(index->erase(ChunkId{"b"}), "erase existing record failed");
    controls += verify(indexes, query);
    for(auto& index : indexes) index->upsert(record("new", 4, {1}, "new"));
    controls += verify(indexes, query);
    for(const auto fault : {MultiProbeHammingIndexFaultPoint::AfterRecordAppendBeforeSignatureStorage,
                           MultiProbeHammingIndexFaultPoint::AfterSignatureStorageBeforeTables,
                           MultiProbeHammingIndexFaultPoint::AfterFirstTableInsert}) {
        for(std::size_t arm = 0; arm < 4; ++arm) {
            const auto before = hits(indexes[arm]->search(query));
            inject(indexes[arm], arm, fault);
            bool rejected = false;
            try { indexes[arm]->upsert(record("failure", 4, {7}, "failure")); }
            catch(const std::runtime_error&) { rejected = true; }
            require(rejected && !indexes[arm]->find(ChunkId{"failure"}) &&
                    hits(indexes[arm]->search(query)) == before, "insertion rollback lost state");
            ++controls;
        }
    }
    for(auto& index : indexes) { index->clear(); index->upsert(record("rebuilt", 4, {2}, "rebuilt")); }
    controls += verify(indexes, query);
    std::atomic<bool> readers_ok{true};
    const auto expected = hits(indexes[0]->search(query));
    for(std::size_t arm = 0; arm < 4; ++arm) {
        std::array<std::thread, 4> readers;
        for(auto& reader : readers) reader = std::thread([&, arm] {
                try {
                    for(unsigned repeat = 0; repeat < 16; ++repeat)
                        if(hits(indexes[arm]->search(query)) != expected) readers_ok = false;
                } catch(...) { readers_ok = false; }
            });
        for(auto& reader : readers) reader.join();
    }
    require(readers_ok, "independent const readers differ"); ++controls;
    options.bits_per_table = 2; options.max_probe_radius = 2;
    for(std::size_t arm = 0; arm < 4; ++arm) {
        indexes[arm] = make_index(arm, options);
        indexes[arm]->upsert(record("radius-two", 4, {0}, "0"));
    }
    query = {BinarySignature(4, {15}), identity(4), 1, {}};
    controls += verify(indexes, query);
    golden = inspect(indexes[0], 0, query);
    require(golden.probed_bucket_count == 8 && golden.visited_posting_count == 2 &&
            golden.candidate_count == 1 && golden.results.front().hamming_distance == 4,
            "literal radius-two oracle"); ++controls;
    for(auto& index : indexes) {
        auto wrong = query;
        wrong.signature = BinarySignature(8, {15});
        bool invalid = false;
        try { (void)index->search(wrong); } catch(const std::invalid_argument&) { invalid = true; }
        require(invalid, "invalid query width accepted"); ++controls;
    }
    multiprobe_research::CompactVisited compact;
    for(std::size_t position = 0; position < 8; ++position) require(compact.insert(position), "compact first insert");
    const auto capacity = compact.capacity();
    require(!compact.insert(0) && compact.capacity() == capacity, "duplicate at growth threshold grew");
    for(std::size_t position = 8; position < 4096; ++position) require(compact.insert(position), "compact growth/collision");
    for(std::size_t position = 0; position < 4096; ++position) require(!compact.insert(position), "compact repeated position");
    bool rejected = false;
    try { (void)compact.insert(std::numeric_limits<std::size_t>::max()); }
    catch(const std::invalid_argument&) { rejected = true; }
    require(rejected, "compact reserved sentinel accepted"); controls += 3;
    for(const auto width : {128U, 256U}) for(const auto radius : {0U, 1U, 2U}) {
        MultiProbeHammingIndexOptions wide_options;
        wide_options.signature_info = identity(width);
        wide_options.table_count = 8; wide_options.bits_per_table = 12;
        wide_options.max_probe_radius = radius;
        wide_options.candidate_multiplier = 1; wide_options.minimum_candidate_count = 64;
        std::array<Index, 4> wide_indexes;
        for(std::size_t arm = 0; arm < 4; ++arm) wide_indexes[arm] = make_index(arm, wide_options);
        std::mt19937_64 random(20261122);
        for(std::size_t position = 0; position < 64; ++position) {
            std::vector<std::uint64_t> signature(width / 64);
            for(auto& word : signature) word = position < 32 ? 0 : random();
            const auto value = record("wide:" + std::to_string(position), width, signature, std::to_string(position % 2));
            for(auto& index : wide_indexes) index->upsert(value);
        }
        BinarySignatureSearchQuery wide_query{BinarySignature(width, std::vector<std::uint64_t>(width / 64, 0)),
                                              identity(width), 10, {}};
        controls += verify(wide_indexes, wide_query);
        wide_query.metadata_filters = {{"cohort", "0"}};
        controls += verify(wide_indexes, wide_query);
    }
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
    multiprobe_research::CompactVisited failed;
    allocation_diagnostic::start(0);
    rejected = false;
    try { (void)failed.insert(0); } catch(const std::bad_alloc&) { rejected = true; }
    allocation_diagnostic::stop();
    require(rejected && failed.size() == 0 && failed.capacity() == 0, "failed compact initial allocation"); ++controls;
    for(std::size_t position = 0; position < 8; ++position) (void)failed.insert(position);
    allocation_diagnostic::start(0);
    rejected = false;
    try { (void)failed.insert(8); } catch(const std::bad_alloc&) { rejected = true; }
    allocation_diagnostic::stop();
    require(rejected && failed.size() == 8 && failed.capacity() == 16 && failed.insert(8),
            "failed compact rehash was not atomic"); ++controls;
    for(std::size_t arm = 0; arm < 4; ++arm) {
        auto allocation_options = options;
        allocation_options.signature_info = identity(4);
        auto index = make_index(arm, allocation_options);
        for(std::size_t position = 0; position < 128; ++position)
            index->upsert(record("alloc:" + std::to_string(position), 4, {0}, "0"));
        const auto expected_result = evidence(inspect(index, arm, query));
        const auto allocations = memory(index, query, expected_result).at("ordinary_allocations").get<std::size_t>();
        require(allocations > 0, "missing allocation observations");
        for(std::size_t fail = 0; fail < allocations; ++fail) {
            allocation_diagnostic::start(fail);
            rejected = false;
            try { (void)index->search(query); } catch(const std::bad_alloc&) { rejected = true; }
            allocation_diagnostic::stop();
            require(rejected && hits(index->search(query)) == expected_result.at("results"),
                    "query allocation failure returned partial result or changed index"); ++controls;
        }
    }
#endif
    return {{"status", "PASS"}, {"native_control_cases", controls},
            {"literal_golden_cases", 3}, {"compact_positions", 4096}, {"independent_readers", 4},
            {"reopen", "NOT_APPLICABLE"}, {"quality", "NOT_EXECUTED"},
            {"compiler", {{"id", AGENT_MEMORY_MP_COMPILER_ID}, {"version", AGENT_MEMORY_MP_COMPILER_VERSION}}}};
}

struct Profile final {
    std::size_t id, width, count, workload;
    bool filtered;
    MultiProbeHammingIndexOptions options() const {
        MultiProbeHammingIndexOptions result;
        result.signature_info = identity(width);
        result.table_count = 8;
        result.bits_per_table = workload == 1 ? 4 : 12;
        result.max_probe_radius = workload == 0 ? 0 : 1;
        result.candidate_multiplier = 64;
        result.minimum_candidate_count = 128;
        return result;
    }
};
std::string document_id(std::size_t position) {
    std::ostringstream text;
    text << "mp:" << std::setfill('0') << std::setw(8) << position;
    return text.str();
}
void clear_projection(std::vector<std::uint64_t>& words, const Profile& profile) {
    const auto options = profile.options();
    const auto count = options.table_count * options.bits_per_table;
    for(std::size_t projection = 0; projection < count; ++projection) {
        const auto bit = projection * profile.width / count;
        words[bit / 64] &= ~(UINT64_C(1) << (bit % 64));
    }
}
void put_word(std::ostream& output, std::uint64_t value) {
    for(unsigned byte = 0; byte < 8; ++byte) output.put(static_cast<char>(value >> (byte * 8)));
}
void put_string(std::ostream& output, const std::string& text) {
    put_word(output, text.size()); output.write(text.data(), static_cast<std::streamsize>(text.size()));
}
struct Fixture final {
    std::vector<BinarySignatureRecord> records;
    std::vector<BinarySignatureSearchQuery> queries;
};
Fixture fixture(const Profile& profile, const std::filesystem::path& path) {
    const auto options = profile.options();
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if(!output) throw std::runtime_error("cannot retain synthetic fixture");
    output.write("MPDEDUP1", 8);
    for(const auto value : {profile.width, profile.count, profile.workload, std::size_t(profile.filtered),
             options.table_count, options.bits_per_table, options.max_probe_radius, std::size_t{10},
             options.candidate_multiplier, options.minimum_candidate_count, std::size_t{8}}) put_word(output, value);
    Fixture data;
    data.records.reserve(profile.count);
    std::mt19937_64 corpus(20261122), queries(20261123);
    for(std::size_t position = 0; position < profile.count; ++position) {
        std::vector<std::uint64_t> words(profile.width / 64);
        for(auto& word : words) word = corpus();
        if(profile.workload == 2 && position % 8 != 7) clear_projection(words, profile);
        const auto id = document_id(position), cohort = std::to_string(position % 16);
        for(const auto word : words) put_word(output, word);
        put_string(output, id); put_string(output, "cohort"); put_string(output, cohort);
        data.records.push_back(record(id, profile.width, std::move(words), cohort));
    }
    for(std::size_t query = 0; query < 8; ++query) {
        std::vector<std::uint64_t> words(profile.width / 64);
        for(auto& word : words) word = queries();
        if(profile.workload == 2) clear_projection(words, profile);
        for(const auto word : words) put_word(output, word);
        data.queries.push_back({BinarySignature(profile.width, std::move(words)), identity(profile.width),
                               10, profile.filtered ? std::vector<MetadataFilter>{{"cohort", "0"}} : std::vector<MetadataFilter>{}});
    }
    require(bool(output), "synthetic fixture write failed");
    return data;
}
Json configuration() {
    return {{"widths", {128, 256}}, {"counts", {4096, 262144}}, {"workloads", kWorkloads},
            {"filters", {false, true}}, {"queries", 8}, {"limit", 10}, {"table_count", 8},
            {"candidate_multiplier", 64}, {"minimum_candidate_count", 128},
            {"corpus_seed", 20261122}, {"query_seed", 20261123}, {"order_seed", 20261124},
            {"rng_reset", "per_width_per_profile"}, {"skew_unmodified_ordinal_mod8", 7},
            {"warmups", 1}, {"repeats", 5}, {"arms", kArms}, {"quantile", "nearest_rank"},
            {"order_policy", "mt19937_raw_modulo_fisher_yates"}};
}

Json run(const std::filesystem::path& directory, bool diagnostic) {
    std::filesystem::create_directories(directory / "fixtures");
    Json output{{"schema_version", 1}, {"family", "r6_multiprobe_dedup"}, {"status", "EXECUTED"},
                {"mode", diagnostic ? "DIAGNOSTIC" : "TIMING"}, {"configuration", configuration()},
                {"compiler", {{"id", AGENT_MEMORY_MP_COMPILER_ID}, {"version", AGENT_MEMORY_MP_COMPILER_VERSION}}},
                {"sizeof_size_t", sizeof(std::size_t)}, {"quality", "NOT_EXECUTED"},
                {"production_activation", false}, {"source_identity", "EXTERNAL_MANIFEST_REQUIRED"},
                {"peak_rss", "NOT_MEASURED"}, {"allocation_scope", diagnostic ? "ordinary_new_payload_only" : "NOT_MEASURED"},
                {"profiles", Json::array()}, {"raw_samples", Json::array()}, {"diagnostic_rows", Json::array()}};
    std::mt19937 order_engine(20261124);
    std::size_t id = 0, trial = 0;
    for(const auto width : {128U, 256U}) for(const auto count : {4096U, 262144U})
        for(std::size_t workload = 0; workload < 3; ++workload) for(const auto filtered : {false, true}) {
            const Profile profile{id++, width, count, workload, filtered};
            const auto file = std::string("fixtures/profile-") + std::to_string(profile.id) + ".bin";
            const auto data = fixture(profile, directory / file);
            std::array<Index, 4> indexes;
            Json build_ms = Json::object();
            for(std::size_t arm = 0; arm < 4; ++arm) {
                const auto start = Clock::now();
                indexes[arm] = make_index(arm, profile.options());
                for(const auto& value : data.records) indexes[arm]->upsert(value);
                build_ms[kArms[arm]] = milliseconds(start, Clock::now());
            }
            Json row{{"id", profile.id}, {"width", width}, {"count", count}, {"workload", kWorkloads[workload]},
                     {"filtered", filtered}, {"bits_per_table", workload == 1 ? 4 : 12},
                     {"max_probe_radius", workload == 0 ? 0 : 1}, {"fixture_file", file}, {"build_ms", build_ms},
                     {"reference_cases", Json::array()}, {"first_search_ms", Json::object()}};
            if(!diagnostic) for(std::size_t arm = 0; arm < 4; ++arm) {
                const auto start = Clock::now();
                const auto result = indexes[arm]->search(data.queries.front());
                row["first_search_ms"][kArms[arm]] = milliseconds(start, Clock::now());
                require(result.size() <= 10, "invalid first-search result count");
            }
            for(std::size_t query = 0; query < 8; ++query) {
                const auto expected = evidence(inspect(indexes[0], 0, data.queries[query]));
                for(std::size_t arm = 0; arm < 4; ++arm)
                    require(evidence(inspect(indexes[arm], arm, data.queries[query])) == expected,
                            "preflight full production evidence differs");
                row["reference_cases"].push_back({{"query", query}, {"evidence", expected}, {"arms_checked", 4}});
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
                if(diagnostic) {
                    const auto baseline = traced(indexes[1], 1, data.queries[query], expected);
                    for(std::size_t arm = 0; arm < 4; ++arm) {
                        Json trace = nullptr;
                        if(arm) {
                            const auto observed = traced(indexes[arm], arm, data.queries[query], expected);
                            require(same_trace(baseline, observed), "full matrix candidate identities differ");
                            trace = trace_evidence(observed);
                        }
                        output["diagnostic_rows"].push_back({{"profile", profile.id}, {"query", query},
                            {"arm", kArms[arm]}, {"trace", trace}, {"evidence", expected},
                            {"allocations", memory(indexes[arm], data.queries[query], expected)}});
                    }
                }
#endif
                if(!diagnostic) {
                    for(std::size_t arm = 0; arm < 4; ++arm)
                        require(hits(indexes[arm]->search(data.queries[query])) == expected.at("results"), "warmup mismatch");
                    for(std::size_t repeat = 0; repeat < 5; ++repeat) {
                        std::array<std::size_t, 4> order{{0, 1, 2, 3}};
                        for(std::size_t end = order.size() - 1; end > 0; --end)
                            std::swap(order[end], order[order_engine() % (end + 1)]);
                        for(std::size_t position = 0; position < order.size(); ++position) {
                            const auto arm = order[position];
                            const auto start = Clock::now();
                            const auto result = indexes[arm]->search(data.queries[query]);
                            const auto elapsed = milliseconds(start, Clock::now());
                            require(hits(result) == expected.at("results"), "timed result mismatch");
                            output["raw_samples"].push_back({{"profile", profile.id}, {"query", query},
                                {"repeat", repeat}, {"trial", trial}, {"order_position", position},
                                {"arm", kArms[arm]}, {"full_search_ms", elapsed}, {"results", hits(result)}});
                        }
                        ++trial;
                    }
                }
            }
            output["profiles"].push_back(std::move(row));
        }
    output["sample_count"] = output["raw_samples"].size();
    output["trial_count"] = trial;
    output["warmup_executions"] = diagnostic ? 0 : 768;
    output["diagnostic_count"] = output["diagnostic_rows"].size();
    return output;
}
} // namespace

int main(int argc, char** argv) {
    try {
        if(argc == 2 && std::string(argv[1]) == "--self-test") {
            std::cout << self_test().dump(2) << '\n'; return 0;
        }
        require(argc == 3, "usage: --self-test OR --benchmark/--diagnostic <new-evidence-directory>");
        const auto diagnostic = std::string(argv[1]) == "--diagnostic";
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
        require(diagnostic, "diagnostic executable cannot produce uninstrumented timings");
#else
        require(std::string(argv[1]) == "--benchmark", "timed executable has no diagnostics/allocation hooks");
#endif
        const std::filesystem::path directory(argv[2]);
        require(!std::filesystem::exists(directory), "refusing to overwrite existing evidence");
        const auto result = run(directory, diagnostic);
        std::ofstream output(directory / "raw.json");
        output << result.dump(2) << '\n';
        require(bool(output), "result write failed");
        std::cout << "MultiProbe " << (diagnostic ? "diagnostic" : "full-search timing") << ": EXECUTED\n";
        return 0;
    } catch(const std::exception& error) {
#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
        allocation_diagnostic::stop();
#endif
        std::cerr << "MultiProbe experiment rejected: " << error.what() << '\n'; return 1;
    }
}
