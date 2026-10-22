#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxDurableLexicalDocumentMemory.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>
#include <mdbx_containers/common/Config.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void expect_digest(const agent_memory::ResourceBodyDigest& digest) {
    constexpr std::array<std::uint8_t, 32> kAbcSha256{{
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea,
        0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
        0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c,
        0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad
    }};
    expect(digest.bytes == kAbcSha256, "SHA-256 abc vector mismatch");
}

std::filesystem::path test_path(const char* suffix) {
    return std::filesystem::temp_directory_path() /
        (std::string{"agent_memory_m0_importer_"} + suffix + ".mdbx");
}

void remove_database_or_fail(const std::filesystem::path& path) {
    std::error_code error;
    const bool existed = std::filesystem::exists(path, error);
    expect(!error, "benchmark database existence check failed");
    if(existed) {
        expect(std::filesystem::remove(path, error) && !error,
               "benchmark database could not be removed");
    }
    expect(!std::filesystem::exists(path, error) && !error,
           "benchmark database was not clean after removal");
}

agent_memory::ObservedResource original(
    std::string resource_id,
    std::string uri,
    std::string body
) {
    agent_memory::ObservedResource resource;
    resource.scope_id = agent_memory::ScopeId{"tenant-a"};
    resource.resource_id = agent_memory::ResourceId{std::move(resource_id)};
    resource.source_uri = std::move(uri);
    resource.utf8_body = std::move(body);
    return resource;
}

void use_test_clock(
    agent_memory::MdbxResourceImporterOptions& options,
    std::uint64_t now_ms
) {
    options.ownership_lease_ms = 100;
    options.clock_ms = [now_ms] { return now_ms; };
}

void append_framed(std::string& payload, std::string_view value) {
    payload += std::to_string(value.size());
    payload.push_back(':');
    payload.append(value.data(), value.size());
}

std::string legacy_v3_intent(
    std::string_view owner,
    std::string_view scope,
    std::string_view resource,
    std::string_view uri,
    std::string_view body,
    std::uint64_t epoch,
    std::uint64_t lease_deadline
) {
    std::string payload;
    append_framed(payload, "agent_memory.resource_import_intent.v3");
    append_framed(payload, owner);
    append_framed(payload, std::to_string(epoch));
    append_framed(payload, std::to_string(lease_deadline));
    append_framed(payload, scope);
    append_framed(payload, resource);
    append_framed(payload, uri);
    append_framed(payload, body);
    append_framed(payload, "original");
    append_framed(payload, "");
    append_framed(payload, "");
    append_framed(payload, "");
    append_framed(payload, "1");
    return payload;
}

std::string legacy_v4_intent(
    std::string_view profile,
    std::string_view owner,
    std::string_view scope,
    std::string_view resource,
    std::string_view uri,
    std::string_view body,
    std::uint64_t epoch,
    std::uint64_t lease_deadline
) {
    std::string payload;
    append_framed(payload, "agent_memory.resource_import_intent.v4");
    append_framed(payload, profile);
    append_framed(payload, owner);
    append_framed(payload, std::to_string(epoch));
    append_framed(payload, std::to_string(lease_deadline));
    append_framed(payload, scope);
    append_framed(payload, resource);
    append_framed(payload, uri);
    append_framed(payload, body);
    append_framed(payload, "original");
    append_framed(payload, "");
    append_framed(payload, "");
    append_framed(payload, "");
    append_framed(payload, "1");
    return payload;
}

std::string canonical_document_id(
    std::string_view scope,
    std::string_view resource
) {
    static constexpr char digits[] = "0123456789abcdef";
    const auto encode = [&](std::string_view value) {
        std::string encoded;
        encoded.reserve(value.size() * 2U);
        for(const auto byte : value) {
            const auto octet = static_cast<unsigned char>(byte);
            encoded.push_back(digits[octet >> 4U]);
            encoded.push_back(digits[octet & 0x0fU]);
        }
        return encoded;
    };
    return "durable-lexical:" + encode(scope) + ':' + encode(resource);
}

std::string benchmark_kind_index_key(
    std::string_view scope,
    std::string_view kind
) {
    std::string key;
    append_framed(key, scope);
    append_framed(key, kind);
    return key;
}

void run_benchmark() {
    using namespace agent_memory;
    for(const std::size_t body_bytes : {std::size_t{128} * 1024U,
                                        std::size_t{1024} * 1024U,
                                        std::size_t{4} * 1024U * 1024U}) {
        const auto path = test_path(body_bytes == 128U * 1024U
            ? "benchmark_128k"
            : body_bytes == 1024U * 1024U
                ? "benchmark_1m"
                : "benchmark_4m");
        std::error_code error;
        std::filesystem::remove(path, error);
        MdbxResourceImporterOptions options;
        options.path = path.string();
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        options.max_raw_body_bytes = body_bytes;
        options.max_raw_chunks = body_bytes / kMdbxResourceImporterRawChunkBytes;
        const auto diagnostics = std::make_shared<MdbxResourceImporterDiagnostics>();
        options.diagnostics = diagnostics;
        const auto body = std::string(body_bytes, 'b');
        const auto started = std::chrono::steady_clock::now();
        agent_memory::ResourceImportResult result;
        double open_elapsed = 0.0;
        double import_only_elapsed = 0.0;
        double close_elapsed = 0.0;
        {
            const auto open_started = std::chrono::steady_clock::now();
            auto importer = std::make_unique<MdbxResourceImporter>(std::move(options));
            open_elapsed = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - open_started
            ).count();
            const auto resource_id = body_bytes == 128U * 1024U
                ? "resource:benchmark-128k"
                : body_bytes == 1024U * 1024U
                    ? "resource:benchmark-1m"
                    : "resource:benchmark-4m";
            const auto import_started = std::chrono::steady_clock::now();
            result = importer->import(original(
                resource_id,
                "https://example.test/benchmark",
                body
            ));
            import_only_elapsed = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - import_started
            ).count();
            const auto close_started = std::chrono::steady_clock::now();
            importer.reset();
            close_elapsed = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - close_started
            ).count();
        }
        const auto import_elapsed = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - started
        ).count();
        const auto index_started = std::chrono::steady_clock::now();
        std::size_t indexed_units = 0;
        {
            MdbxKnowledgeUnitStore store({path.string(), "agent_memory", false});
            indexed_units = store.scan_by_scope_and_kind(
                ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
            ).size();
        }
        // Synthetic post-import control; this is not production telemetry.
        std::size_t synthetic_admission_probe_records = 0;
        {
            auto context = MdbxStorageContext::open(
                path.string(), false, kDefaultMdbxMaxDbs
            );
            mdbxc::KeyValueTable<std::string, std::string> index(
                context->connection(), "agent_memory_knowledge_units_by_kind"
            );
            const auto base = benchmark_kind_index_key("tenant-a", "chunk");
            const auto lower = base + std::string(8, '\0');
            const auto upper = base + std::string(8, '\xff');
            const auto txn = context->connection()->transaction(
                mdbxc::TransactionMode::READ_ONLY
            );
            for(std::size_t probe = 0; probe < result.raw_units.size(); ++probe) {
                index.for_each_range(
                    lower,
                    upper,
                    [&synthetic_admission_probe_records](const std::string&, const std::string&) {
                        ++synthetic_admission_probe_records;
                        return false;
                    },
                    txn
                );
            }
        }
        const auto index_elapsed = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - index_started
        ).count();
        const auto db_bytes = std::filesystem::file_size(path);
        std::cout << "body_bytes=" << body_bytes
                  << " chunks=" << result.raw_units.size()
                  << " import_elapsed_ms=" << import_elapsed
                  << " open_recover_ms=" << open_elapsed
                  << " import_only_ms=" << import_only_elapsed
                  << " close_ms=" << close_elapsed
                  << " index_scan_elapsed_ms=" << index_elapsed
                  << " index_scan_units=" << indexed_units
                  << " synthetic_admission_probe_records=" << synthetic_admission_probe_records
                  << " ownership_lookup_attempts=" << diagnostics->ownership_lookup_attempts.load()
                  << " ownership_checks=" << diagnostics->ownership_checks.load()
                  << " ownership_payload_materializations="
                  << diagnostics->ownership_payload_materializations.load()
                  << " ownership_payload_bytes="
                  << diagnostics->ownership_payload_bytes.load()
                  << " ownership_lookup_ns=" << diagnostics->ownership_lookup_ns.load()
                  << " ownership_compare_ns=" << diagnostics->ownership_compare_ns.load()
                  << " ownership_total_ns=" << diagnostics->ownership_total_ns.load()
                  << " lexical_ns=" << diagnostics->lexical_ns.load()
                  << " raw_unit_store_ns=" << diagnostics->raw_unit_store_ns.load()
                  << " raw_store_calls=" << diagnostics->raw_store_calls.load()
                  << " manifest_ns=" << diagnostics->manifest_ns.load()
                  << " db_bytes=" << db_bytes
                  << " index_entry_writes=" << result.raw_units.size()
                  << " index_write_amplification=1"
                  << '\n';
        std::filesystem::remove(path, error);
    }
}

std::string batch_benchmark_body(std::size_t bytes, bool utf8) {
    if(!utf8) return std::string(bytes, 'b');
    const std::string pattern =
        "Привет, durable memory сохраняет provenance между revision и source. ";
    std::string body;
    body.reserve(bytes);
    while(body.size() + pattern.size() <= bytes) body += pattern;
    body.append(bytes - body.size(), ' ');
    return body;
}

void run_batch_benchmark(std::size_t repeats, bool utf8) {
    using namespace agent_memory;
    for(const std::size_t body_bytes : {std::size_t{128} * 1024U,
                                        std::size_t{1024} * 1024U,
                                        std::size_t{4} * 1024U * 1024U}) {
        for(const std::size_t batch_size : {std::size_t{1}, std::size_t{16},
                                            std::size_t{64}, std::size_t{128}}) {
            const auto suffix = std::string{"batch_"} +
                std::to_string(body_bytes) + "_" + std::to_string(batch_size);
            const auto path = test_path(suffix.c_str());
            std::vector<double> samples;
            samples.reserve(repeats);
            std::size_t chunks = 0;
            std::size_t raw_store_calls = 0;
            std::uintmax_t db_bytes = 0;
            std::size_t baseline_chunks = 0;
            std::size_t baseline_raw_store_calls = 0;
            std::uintmax_t baseline_db_bytes = 0;
            bool have_baseline = false;
            for(std::size_t run = 0; run < repeats; ++run) {
                remove_database_or_fail(path);
                MdbxResourceImporterOptions options;
                options.path = path.string();
                options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
                options.max_raw_body_bytes = body_bytes;
                // UTF-8 boundary backtracking can create a small number of
                // additional ranges beyond bytes/chunk; keep this benchmark
                // admission bound above that deterministic slicing overhead.
                options.max_raw_chunks =
                    body_bytes / kMdbxResourceImporterRawChunkBytes + 256U;
                options.raw_chunk_batch_size = batch_size;
                const auto diagnostics = std::make_shared<MdbxResourceImporterDiagnostics>();
                options.diagnostics = diagnostics;
                const auto body = batch_benchmark_body(body_bytes, utf8);
                const auto started = std::chrono::steady_clock::now();
                ResourceImportResult result;
                {
                    MdbxResourceImporter importer{std::move(options)};
                    result = importer.import(original(
                        "resource:batch-benchmark",
                        "https://example.test/batch-benchmark",
                        body
                    ));
                }
                const auto elapsed = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - started
                ).count();
                chunks = result.raw_units.size();
                raw_store_calls = diagnostics->raw_store_calls.load();
                expect(std::filesystem::exists(path),
                       "benchmark import did not create a database");
                db_bytes = std::filesystem::file_size(path);
                if(!have_baseline) {
                    baseline_chunks = chunks;
                    baseline_raw_store_calls = raw_store_calls;
                    baseline_db_bytes = db_bytes;
                    have_baseline = true;
                } else {
                    expect(chunks == baseline_chunks,
                           "benchmark chunk count changed between repeats");
                    expect(raw_store_calls == baseline_raw_store_calls,
                           "benchmark store-call count changed between repeats");
                    expect(db_bytes == baseline_db_bytes,
                           "benchmark database size changed between repeats");
                }
                samples.push_back(elapsed);
                std::cout << "batch_size=" << batch_size
                          << " body_bytes=" << body_bytes
                          << " corpus=" << (utf8 ? "utf8" : "ascii")
                          << " run=" << (run + 1)
                          << " chunks=" << chunks
                          << " import_elapsed_ms=" << elapsed
                          << " raw_store_calls=" << raw_store_calls
                          << " db_bytes=" << db_bytes
                          << '\n';
                remove_database_or_fail(path);
            }
            std::sort(samples.begin(), samples.end());
            const auto middle = samples.size() / 2;
            const auto median = samples.size() % 2 == 0
                ? (samples[middle - 1] + samples[middle]) / 2.0
                : samples[middle];
            const auto min = samples.front();
            const auto max = samples.back();
            std::cout << "batch_size=" << batch_size
                      << " body_bytes=" << body_bytes
                      << " corpus=" << (utf8 ? "utf8" : "ascii")
                      << " repeats=" << repeats
                      << " median_ms=" << median
                      << " min_ms=" << min
                      << " max_ms=" << max
                      << " chunks=" << chunks
                      << " raw_store_calls=" << raw_store_calls
                      << " db_bytes=" << db_bytes
                      << '\n';
        }
    }
}

std::string diagnostic_body(std::size_t bytes, std::string_view corpus) {
    if(corpus == "technical") {
        std::string body;
        body.reserve(bytes);
        for(std::size_t i = 0; body.size() < bytes; ++i) {
            body += "resource_id=doc-" + std::to_string(i) +
                " generation=" + std::to_string(i % 97U) +
                " tokenizer=bm25 score=0." + std::to_string(i % 1000U) + "; ";
        }
        body.resize(bytes);
        return body;
    }
    const std::string pattern = corpus == "english"
        ? "Durable memory preserves source provenance across revisions. "
        : corpus == "russian"
            ? "Память сохраняет проверяемую связь с исходным документом. "
            : "b";
    std::string body;
    body.reserve(bytes);
    while(body.size() + pattern.size() <= bytes) body += pattern;
    if(body.empty()) body = pattern.substr(0, std::min(bytes, pattern.size()));
    while(body.size() < bytes) body.push_back(corpus == "pathological" ? 'b' : ' ');
    return body;
}

void run_corpus_benchmark(
    std::size_t body_bytes,
    std::string_view corpus
) {
    using namespace agent_memory;
    const auto path = test_path((std::string{"corpus_"} + std::string{corpus}).c_str());
    std::error_code error;
    std::filesystem::remove(path, error);
    MdbxResourceImporterOptions options;
    options.path = path.string();
    options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
    options.max_raw_body_bytes = body_bytes;
    options.max_raw_chunks = body_bytes / kMdbxResourceImporterRawChunkBytes + 2U;
    const auto diagnostics = std::make_shared<MdbxResourceImporterDiagnostics>();
    options.diagnostics = diagnostics;
    const auto body = diagnostic_body(body_bytes, corpus);
    StandardTokenizer tokenizer;
    const auto tokenization = tokenizer.tokenize(body);
    std::unordered_set<std::string> unique_tokens;
    for(const auto& token : tokenization.tokens) unique_tokens.insert(token.text);
    const auto started = std::chrono::steady_clock::now();
    {
        MdbxResourceImporter importer{std::move(options)};
        static_cast<void>(importer.import(original(
            "resource:corpus-" + std::string{corpus},
            "https://example.test/corpus",
            body
        )));
    }
    const auto elapsed = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - started
    ).count();
    std::cout << "corpus=" << corpus
              << " body_bytes=" << body_bytes
              << " utf8_bytes=" << body.size()
              << " token_count=" << tokenization.tokens.size()
              << " unique_token_count=" << unique_tokens.size()
              << " import_elapsed_ms=" << elapsed
              << " ownership_lookup_attempts=" << diagnostics->ownership_lookup_attempts.load()
              << " ownership_checks=" << diagnostics->ownership_checks.load()
              << " ownership_payload_bytes=" << diagnostics->ownership_payload_bytes.load()
              << " ownership_lookup_ns=" << diagnostics->ownership_lookup_ns.load()
              << " ownership_compare_ns=" << diagnostics->ownership_compare_ns.load()
              << " ownership_total_ns=" << diagnostics->ownership_total_ns.load()
              << " lexical_ns=" << diagnostics->lexical_ns.load()
              << " raw_unit_store_ns=" << diagnostics->raw_unit_store_ns.load()
              << " raw_store_calls=" << diagnostics->raw_store_calls.load()
              << " manifest_ns=" << diagnostics->manifest_ns.load()
              << '\n';
    std::filesystem::remove(path, error);
}

void run_fencing_benchmark(std::size_t body_bytes, std::size_t iterations) {
    using namespace agent_memory;
    const auto path = test_path("fencing");
    std::error_code error;
    std::filesystem::remove(path, error);
    const auto resource_id = std::string{"resource:fencing"};
    MdbxResourceImporterOptions options;
    options.path = path.string();
    options.fault_injector = [](ResourceImporterFaultPoint point) {
        if(point == ResourceImporterFaultPoint::AfterIntent) {
            throw std::runtime_error("diagnostic intent pause");
        }
    };
    try {
        MdbxResourceImporter importer{std::move(options)};
        static_cast<void>(importer.import(original(
            resource_id,
            "https://example.test/fencing",
            std::string(body_bytes, 'b')
        )));
    } catch(const std::runtime_error&) {
    }
    auto context = MdbxStorageContext::open(path.string(), false, kDefaultMdbxMaxDbs);
    mdbxc::KeyValueTable<std::string, std::string> intents(
        context->connection(), "agent_memory_resource_import_intents"
    );
    mdbxc::KeyValueTable<std::string, std::string> tokens(
        context->connection(), "agent_memory_diagnostic_fence_tokens"
    );
    const auto payload = intents.find(resource_id);
    expect(payload.has_value(), "fencing benchmark intent was not persisted");
    tokens.insert_or_assign(resource_id, std::string(32, 't'));
    std::size_t full_bytes = 0;
    auto started = std::chrono::steady_clock::now();
    for(std::size_t i = 0; i < iterations; ++i) {
        const auto value = intents.find(resource_id);
        if(value) full_bytes += value->size();
    }
    const auto full_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - started
    ).count();
    started = std::chrono::steady_clock::now();
    std::size_t token_bytes = 0;
    for(std::size_t i = 0; i < iterations; ++i) {
        const auto value = tokens.find(resource_id);
        if(value) token_bytes += value->size();
    }
    const auto token_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - started
    ).count();
    std::cout << "fencing_body_bytes=" << body_bytes
              << " iterations=" << iterations
              << " full_payload_size=" << payload->size()
              << " full_materialized_bytes=" << full_bytes
              << " full_find_ns=" << full_ns
              << " compact_token_bytes=" << token_bytes
              << " compact_find_ns=" << token_ns
              << '\n';
    std::filesystem::remove(path, error);
}

void run_transaction_benchmark(std::size_t units) {
    using namespace agent_memory;
    for(const std::size_t batch_size : {std::size_t{1}, std::size_t{16},
                                        std::size_t{64}, std::size_t{128}}) {
        const auto path = test_path(
            (std::string{"txn_durable_"} + std::to_string(batch_size)).c_str()
        );
        std::error_code error;
        std::filesystem::remove(path, error);
        mdbxc::Config config;
        config.pathname = path.string();
        config.max_dbs = kDefaultMdbxMaxDbs;
        config.no_subdir = true;
        auto context = MdbxStorageContext::attach(mdbxc::Connection::create(config));
        mdbxc::KeyValueTable<std::string, std::string> table(
            context->connection(), "agent_memory_diagnostic_batch"
        );
        const auto started = std::chrono::steady_clock::now();
        std::size_t commits = 0;
        for(std::size_t first = 0; first < units; first += batch_size) {
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            const auto last = std::min(units, first + batch_size);
            for(std::size_t i = first; i < last; ++i) {
                table.insert_or_assign(
                    "unit:" + std::to_string(i), std::string(1024, 'u'), txn
                );
            }
            txn.commit();
            ++commits;
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started
        ).count();
        std::cout << "txn_sync_durable=true"
                  << " txn_units=" << units
                  << " batch_size=" << batch_size
                  << " commits=" << commits
                  << " logical_payload_bytes=" << units * 1024U
                  << " elapsed_us=" << elapsed
                  << '\n';
        std::filesystem::remove(path, error);
    }
}

void run_diagnostics_negative_checks() {
    using namespace agent_memory;
    for(const bool erase_intent : {false, true}) {
        const auto path = test_path(erase_intent ? "diagnostics_missing" : "diagnostics_mismatch");
        std::error_code error;
        if(std::filesystem::exists(path)) {
            expect(std::filesystem::remove(path, error),
                   "diagnostic test database could not be reset");
        }
        const auto diagnostics = std::make_shared<MdbxResourceImporterDiagnostics>();
        auto context = MdbxStorageContext::open(path.string(), false, kDefaultMdbxMaxDbs);
        MdbxResourceImporterOptions options;
        options.path = path.string();
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        options.max_raw_body_bytes = 1024;
        options.max_raw_chunks = 2;
        options.diagnostics = diagnostics;
        options.fault_injector = [context, erase_intent](ResourceImporterFaultPoint point) {
            if(point != ResourceImporterFaultPoint::AfterRawUnitPublication) return;
            mdbxc::KeyValueTable<std::string, std::string> intents(
                context->connection(), "agent_memory_resource_import_intents"
            );
            mdbxc::KeyValueTable<std::string, std::string> tokens(
                context->connection(), "agent_memory_resource_import_fencing_tokens"
            );
            if(erase_intent) {
                expect(intents.erase("resource:diagnostic"), "negative test could not erase intent");
                expect(tokens.erase("resource:diagnostic"), "negative test could not erase token");
            } else {
                intents.insert_or_assign("resource:diagnostic", "mismatched-payload");
                tokens.insert_or_assign("resource:diagnostic", "mismatched-token");
            }
        };
        bool rejected = false;
        try {
            MdbxResourceImporter importer{std::move(options)};
            static_cast<void>(importer.import(original(
                "resource:diagnostic",
                "https://example.test/diagnostic",
                std::string(1024, 'b')
            )));
        } catch(const std::runtime_error& error_value) {
            rejected = std::string_view{error_value.what()} ==
                "resource import executor was fenced out";
        }
        expect(rejected, "diagnostic stale intent mutation was not rejected");
        expect(diagnostics->ownership_lookup_attempts.load() >= 3,
               "diagnostic negative case did not execute post-write fencing");
        if(erase_intent) {
            expect(
                diagnostics->ownership_payload_materializations.load() <
                    diagnostics->ownership_lookup_attempts.load(),
                "missing intent was incorrectly counted as materialized"
            );
        } else {
            expect(
                diagnostics->ownership_payload_materializations.load() ==
                    diagnostics->ownership_lookup_attempts.load(),
                "mismatched intent materialization count is inconsistent"
            );
        }
        expect(diagnostics->ownership_compare_ns.load() > 0,
               "diagnostic compare timer did not record a comparison");
        expect(
            diagnostics->ownership_total_ns.load() >=
                diagnostics->ownership_lookup_ns.load() +
                diagnostics->ownership_compare_ns.load(),
            "diagnostic total timer is below lookup plus compare"
        );
        context.reset();
        expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
               "diagnostic test database could not be removed");
    }
}

void run_fencing_schema_negative_checks() {
    using namespace agent_memory;
    struct Case final {
        const char* suffix;
        std::function<void(const std::shared_ptr<MdbxStorageContext>&)> setup;
        const char* expected;
    };
    const auto payload = legacy_v3_intent(
        "legacy-owner", "tenant-a", "resource:fencing-schema",
        "https://example.test/fencing-schema", "schema body", 1, 100
    );
    const std::vector<Case> cases{
        {
            "fencing_missing_token",
            [payload](const auto& context) {
                mdbxc::KeyValueTable<std::string, std::string> marker(
                    context->connection(), "agent_memory_resource_import_fencing_schema"
                );
                marker.insert_or_assign("schema", "agent_memory.resource_import_fencing.v1");
                mdbxc::KeyValueTable<std::string, std::string> intents(
                    context->connection(), "agent_memory_resource_import_intents"
                );
                intents.insert_or_assign("resource:fencing-schema", payload);
            },
            "missing or does not match"
        },
        {
            "fencing_mismatched_token",
            [payload](const auto& context) {
                mdbxc::KeyValueTable<std::string, std::string> marker(
                    context->connection(), "agent_memory_resource_import_fencing_schema"
                );
                marker.insert_or_assign("schema", "agent_memory.resource_import_fencing.v1");
                mdbxc::KeyValueTable<std::string, std::string> intents(
                    context->connection(), "agent_memory_resource_import_intents"
                );
                intents.insert_or_assign("resource:fencing-schema", payload);
                mdbxc::KeyValueTable<std::string, std::string> tokens(
                    context->connection(), "agent_memory_resource_import_fencing_tokens"
                );
                tokens.insert_or_assign("resource:fencing-schema", "wrong-token");
            },
            "missing or does not match"
        },
        {
            "fencing_orphan_token",
            [](const auto& context) {
                mdbxc::KeyValueTable<std::string, std::string> marker(
                    context->connection(), "agent_memory_resource_import_fencing_schema"
                );
                marker.insert_or_assign("schema", "agent_memory.resource_import_fencing.v1");
                mdbxc::KeyValueTable<std::string, std::string> tokens(
                    context->connection(), "agent_memory_resource_import_fencing_tokens"
                );
                tokens.insert_or_assign("resource:orphan", "orphan-token");
            },
            "orphaned"
        },
        {
            "fencing_bad_schema",
            [](const auto& context) {
                mdbxc::KeyValueTable<std::string, std::string> marker(
                    context->connection(), "agent_memory_resource_import_fencing_schema"
                );
                marker.insert_or_assign("schema", "agent_memory.resource_import_fencing.v0");
            },
            "unsupported resource import fencing schema marker"
        }
    };
    for(const auto& item : cases) {
        const auto path = test_path(item.suffix);
        std::error_code error;
        std::filesystem::remove(path, error);
        auto context = MdbxStorageContext::open(path.string());
        item.setup(context);
        MdbxResourceImporterOptions options;
        options.context = context;
        bool rejected = false;
        try {
            MdbxResourceImporter importer{std::move(options)};
        } catch(const std::runtime_error& error_value) {
            rejected = std::string_view{error_value.what()}.find(item.expected) !=
                std::string_view::npos;
        }
        expect(rejected, "fencing schema corruption was not rejected");
        context.reset();
        expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
               "fencing schema test database could not be removed");
    }
}

void run_offline_fencing_migration_checks() {
    using namespace agent_memory;
    struct Case final {
        const char* suffix;
        std::string payload;
        ResourceImportPublicationProfile profile;
        const char* resource_id;
    };
    const std::vector<Case> cases{
        {
            "offline_migrate_v3",
            legacy_v3_intent(
                "legacy-owner", "tenant-a", "resource:offline-v3",
                "https://example.test/offline-v3", "legacy v3 body", 1, 1
            ),
            ResourceImportPublicationProfile::LexicalOnly,
            "resource:offline-v3"
        },
        {
            "offline_migrate_v4",
            legacy_v4_intent(
                "lexical_with_raw_chunks", "legacy-owner", "tenant-a",
                "resource:offline-v4", "https://example.test/offline-v4",
                "legacy v4 body", 3, 1
            ),
            ResourceImportPublicationProfile::LexicalWithRawChunks,
            "resource:offline-v4"
        }
    };
    for(const auto& item : cases) {
        const auto path = test_path(item.suffix);
        std::error_code error;
        std::filesystem::remove(path, error);
        auto context = MdbxStorageContext::open(path.string());
        {
            mdbxc::KeyValueTable<std::string, std::string> intents(
                context->connection(), "agent_memory_resource_import_intents"
            );
            intents.insert_or_assign(item.resource_id, item.payload);
            mdbxc::KeyValueTable<std::string, std::string> bindings(
                context->connection(), "agent_memory_resource_import_bindings"
            );
            bindings.insert_or_assign(item.resource_id, "tenant-a");
            mdbxc::KeyValueTable<std::string, std::string> epochs(
                context->connection(), "agent_memory_resource_import_epochs"
            );
            epochs.insert_or_assign(
                item.resource_id,
                std::string_view{item.suffix} == "offline_migrate_v4" ? "3" : "1"
            );
        }

        MdbxResourceImporter::migrate_legacy_fencing_state(context);
        {
            mdbxc::KeyValueTable<std::string, std::string> marker(
                context->connection(), "agent_memory_resource_import_fencing_schema"
            );
            mdbxc::KeyValueTable<std::string, std::string> tokens(
                context->connection(), "agent_memory_resource_import_fencing_tokens"
            );
            const auto marker_value = marker.find("schema");
            const auto token_value = tokens.find(item.resource_id);
            expect(marker_value && *marker_value == "agent_memory.resource_import_fencing.v1",
                   "offline fencing migration did not install schema marker");
            expect(token_value && !token_value->empty(),
                   "offline fencing migration did not create token");
        }

        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = item.profile;
        options.max_raw_body_bytes = 1024;
        options.max_raw_chunks = 4;
        {
            MdbxResourceImporter importer{std::move(options)};
            const auto manifest = importer.find_manifest(ResourceId{item.resource_id});
            expect(manifest.has_value(), "offline migration recovery did not publish manifest");
        }
        if(item.profile == ResourceImportPublicationProfile::LexicalWithRawChunks) {
            MdbxKnowledgeUnitStoreOptions store_options;
            store_options.context = context;
            MdbxKnowledgeUnitStore store{std::move(store_options)};
            expect(store.scan_by_scope(ScopeId{"tenant-a"}).size() == 1,
                   "offline migration created duplicate raw units");
        }
        context.reset();
        context = MdbxStorageContext::open(path.string());
        MdbxResourceImporterOptions reopened_options;
        reopened_options.context = context;
        reopened_options.publication_profile = item.profile;
        reopened_options.max_raw_body_bytes = 1024;
        reopened_options.max_raw_chunks = 4;
        {
            MdbxResourceImporter reopened{std::move(reopened_options)};
            expect(
                reopened.find_manifest(ResourceId{item.resource_id}).has_value(),
                "offline migration manifest disappeared after reopen"
            );
        }
        if(item.profile == ResourceImportPublicationProfile::LexicalWithRawChunks) {
            MdbxKnowledgeUnitStoreOptions store_options;
            store_options.context = context;
            MdbxKnowledgeUnitStore store{std::move(store_options)};
            expect(store.scan_by_scope(ScopeId{"tenant-a"}).size() == 1,
                   "offline migration reopen duplicated raw units");
        }
        context.reset();
        expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
               "offline fencing migration database could not be removed");
    }
}

void run_inner_guard_negative_checks() {
    using namespace agent_memory;
    const auto path = test_path("inner_guards");
    std::error_code error;
    std::filesystem::remove(path, error);
    auto context = MdbxStorageContext::open(path.string());
    {
        mdbxc::KeyValueTable<std::string, std::string> intents(
            context->connection(), "agent_memory_resource_import_intents"
        );
        mdbxc::KeyValueTable<std::string, std::string> tokens(
            context->connection(), "agent_memory_resource_import_fencing_tokens"
        );
        intents.insert_or_assign("resource:inner", "current-intent");
        tokens.insert_or_assign("resource:inner", "current-token");
    }

    {
    bool canonical_rejected = false;
    MdbxCanonicalContentStoreOptions canonical_options;
    canonical_options.context = context;
    canonical_options.publication_guard_table_name =
        "agent_memory_resource_import_intents";
    canonical_options.publication_token_guard_table_name =
        "agent_memory_resource_import_fencing_tokens";
    MdbxCanonicalContentStore canonical{std::move(canonical_options)};
    CanonicalDocumentRevision initial;
    initial.document_id = DocumentId{"inner-canonical"};
    initial.revision = 0;
    initial.blocks.push_back(ContentBlock{
        ContentBlockId{"block"}, 1, ContentBlockKind::Paragraph, std::nullopt, "text"
    });
    try {
        canonical.create_document_guarded(
            initial,
            MdbxCanonicalPublicationGuard{
                "agent_memory_resource_import_intents", "resource:inner", "",
                "agent_memory_resource_import_fencing_tokens", "stale-token"
            }
        );
    } catch(const std::runtime_error& error_value) {
        canonical_rejected =
            std::string_view{error_value.what()}.find("token changed") !=
            std::string_view::npos;
    }
    expect(canonical_rejected, "canonical inner guard accepted stale token");
    expect(!canonical.read_current(DocumentId{"inner-canonical"}).has_value(),
           "canonical stale guard changed the head");

    bool lexical_rejected = false;
    MdbxDurableLexicalDocumentMemoryOptions lexical_options;
    lexical_options.context = context;
    MdbxDurableLexicalDocumentMemory lexical{std::move(lexical_options)};
    try {
        static_cast<void>(lexical.upsert_text_guarded(
            ScopeId{"tenant-a"}, ResourceId{"resource:inner"},
            "https://example.test/inner", "guarded text",
            DurableLexicalPublicationGuard{
                "agent_memory_resource_import_intents", "resource:inner", "",
                "agent_memory_resource_import_fencing_tokens", "stale-token"
            }
        ));
    } catch(const std::runtime_error& error_value) {
        lexical_rejected =
            std::string_view{error_value.what()}.find("fence rejected") !=
            std::string_view::npos;
    }
    expect(lexical_rejected, "lexical inner guard accepted stale token");
    expect(lexical.search(ScopeId{"tenant-a"}, "guarded", 5).empty(),
           "lexical stale guard published a revision");

    bool raw_rejected = false;
    MdbxKnowledgeUnitStoreOptions raw_options;
    raw_options.context = context;
    raw_options.publication_token_guard_table_name =
        "agent_memory_resource_import_fencing_tokens";
    MdbxKnowledgeUnitStore raw{std::move(raw_options)};
    KnowledgeUnitCreateRequest request;
    request.kind = KnowledgeUnitKind::Chunk;
    request.scope_id = ScopeId{"tenant-a"};
    request.primary_text = "guarded unit";
    request.display_text = request.primary_text;
    SourceRefSummary source;
    source.resource_id = ResourceId{"resource:inner"};
    source.resource_revision = ResourceRevisionRef{
        ResourceId{"resource:inner"}, 1, ResourceBodyDigest{}
    };
    source.uri = "https://example.test/inner";
    source.excerpt = TextRange{0, 7};
    source.preview = request.primary_text;
    source.quote_hash = compute_quote_hash(source.preview);
    request.sources.push_back(std::move(source));
    request.content_hash_recipe_version = 2;
    try {
        static_cast<void>(raw.create_or_get_unit_guarded(
            request,
            MdbxKnowledgeUnitPublicationGuard{
                "agent_memory_resource_import_fencing_tokens", "resource:inner",
                "stale-token"
            }
        ));
    } catch(const std::runtime_error& error_value) {
        raw_rejected =
            std::string_view{error_value.what()}.find("knowledge-unit publication fence rejected") !=
            std::string_view::npos;
    }
    expect(raw_rejected, "raw inner guard accepted stale token");
    expect(raw.scan_by_scope(ScopeId{"tenant-a"}).empty(),
           "raw stale guard inserted a knowledge unit");

    bool manifest_rejected = false;
    MdbxResourceManifestStorageOptions manifest_options;
    manifest_options.context = context;
    manifest_options.publication_guard_table_name =
        "agent_memory_resource_import_intents";
    manifest_options.publication_token_guard_table_name =
        "agent_memory_resource_import_fencing_tokens";
    MdbxResourceManifestStorage manifests{std::move(manifest_options)};
    ResourceManifest manifest;
    manifest.revision.resource_id = ResourceId{"resource:inner"};
    manifest.revision.generation = 1;
    manifest.revision.body_digest = ResourceBodyDigest{};
    manifest.payload_version = ResourceManifestPayloadVersion::V6;
    try {
        manifests.upsert_manifest_if_unchanged_guarded(
            manifest, std::nullopt,
            MdbxManifestPublicationGuard{
                "agent_memory_resource_import_intents", "resource:inner", "",
                "agent_memory_resource_import_fencing_tokens", "stale-token"
            }
        );
    } catch(const std::runtime_error& error_value) {
        manifest_rejected =
            std::string_view{error_value.what()}.find("fence rejected the token") !=
            std::string_view::npos;
    }
    expect(manifest_rejected, "manifest inner guard accepted stale token");
    expect(!manifests.find_manifest(ResourceId{"resource:inner"}).has_value(),
           "manifest stale guard published a manifest");
    }

    context.reset();
    expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
           "inner guard test database could not be removed");
}

void run_lexical_catalog_guard_race_check() {
    using namespace agent_memory;
    const auto path = test_path("lexical_catalog_guard_race");
    std::error_code error;
    std::filesystem::remove(path, error);
    auto context = MdbxStorageContext::open(path.string());

    const ScopeId scope{"tenant-catalog"};
    const ResourceId baseline_resource{"baseline"};
    const ResourceId guarded_resource{"guarded"};
    const std::string intent_key = "resource:catalog-race";
    const std::string scope_version_key = "scope-version\x1f" + scope.value();
    const auto encode_document_component = [](std::string_view value) {
        static constexpr char digits[] = "0123456789abcdef";
        std::string encoded;
        encoded.reserve(value.size() * 2U);
        for(const auto byte : value) {
            const auto octet = static_cast<unsigned char>(byte);
            encoded.push_back(digits[octet >> 4U]);
            encoded.push_back(digits[octet & 0x0fU]);
        }
        return encoded;
    };
    const DocumentId guarded_document_id{
        "durable-lexical:" + encode_document_component(scope.value()) + ':' +
        encode_document_component(guarded_resource.value())
    };

    {
        mdbxc::KeyValueTable<std::string, std::string> intents(
            context->connection(), "agent_memory_resource_import_intents"
        );
        intents.insert_or_assign(intent_key, "intent-v1");
        mdbxc::KeyValueTable<std::string, std::string> tokens(
            context->connection(), "agent_memory_resource_import_fencing_tokens"
        );
        tokens.insert_or_assign(intent_key, "token-v1");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions baseline_options;
        baseline_options.context = context;
        MdbxDurableLexicalDocumentMemory baseline{std::move(baseline_options)};
        expect(
            baseline.upsert_text(
                scope,
                baseline_resource,
                "https://example.test/baseline",
                "Baseline lexical visibility remains intact."
            ) == 1,
            "lexical catalog guard baseline publication failed"
        );
        {
            mdbxc::KeyValueTable<std::string, std::string> catalog(
                context->connection(), "agent_memory_durable_lexical_catalog"
            );
            const auto version = catalog.find(scope_version_key);
            expect(version && *version == "1", "baseline scope version mismatch");
        }

        bool token_mutated_after_canonical = false;
        MdbxDurableLexicalDocumentMemoryOptions guarded_options;
        guarded_options.context = context;
        guarded_options.fault_injector = [&](DurableLexicalDocumentMemoryFaultPoint point) {
            if(point != DurableLexicalDocumentMemoryFaultPoint::BeforeCatalogPublication ||
               token_mutated_after_canonical) {
                return;
            }
            mdbxc::KeyValueTable<std::string, std::string> tokens(
                context->connection(), "agent_memory_resource_import_fencing_tokens"
            );
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            tokens.insert_or_assign(intent_key, "token-v2", txn);
            txn.commit();
            token_mutated_after_canonical = true;
        };
        MdbxDurableLexicalDocumentMemory guarded{std::move(guarded_options)};

        bool rejected = false;
        try {
            static_cast<void>(guarded.upsert_text_guarded(
                scope,
                guarded_resource,
                "https://example.test/guarded",
                "Catalog fencing rejects a token changed after canonical publication.",
                DurableLexicalPublicationGuard{
                    "agent_memory_resource_import_intents",
                    intent_key,
                    "",
                    "agent_memory_resource_import_fencing_tokens",
                    "token-v1"
                }
            ));
        } catch(const std::runtime_error& error_value) {
            rejected = std::string_view{error_value.what()}.find("fence rejected") !=
                std::string_view::npos;
        }
        expect(token_mutated_after_canonical,
               "lexical catalog guard did not mutate token after canonical publication");
        expect(rejected, "lexical catalog guard accepted a changed token");
        MdbxCanonicalContentStoreOptions canonical_options;
        canonical_options.context = context;
        MdbxCanonicalContentStore canonical{std::move(canonical_options)};
        expect(
            canonical.read_current(guarded_document_id).has_value(),
            "lexical catalog guard did not reach the post-canonical publication point"
        );
        expect(
            guarded.search(scope, "catalog fencing", 5).empty(),
            "lexical catalog guard exposed a fenced publication"
        );
        expect(
            guarded.search(scope, "baseline lexical", 5).size() == 1,
            "lexical catalog guard changed existing visibility"
        );
        {
            mdbxc::KeyValueTable<std::string, std::string> catalog(
                context->connection(), "agent_memory_durable_lexical_catalog"
            );
            const auto version = catalog.find(scope_version_key);
            expect(version && *version == "1",
                   "fenced lexical publication advanced the scope version");
            mdbxc::KeyValueTable<std::string, std::string> tokens(
                context->connection(), "agent_memory_resource_import_fencing_tokens"
            );
            const auto token = tokens.find(intent_key);
            expect(token && *token == "token-v2",
                   "lexical catalog guard token mutation was not durable");
        }
    }

    context.reset();
    expect(
        std::filesystem::remove(path, error) || !std::filesystem::exists(path),
        "lexical catalog guard test database could not be removed"
    );
}

} // namespace

void run_manifest_aware_original_publication_check() {
    using namespace agent_memory;
    const auto path = test_path("manifest_original");
    std::error_code error;
    std::filesystem::remove(path, error);
    expect(!error && !std::filesystem::exists(path),
           "manifest-aware projection fixture was not cleanly removed");
    auto context = MdbxStorageContext::open(path.string());
    const auto body = std::string(2048, 'o');
    MdbxResourceImporterOptions interrupted_options;
    interrupted_options.context = context;
    interrupted_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    interrupted_options.max_raw_body_bytes = 4096;
    interrupted_options.max_raw_chunks = 4;
    use_test_clock(interrupted_options, 100);
    bool interrupted = false;
    interrupted_options.fault_injector = [](ResourceImporterFaultPoint point) {
        if(point == ResourceImporterFaultPoint::AfterRawUnitPublication) {
            throw std::runtime_error("interrupt after raw/projection batch");
        }
    };
    {
        MdbxResourceImporter importer{std::move(interrupted_options)};
        try {
            (void)importer.import(original(
                "resource:manifest-original",
                "https://example.test/manifest-original",
                body
            ));
        } catch(const std::runtime_error&) {
            interrupted = true;
        }
    }
    expect(interrupted, "projection publication fixture did not interrupt");

    KnowledgeUnitId first_staged_unit;
    {
    MdbxKnowledgeUnitStore raw_store(MdbxKnowledgeUnitStoreOptions{
        {}, "agent_memory", false, kDefaultMdbxMaxDbs, context, {}
    });
    const auto staged_units = raw_store.scan_by_scope_and_kind(
        ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
    );
    expect(staged_units.size() == 2,
           "interrupted projection fixture did not retain two staged raw units");
    first_staged_unit = staged_units.front();
    MdbxProjectionStore projection_store(MdbxProjectionStoreOptions{
        {}, "agent_memory", false, kDefaultMdbxMaxDbs, context
    });
    for(const auto unit_id : staged_units) {
        const auto staged_projection = projection_store.list(
            ScopeId{"tenant-a"}, unit_id, ProjectionKind::Original
        );
        expect(staged_projection.size() == 1 && staged_projection.front().body ==
                   std::string(1024, 'o'),
               "interrupted import did not retain the staged Original projection");
    }
    MdbxResourceManifestStorage manifests(MdbxResourceManifestStorageOptions{
        {}, "agent_memory", false, kDefaultMdbxMaxDbs, context
    });
    expect(!manifests.find_manifest(ResourceId{"resource:manifest-original"}),
           "manifest became visible before Original projection publication completed");

    MdbxResourceImporterOptions recovery_options;
    recovery_options.context = context;
    recovery_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    recovery_options.max_raw_body_bytes = 4096;
    recovery_options.max_raw_chunks = 4;
    use_test_clock(recovery_options, 1'000);
    MdbxResourceImporter recovered{std::move(recovery_options)};
    const auto manifest = recovered.find_manifest(ResourceId{"resource:manifest-original"});
    expect(manifest && manifest->state == ResourceManifestState::Active,
           "recovery did not publish an active manifest");
    expect(manifest->records.size() == 6,
           "manifest did not bind document, chunk, raw unit and Original projection");
    auto mutable_patch = KnowledgeUnitMutablePatch{};
    mutable_patch.display_text = "updated display text";
    const auto updated = raw_store.update_mutable_fields(
        first_staged_unit, std::move(mutable_patch)
    );
    expect(updated.revision == 2,
           "mutable envelope update did not advance the unit revision");
    const auto replayed = recovered.import(original(
        "resource:manifest-original",
        "https://example.test/manifest-original",
        body
    ));
    expect(replayed.raw_units == staged_units,
           "replay after a mutable update allocated new raw unit ids");
    expect(projection_store.list(
               ScopeId{"tenant-b"}, first_staged_unit, ProjectionKind::Original
           ).empty(),
           "Original projection leaked across scopes");
    for(std::size_t index = 0; index < 2; ++index) {
        const auto unit_id = replayed.raw_units[index];
        const auto expected = std::string{"original_projection:"} +
            std::to_string(unit_id.value());
        const auto it = std::find_if(
            manifest->records.begin(), manifest->records.end(),
            [&](const DerivedRecordRef& record) {
                return record.kind == DerivedRecordKind::Custom &&
                    record.key == expected && record.ordinal == index;
            }
        );
        expect(it != manifest->records.end(),
               "manifest missed an Original projection binding");
        const auto projections = projection_store.list(
            ScopeId{"tenant-a"}, unit_id, ProjectionKind::Original
        );
        expect(projections.size() == (index == 0 ? 2U : 1U) &&
                   projections.front().body == std::string(1024, 'o'),
               "recovery did not preserve the Original projection identity");
        if(index == 0) {
            expect(projections.back().version.projection_revision == 2,
                   "mutable envelope revision did not allocate a new projection revision");
        }
    }
    const auto repeated = recovered.import(original(
        "resource:manifest-original",
        "https://example.test/manifest-original",
        body
    ));
    expect(repeated.raw_units == staged_units,
           "repeated Original import changed raw unit identity");
    expect(projection_store.list(
               ScopeId{"tenant-a"}, first_staged_unit, ProjectionKind::Original
           ).size() == 2,
           "repeated import duplicated the current Original projection");
    }
    context.reset();
    {
        auto reopened_context = MdbxStorageContext::open(path.string());
        MdbxProjectionStore reopened_projection_store(MdbxProjectionStoreOptions{
            {}, "agent_memory", false, kDefaultMdbxMaxDbs, reopened_context
        });
        expect(reopened_projection_store.list(
                   ScopeId{"tenant-a"}, first_staged_unit, ProjectionKind::Original
               ).size() == 2,
               "Original projection did not survive reopen");
    }
    expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
           "manifest-aware projection database could not be removed");
}

int main(int argc, char** argv) {
    if(argc == 2 && std::string_view{argv[1]} == "--benchmark") {
        run_benchmark();
        return 0;
    }
    if(argc >= 2 && std::string_view{argv[1]} == "--batch-benchmark") {
        const auto repeats = argc >= 3
            ? static_cast<std::size_t>(std::stoull(argv[2]))
            : std::size_t{1};
        expect(repeats > 0, "batch benchmark repeat count must be positive");
        const bool utf8 = argc >= 4 && std::string_view{argv[3]} == "utf8";
        run_batch_benchmark(repeats, utf8);
        return 0;
    }
    if(argc >= 2 && std::string_view{argv[1]} == "--fencing-benchmark") {
        run_fencing_benchmark(
            argc >= 3 ? static_cast<std::size_t>(std::stoull(argv[2])) : 4U * 1024U * 1024U,
            argc >= 4 ? static_cast<std::size_t>(std::stoull(argv[3])) : 8192U
        );
        return 0;
    }
    if(argc >= 2 && std::string_view{argv[1]} == "--transaction-benchmark") {
        run_transaction_benchmark(
            argc >= 3 ? static_cast<std::size_t>(std::stoull(argv[2])) : 1024U
        );
        return 0;
    }
    if(argc >= 4 && std::string_view{argv[1]} == "--corpus-benchmark") {
        run_corpus_benchmark(
            static_cast<std::size_t>(std::stoull(argv[2])),
            argv[3]
        );
        return 0;
    }
    run_diagnostics_negative_checks();
    run_fencing_schema_negative_checks();
    run_offline_fencing_migration_checks();
    run_inner_guard_negative_checks();
    run_lexical_catalog_guard_race_check();
    run_manifest_aware_original_publication_check();
    using namespace agent_memory;
    const auto path = test_path("contract");
    std::error_code error;
    std::filesystem::remove(path, error);

    {
        MdbxResourceImporterOptions options;
        options.path = path.string();
        MdbxResourceImporter importer{std::move(options)};
        const auto first = importer.import(original(
            "resource:one",
            "https://example.test/one",
            "Durable importer keeps this searchable source."
        ));
        expect(first.revision.generation == 1, "initial import generation mismatch");
        expect(first.revision.body_digest.has_value(), "body digest was not published");
        expect(first.raw_units.empty(), "lexical-only importer unexpectedly published raw units");
        expect(first.document_id.value().find("durable-lexical:") == 0,
               "document identity is not lexical-profile stable");
        const auto manifest = importer.find_manifest(first.resource_id);
        expect(manifest && manifest->records.size() == 2,
               "import did not publish document and chunk manifest records");
        expect(manifest->revision.text_origin == SourceTextOrigin::OriginalText,
               "original import changed text origin");
        expect(importer.search(ScopeId{"tenant-a"}, "searchable", 5).size() == 1,
               "imported source was not searchable");
        bool oversized_search_rejected = false;
        try {
            (void)importer.search(
                ScopeId{"tenant-a"},
                "searchable",
                kMdbxResourceImporterMaxSearchLimit + 1
            );
        } catch(const std::invalid_argument&) { oversized_search_rejected = true; }
        expect(oversized_search_rejected,
               "search accepted a limit beyond its bounded refill window");

        const auto uri_only = importer.import(original(
            "resource:one",
            "https://example.test/renamed",
            "Durable importer keeps this searchable source."
        ));
        expect(uri_only.revision.generation == 1,
               "metadata-only update created a false body revision");

        bool pipeline_rejected = false;
        auto pipeline_changed = original(
            "resource:one", "https://example.test/renamed", "Durable importer keeps this searchable source."
        );
        pipeline_changed.pipeline_config_hash = 2;
        try { (void)importer.import(std::move(pipeline_changed)); }
        catch(const std::runtime_error&) { pipeline_rejected = true; }
        expect(pipeline_rejected,
               "pipeline metadata changed without a new body generation");

        auto sha = importer.import(original("resource:sha", "https://example.test/sha", "abc"));
        expect(sha.revision.body_digest.has_value(), "SHA vector import has no digest");
        expect_digest(*sha.revision.body_digest);

        bool scope_rejected = false;
        auto foreign_scope = original(
            "resource:one", "https://example.test/foreign", "Durable importer keeps this searchable source."
        );
        foreign_scope.scope_id = ScopeId{"tenant-b"};
        try { (void)importer.import(std::move(foreign_scope)); }
        catch(const std::invalid_argument&) { scope_rejected = true; }
        expect(scope_rejected, "ResourceId was reused across scopes");

        bool rejected = false;
        auto derived = original("resource:derived", "https://example.test/derived", "derived");
        derived.text_origin = SourceTextOrigin::DerivedExtraction;
        try { (void)importer.import(derived); }
        catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "derived text without provenance was accepted");

        derived.derived_text_provenance = DerivedTextProvenance{
            "text/html", "example-extractor", "1"
        };
        const auto derived_result = importer.import(std::move(derived));
        const auto derived_manifest = importer.find_manifest(derived_result.resource_id);
        expect(
            derived_manifest &&
                derived_manifest->revision.text_origin == SourceTextOrigin::DerivedExtraction &&
                derived_manifest->revision.derived_text_provenance &&
                derived_manifest->revision.derived_text_provenance->extractor_id ==
                    "example-extractor",
            "valid derived provenance was not persisted"
        );

        auto malformed = original("resource:malformed", "https://example.test/malformed", "x");
        malformed.utf8_body = std::string{"\xc3\x28", 2};
        bool malformed_rejected = false;
        try { (void)importer.import(std::move(malformed)); }
        catch(const std::invalid_argument&) { malformed_rejected = true; }
        expect(malformed_rejected, "malformed UTF-8 was accepted");
    }

    {
        MdbxResourceImporterOptions options;
        options.path = path.string();
        MdbxResourceImporter importer{std::move(options)};
        expect(importer.search(ScopeId{"tenant-a"}, "searchable", 5).size() == 1,
               "reopen lost imported lexical source");
        const auto manifest = importer.find_manifest(ResourceId{"resource:one"});
        expect(manifest && manifest->revision.generation == 1,
               "reopen lost imported manifest");
    }

    const auto recovery_path = test_path("recovery");
    std::filesystem::remove(recovery_path, error);
    {
        MdbxResourceImporterOptions options;
        options.path = recovery_path.string();
        use_test_clock(options, 100);
        options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::BeforeManifestPublication) {
                throw std::runtime_error("importer interruption");
            }
        };
        MdbxResourceImporter importer{std::move(options)};
        bool interrupted = false;
        try {
            (void)importer.import(original(
                "resource:recovery",
                "https://example.test/recovery",
                "Recovery intent completes the publication."
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "importer fault injection did not interrupt publication");
        expect(importer.search(ScopeId{"tenant-a"}, "publication", 5).empty(),
               "pending lexical publication was visible before manifest commit");
    }
    {
        MdbxResourceImporterOptions options;
        options.path = recovery_path.string();
        use_test_clock(options, 1'000);
        MdbxResourceImporter importer{std::move(options)};
        const auto manifest = importer.find_manifest(ResourceId{"resource:recovery"});
        expect(manifest && manifest->revision.generation == 1,
               "reopen did not recover pending importer intent");
        expect(importer.search(ScopeId{"tenant-a"}, "publication", 5).size() == 1,
               "recovered import was not searchable");
    }

    for(const auto point : {
        ResourceImporterFaultPoint::AfterIntent,
        ResourceImporterFaultPoint::BeforeLexicalPublication,
        ResourceImporterFaultPoint::AfterLexicalPublication,
        ResourceImporterFaultPoint::BeforeManifestPublication,
        ResourceImporterFaultPoint::AfterManifestPublication
    }) {
        const auto fault_path = test_path(("fault_" +
            std::to_string(static_cast<int>(point))).c_str());
        std::filesystem::remove(fault_path, error);
        {
            MdbxResourceImporterOptions options;
            options.path = fault_path.string();
            use_test_clock(options, 100);
            options.fault_injector = [point](ResourceImporterFaultPoint observed) {
                if(observed == point) throw std::runtime_error("fault fixture interruption");
            };
            MdbxResourceImporter importer{std::move(options)};
            bool interrupted = false;
            try {
                (void)importer.import(original(
                    "resource:fault", "https://example.test/fault", "fault recovery source"
                ));
            } catch(const std::runtime_error&) { interrupted = true; }
            expect(interrupted, "fault point did not interrupt import");
            expect(importer.search(ScopeId{"tenant-a"}, "recovery", 5).empty(),
                   "faulted import leaked an uncommitted lexical result");
        }
        {
            MdbxResourceImporterOptions options;
            options.path = fault_path.string();
            use_test_clock(options, 1'000);
            MdbxResourceImporter importer{std::move(options)};
            expect(importer.search(ScopeId{"tenant-a"}, "recovery", 5).size() == 1,
                   "reopen did not recover faulted import");
        }
        std::filesystem::remove(fault_path, error);
    }

    const auto refill_path = test_path("search_refill");
    std::filesystem::remove(refill_path, error);
    {
        const auto context = MdbxStorageContext::open(refill_path.string());
        MdbxResourceImporterOptions committed_options;
        committed_options.context = context;
        MdbxResourceImporter committed{std::move(committed_options)};
        (void)committed.import(original(
            "resource:visible", "https://example.test/visible",
            "shared retrieval phrase visible"
        ));

        MdbxResourceImporterOptions pending_options;
        pending_options.context = context;
        pending_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterLexicalPublication) {
                throw std::runtime_error("leave lexical intent pending");
            }
        };
        MdbxResourceImporter pending{std::move(pending_options)};
        bool interrupted = false;
        try {
            (void)pending.import(original(
                "resource:pending", "https://example.test/pending",
                "shared retrieval phrase pending"
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "search refill fixture did not leave an intent pending");
        const auto visible = pending.search(ScopeId{"tenant-a"}, "shared retrieval", 1);
        expect(visible.size() == 1 && visible.front().resource_id == ResourceId{"resource:visible"},
               "pending filtering did not refill the bounded lexical candidate set");
    }
    std::filesystem::remove(refill_path, error);

    const auto foreign_manifest_path = test_path("foreign_schema");
    std::filesystem::remove(foreign_manifest_path, error);
    {
        const auto context = MdbxStorageContext::open(foreign_manifest_path.string());
        MdbxResourceManifestStorageOptions storage_options;
        storage_options.context = context;
        MdbxResourceManifestStorage storage{std::move(storage_options)};
        ResourceManifest foreign;
        foreign.revision.resource_id = ResourceId{"resource:foreign-schema"};
        foreign.revision.generation = 1;
        foreign.revision.content_hash = 1;
        foreign.records = {
            DerivedRecordRef{
                DerivedRecordKind::Document,
                ChunkId{},
                "foreign-document",
                0
            }
        };
        foreign.schema = ResourceManifestSchema{"other.writer", 7};
        storage.upsert_manifest(std::move(foreign));

        MdbxResourceImporterOptions options;
        options.context = context;
        MdbxResourceImporter importer{std::move(options)};
        bool rejected = false;
        try {
            (void)importer.import(original(
                "resource:foreign-schema",
                "https://example.test/foreign-schema",
                "foreign schema must remain untouched"
            ));
        } catch(const std::runtime_error&) { rejected = true; }
        expect(rejected, "foreign manifest schema was accepted for overwrite");
        const mdbxc::KeyValueTable<std::string, std::string> bindings(
            context->connection(),
            "agent_memory_resource_import_bindings"
        );
        expect(!bindings.find("resource:foreign-schema"),
               "failed foreign-schema import left a durable scope binding");
    }
    std::filesystem::remove(foreign_manifest_path, error);

    const auto concurrency_path = test_path("concurrency");
    std::filesystem::remove(concurrency_path, error);

    const auto wired_path = test_path("knowledge_units");
    std::filesystem::remove(wired_path, error);
    {
        const auto context = MdbxStorageContext::open(wired_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));

        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(options, 100);
        MdbxResourceImporter importer{std::move(options)};

        std::string body;
        for(int index = 0; index < 400; ++index) body += "Привет, durable chunk! ";
        const auto imported = importer.import(original(
            "resource:wired", "https://example.test/wired", body
        ));
        expect(imported.raw_units.size() > 1,
               "wired importer did not split an oversized UTF-8 body");
        const auto manifest = importer.find_manifest(imported.resource_id);
        expect(manifest && manifest->records.size() == imported.raw_units.size() + 2,
               "manifest did not publish one custom binding per raw unit");
        expect(manifest->schema.schema_id ==
                   "agent_memory.m0_resource_importer.raw_chunks@agent_memory",
               "raw manifest did not persist its publication namespace");

        std::string reconstructed;
        std::uint64_t expected_offset = 0;
        for(std::size_t index = 0; index < imported.raw_units.size(); ++index) {
            const auto envelope = store->find(imported.raw_units[index]);
            expect(envelope.has_value(), "raw unit was not persisted");
            expect(envelope->sources.size() == 1,
                   "raw unit did not retain exactly one source summary");
            const auto& source = envelope->sources.front();
            expect(source.reference_mode == SourceReferenceMode::RevisionBoundQuote,
                   "raw unit source was not revision-bound");
            expect(source.resource_revision &&
                       source.resource_revision->resource_id == imported.resource_id &&
                       source.resource_revision->generation == imported.revision.generation &&
                       source.resource_revision->body_digest.bytes ==
                           imported.revision.body_digest->bytes,
                   "raw unit source revision binding is incomplete");
            expect(source.excerpt.offset == expected_offset,
                   "raw unit ranges are not contiguous");
            expect(source.excerpt.length == envelope->primary_text.size(),
                   "raw unit range length does not match primary text");
            expect(envelope->primary_text.size() <= 1024,
                   "raw unit exceeded the M0 primary text bound");
            expect(source.preview.size() <= 256 &&
                       envelope->primary_text.compare(0, source.preview.size(), source.preview) == 0,
                   "raw unit preview is not a bounded prefix");
            reconstructed += envelope->primary_text;
            expected_offset += source.excerpt.length;

            const auto binding = std::string{"knowledge_unit:"} +
                std::to_string(imported.raw_units[index].value());
            bool found_binding = false;
            for(const auto& record : manifest->records) {
                if(record.kind == DerivedRecordKind::Custom && record.key == binding) {
                    found_binding = true;
                    break;
                }
            }
            expect(found_binding, "manifest missed a raw knowledge-unit binding");
        }
        expect(reconstructed == body, "UTF-8 chunk slicing changed the source body");

        const auto repeated = importer.import(original(
            "resource:wired", "https://example.test/wired", body
        ));
        expect(repeated.revision.generation == imported.revision.generation,
               "idempotent wired import advanced the generation");
        expect(repeated.raw_units == imported.raw_units,
               "idempotent wired import allocated duplicate raw units");
        const auto uri_changed = importer.import(original(
            "resource:wired", "https://example.test/wired-v2", body
        ));
        expect(uri_changed.raw_units == imported.raw_units,
               "URI-only update allocated a new raw unit identity");
        const auto historical = store->find(imported.raw_units.front());
        expect(historical && historical->sources.front().uri == "https://example.test/wired",
               "raw source URI was rewritten instead of remaining historical");
    }
    {
        const auto context = MdbxStorageContext::open(wired_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(options, 1'000);
        MdbxResourceImporter importer{std::move(options)};
        const auto manifest = importer.find_manifest(ResourceId{"resource:wired"});
        expect(manifest && manifest->records.size() > 2,
               "reopen lost wired manifest bindings");
        expect(store->scan_by_scope_and_kind(ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk).size() > 1,
               "reopen lost raw knowledge units");
    }
    std::filesystem::remove(wired_path, error);

    const auto wired_recovery_path = test_path("knowledge_units_recovery");
    std::filesystem::remove(wired_recovery_path, error);

    const auto profile_path = test_path("publication_profile");
    std::filesystem::remove(profile_path, error);
    {
        const auto context = MdbxStorageContext::open(profile_path.string());
        MdbxResourceImporterOptions raw_options;
        raw_options.context = context;
        raw_options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(raw_options, 100);
        MdbxResourceImporter raw{std::move(raw_options)};
        const auto result = raw.import(original(
            "resource:profile", "https://example.test/profile", "profile-bound source"
        ));
        expect(result.raw_units.size() == 1, "raw profile did not publish its unit");

        MdbxResourceImporterOptions lexical_options;
        lexical_options.context = context;
        use_test_clock(lexical_options, 100);
        MdbxResourceImporter lexical{std::move(lexical_options)};
        bool downgraded = false;
        try {
            (void)lexical.import(original(
                "resource:profile", "https://example.test/profile", "profile-bound source"
            ));
        } catch(const std::runtime_error&) { downgraded = true; }
        expect(downgraded, "wired resource silently downgraded to lexical-only");

        MdbxResourceImporterOptions projection_options;
        projection_options.context = context;
        projection_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        use_test_clock(projection_options, 100);
        MdbxResourceImporter projection_importer{std::move(projection_options)};
        bool mixed_profile_rejected = false;
        try {
            (void)projection_importer.import(original(
                "resource:profile", "https://example.test/profile", "profile-bound source"
            ));
        } catch(const std::runtime_error&) { mixed_profile_rejected = true; }
        expect(mixed_profile_rejected,
               "projection profile silently rewrote a raw-only manifest");
    }
    std::filesystem::remove(profile_path, error);

    const auto legacy_v3_path = test_path("legacy_v3_intent");
    std::filesystem::remove(legacy_v3_path, error);
    {
        const auto context = MdbxStorageContext::open(legacy_v3_path.string());
        mdbxc::KeyValueTable<std::string, std::string> intents(
            context->connection(), "agent_memory_resource_import_intents"
        );
        mdbxc::KeyValueTable<std::string, std::string> bindings(
            context->connection(), "agent_memory_resource_import_bindings"
        );
        mdbxc::KeyValueTable<std::string, std::string> epochs(
            context->connection(), "agent_memory_resource_import_epochs"
        );
        const auto payload = legacy_v3_intent(
            "legacy-owner", "tenant-a", "resource:legacy-v3",
            "https://example.test/legacy-v3", "legacy v3 body", 1, 100
        );
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        expect(bindings.insert("resource:legacy-v3", "tenant-a", txn),
               "legacy v3 fixture binding was not created");
        expect(epochs.insert("resource:legacy-v3", "1", txn),
               "legacy v3 fixture epoch was not created");
        expect(intents.insert("resource:legacy-v3", payload, txn),
               "legacy v3 fixture intent was not created");
        txn.commit();
        MdbxResourceImporterOptions options;
        options.context = context;
        use_test_clock(options, 1'000);
        bool rejected = false;
        try {
            MdbxResourceImporter importer{std::move(options)};
        } catch(const std::runtime_error& error_value) {
            rejected = std::string_view{error_value.what()}.find(
                "quiesced offline upgrade"
            ) != std::string_view::npos;
        }
        expect(rejected, "legacy v3 intent was not held for offline token upgrade");
    }
    std::filesystem::remove(legacy_v3_path, error);

    const auto binding_a_path = test_path("binding_a");
    const auto binding_b_path = test_path("binding_b");
    std::filesystem::remove(binding_a_path, error);
    std::filesystem::remove(binding_b_path, error);
    {
        MdbxResourceImporterOptions options;
        options.path = binding_a_path.string();
        options.table_prefix = "profile_a";
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        MdbxResourceImporter importer{std::move(options)};
        const auto result = importer.import(original(
            "resource:binding", "https://example.test/binding", "binding source"
        ));
        expect(result.raw_units.size() == 1, "binding fixture did not publish its unit");

        const auto context_b = MdbxStorageContext::open(binding_b_path.string());
        MdbxKnowledgeUnitStoreOptions store_b_options;
        store_b_options.context = context_b;
        store_b_options.table_prefix = "profile_a";
        MdbxKnowledgeUnitStore store_b{std::move(store_b_options)};
        expect(store_b.scan_by_scope_and_kind(
                    ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                ).empty(),
               "raw units leaked across MDBX environments");
    }
    std::filesystem::remove(binding_a_path, error);
    std::filesystem::remove(binding_b_path, error);

    const auto pending_profile_path = test_path("pending_profile");
    std::filesystem::remove(pending_profile_path, error);
    {
        const auto context = MdbxStorageContext::open(pending_profile_path.string());
        MdbxResourceImporterOptions raw_options;
        raw_options.context = context;
        raw_options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(raw_options, 100);
        raw_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterIntent) {
                throw std::runtime_error("leave raw profile pending");
            }
        };
        MdbxResourceImporter raw{std::move(raw_options)};
        bool interrupted = false;
        try {
            (void)raw.import(original(
                "resource:pending-profile",
                "https://example.test/pending-profile",
                "pending profile source"
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "raw profile pending fixture did not interrupt");

        MdbxResourceImporterOptions lexical_options;
        lexical_options.context = context;
        use_test_clock(lexical_options, 1'000);
        bool mismatch_rejected = false;
        try {
            MdbxResourceImporter lexical{std::move(lexical_options)};
        } catch(const std::runtime_error&) { mismatch_rejected = true; }
        expect(mismatch_rejected,
               "unwired recovery completed a pending raw publication");
    }
    std::filesystem::remove(pending_profile_path, error);

    const auto recovery_admission_path = test_path("recovery_admission");
    std::filesystem::remove(recovery_admission_path, error);
    {
        const auto context = MdbxStorageContext::open(recovery_admission_path.string());
        MdbxResourceImporterOptions writer_options;
        writer_options.context = context;
        writer_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawChunks;
        writer_options.max_raw_body_bytes = 4'096;
        use_test_clock(writer_options, 100);
        writer_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterIntent) {
                throw std::runtime_error("leave admission intent pending");
            }
        };
        MdbxResourceImporter writer{std::move(writer_options)};
        bool interrupted = false;
        try {
            (void)writer.import(original(
                "resource:recovery-admission",
                "https://example.test/recovery-admission",
                std::string(2'048, 'a')
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "admission recovery fixture did not leave an intent");

        MdbxResourceImporterOptions tighter_options;
        tighter_options.context = context;
        tighter_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawChunks;
        tighter_options.max_raw_body_bytes = 1'024;
        use_test_clock(tighter_options, 1'000);
        bool tighter_rejected = false;
        try {
            MdbxResourceImporter tighter{std::move(tighter_options)};
        } catch(const std::invalid_argument&) { tighter_rejected = true; }
        expect(tighter_rejected,
               "recovery ignored the current raw body admission contract");
    }
    std::filesystem::remove(recovery_admission_path, error);

    const auto raw_fault_path = test_path("raw_fault");
    std::filesystem::remove(raw_fault_path, error);
    {
        const auto context = MdbxStorageContext::open(raw_fault_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        options.raw_chunk_batch_size = 2;
        use_test_clock(options, 100);
        int raw_writes = 0;
        options.fault_injector = [&](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterRawUnitPublication &&
               ++raw_writes == 1) {
                throw std::runtime_error("interrupt after raw batch");
            }
        };
        MdbxResourceImporter importer{std::move(options)};
        std::string body(3'100, 'x');
        bool interrupted = false;
        try {
            (void)importer.import(original(
                "resource:raw-fault", "https://example.test/raw-fault", body
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "raw fault did not interrupt after the first batch");
        expect(!importer.find_manifest(ResourceId{"resource:raw-fault"}),
               "partial raw batch published a manifest");
        expect(store->scan_by_scope_and_kind(
                    ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                ).size() == 2,
               "raw fault fixture did not leave exactly two durable staging records");
    }
    {
        const auto context = MdbxStorageContext::open(raw_fault_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(options, 1'000);
        MdbxResourceImporter importer{std::move(options)};
        const auto manifest = importer.find_manifest(ResourceId{"resource:raw-fault"});
        expect(manifest && manifest->records.size() == 6,
               "raw recovery did not publish the complete four-unit manifest");
        expect(store->scan_by_scope_and_kind(
                    ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                ).size() == 4,
               "raw recovery allocated duplicate or incomplete units");
    }
    std::filesystem::remove(raw_fault_path, error);

    for(const int fault_after_batch : {1, 2}) {
        const auto boundary_path = test_path(
            (std::string{"raw_batch_boundary_"} + std::to_string(fault_after_batch)).c_str()
        );
        std::filesystem::remove(boundary_path, error);
        std::string boundary_body;
        boundary_body.reserve(257U * kMdbxResourceImporterRawChunkBytes);
        for(std::size_t index = 0; index < 257U; ++index) {
            boundary_body.append(
                kMdbxResourceImporterRawChunkBytes,
                static_cast<char>('A' + (index % 26U))
            );
        }
        {
            const auto context = MdbxStorageContext::open(boundary_path.string());
            MdbxResourceImporterOptions options;
            options.context = context;
            options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
            use_test_clock(options, 100);
            int raw_writes = 0;
            options.fault_injector = [&](ResourceImporterFaultPoint point) {
                if(point == ResourceImporterFaultPoint::AfterRawUnitPublication &&
                   ++raw_writes == fault_after_batch) {
                    throw std::runtime_error("interrupt at default batch boundary");
                }
            };
            MdbxResourceImporter importer{std::move(options)};
            bool interrupted = false;
            try {
                (void)importer.import(original(
                    "resource:raw-batch-boundary",
                    "https://example.test/raw-batch-boundary",
                    boundary_body
                ));
            } catch(const std::runtime_error&) { interrupted = true; }
            expect(interrupted, "default batch boundary fixture did not interrupt");
            expect(!importer.find_manifest(ResourceId{"resource:raw-batch-boundary"}),
                   "default batch fault published a manifest before recovery");
            MdbxKnowledgeUnitStoreOptions store_options;
            store_options.context = context;
            MdbxKnowledgeUnitStore store(std::move(store_options));
            expect(store.scan_by_scope_and_kind(
                       ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                   ).size() == static_cast<std::size_t>(fault_after_batch) * 128U,
                   "default batch fault committed an unexpected number of units");
        }
        {
            const auto context = MdbxStorageContext::open(boundary_path.string());
            MdbxKnowledgeUnitStoreOptions store_options;
            store_options.context = context;
            MdbxKnowledgeUnitStore store(std::move(store_options));
            MdbxResourceImporterOptions options;
            options.context = context;
            options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
            use_test_clock(options, 1'000);
            MdbxResourceImporter importer{std::move(options)};
            const auto recovered = importer.import(original(
                "resource:raw-batch-boundary",
                "https://example.test/raw-batch-boundary",
                boundary_body
            ));
            expect(recovered.raw_units.size() == 257U,
                   "default batch recovery did not retain all 257 units");
            const auto manifest = importer.find_manifest(
                ResourceId{"resource:raw-batch-boundary"}
            );
            expect(manifest && manifest->state == ResourceManifestState::Active &&
                       manifest->records.size() == 259U,
                   "default batch recovery published an incomplete manifest");
            std::size_t custom_bindings = 0;
            for(const auto& record : manifest->records) {
                if(record.kind != DerivedRecordKind::Custom) continue;
                ++custom_bindings;
                expect(record.key.rfind("knowledge_unit:", 0) == 0,
                       "default batch manifest contains an unexpected custom binding");
            }
            expect(custom_bindings == recovered.raw_units.size(),
                   "default batch manifest custom binding count is incomplete");
            for(std::size_t index = 0; index < recovered.raw_units.size(); ++index) {
                const auto envelope = store.find(recovered.raw_units[index]);
                expect(envelope.has_value(), "default batch recovery lost a raw unit");
                expect(envelope->primary_text == boundary_body.substr(
                           index * kMdbxResourceImporterRawChunkBytes,
                           kMdbxResourceImporterRawChunkBytes
                       ),
                       "default batch recovery changed raw unit content or order");
                expect(envelope->sources.size() == 1 &&
                       envelope->sources.front().excerpt.offset ==
                               index * kMdbxResourceImporterRawChunkBytes,
                       "default batch recovery changed raw unit provenance order");
                const auto binding = std::string{"knowledge_unit:"} +
                    std::to_string(recovered.raw_units[index].value());
                std::size_t binding_matches = 0;
                for(const auto& record : manifest->records) {
                    if(record.kind == DerivedRecordKind::Custom && record.key == binding) {
                        ++binding_matches;
                        expect(record.ordinal == index,
                               "default batch manifest binding ordinal changed");
                    }
                }
                expect(binding_matches == 1,
                       "default batch manifest binding is missing or duplicated");
            }
            const auto repeated = importer.import(original(
                "resource:raw-batch-boundary",
                "https://example.test/raw-batch-boundary",
                boundary_body
            ));
            expect(repeated.raw_units == recovered.raw_units,
                   "default batch replay allocated duplicate raw unit IDs");
            expect(store.scan_by_scope_and_kind(
                       ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                   ).size() == 257U,
                   "default batch replay changed the durable unit count");
        }
        expect(std::filesystem::remove(boundary_path, error) ||
                   !std::filesystem::exists(boundary_path),
               "default batch boundary database could not be removed");
    }

    const auto raw_takeover_path = test_path("raw_takeover");
    std::filesystem::remove(raw_takeover_path, error);
    {
        const auto context = MdbxStorageContext::open(raw_takeover_path.string());
        std::mutex coordination_mutex;
        std::condition_variable coordination;
        bool paused_after_raw = false;
        bool resume_stale = false;
        bool stale_rejected = false;
        MdbxResourceImporterOptions stale_options;
        stale_options.context = context;
        stale_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(stale_options, 100);
        stale_options.fault_injector = [&](ResourceImporterFaultPoint point) {
            if(point != ResourceImporterFaultPoint::AfterRawUnitPublication) return;
            std::unique_lock<std::mutex> lock(coordination_mutex);
            if(paused_after_raw) return;
            paused_after_raw = true;
            coordination.notify_all();
            coordination.wait(lock, [&] { return resume_stale; });
        };
        MdbxResourceImporter stale{std::move(stale_options)};
        std::thread stale_thread([&] {
            try {
                (void)stale.import(original(
                    "resource:raw-takeover",
                    "https://example.test/raw-takeover",
                    std::string(2'500, 'z')
                ));
            } catch(const std::runtime_error&) { stale_rejected = true; }
        });
        {
            std::unique_lock<std::mutex> lock(coordination_mutex);
            coordination.wait(lock, [&] { return paused_after_raw; });
        }
        MdbxResourceImporterOptions successor_options;
        successor_options.context = context;
        successor_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(successor_options, 1'000);
        MdbxResourceImporter successor{std::move(successor_options)};
        const auto recovered = successor.find_manifest(ResourceId{"resource:raw-takeover"});
        {
            std::lock_guard<std::mutex> lock(coordination_mutex);
            resume_stale = true;
        }
        coordination.notify_all();
        stale_thread.join();
        expect(stale_rejected,
               "stale writer was not fenced during raw publication");
        expect(recovered && recovered->records.size() == 5,
               "raw takeover did not recover a complete manifest");
    }
    std::filesystem::remove(raw_takeover_path, error);

    const auto admission_path = test_path("raw_admission");
    std::filesystem::remove(admission_path, error);
    {
        MdbxResourceImporterOptions options;
        options.path = admission_path.string();
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        options.max_raw_body_bytes = 3;
        MdbxResourceImporter importer{std::move(options)};
        bool rejected = false;
        try {
            (void)importer.import(original(
                "resource:too-large", "https://example.test/too-large", "four"
            ));
        } catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "raw body admission limit was not enforced before publication");
    }
    std::filesystem::remove(admission_path, error);

    bool ordinal_limit_rejected = false;
    const auto ordinal_path = test_path("raw_ordinal_overflow");
    std::filesystem::remove(ordinal_path, error);
    try {
        MdbxResourceImporterOptions invalid_options;
        invalid_options.path = ordinal_path.string();
        invalid_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawChunks;
        invalid_options.max_raw_chunks =
            static_cast<std::size_t>((std::numeric_limits<std::uint32_t>::max)()) + 1U;
        MdbxResourceImporter invalid{std::move(invalid_options)};
    } catch(const std::invalid_argument&) { ordinal_limit_rejected = true; }
    std::filesystem::remove(ordinal_path, error);
    expect(ordinal_limit_rejected,
           "raw chunk admission accepted a manifest ordinal overflow bound");

    for(const auto invalid_batch_size : {std::size_t{0},
                                         kMdbxKnowledgeUnitMaxBatchSize + 1U}) {
        const auto batch_path = test_path(
            (std::string{"raw_batch_size_"} + std::to_string(invalid_batch_size)).c_str()
        );
        std::filesystem::remove(batch_path, error);
        bool rejected = false;
        try {
            MdbxResourceImporterOptions invalid_options;
            invalid_options.path = batch_path.string();
            invalid_options.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawChunks;
            invalid_options.raw_chunk_batch_size = invalid_batch_size;
            MdbxResourceImporter invalid{std::move(invalid_options)};
        } catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "raw chunk batch admission accepted an invalid bound");
        std::filesystem::remove(batch_path, error);
    }

    bool unknown_profile_rejected = false;
    const auto unknown_profile_path = test_path("unknown_profile");
    std::filesystem::remove(unknown_profile_path, error);
    try {
        MdbxResourceImporterOptions invalid_options;
        invalid_options.path = unknown_profile_path.string();
        invalid_options.publication_profile =
            static_cast<ResourceImportPublicationProfile>(255);
        MdbxResourceImporter invalid{std::move(invalid_options)};
    } catch(const std::invalid_argument&) { unknown_profile_rejected = true; }
    std::filesystem::remove(unknown_profile_path, error);
    expect(unknown_profile_rejected,
           "unknown publication profile was accepted by the importer");

    const auto boundary_path = test_path("raw_boundaries");
    std::filesystem::remove(boundary_path, error);
    {
        const auto context = MdbxStorageContext::open(boundary_path.string());
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        options.max_raw_body_bytes = 1'025;
        options.max_raw_chunks = 2;
        MdbxResourceImporter importer{std::move(options)};
        std::string body(1'023, 'x');
        body.append("\xC3\xA9");
        const auto result = importer.import(original(
            "resource:raw-boundary", "https://example.test/raw-boundary", body
        ));
        expect(result.raw_units.size() == 2,
               "UTF-8 boundary fixture did not produce two bounded chunks");
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        MdbxKnowledgeUnitStore store{std::move(store_options)};
        const auto first = store.find(result.raw_units.front());
        const auto second = store.find(result.raw_units.back());
        expect(first && second && first->primary_text.size() == 1'023 &&
                   second->primary_text == "\xC3\xA9",
               "UTF-8 boundary split bisected a code point");

        MdbxResourceImporterOptions chunk_limit_options;
        chunk_limit_options.context = context;
        chunk_limit_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawChunks;
        chunk_limit_options.max_raw_body_bytes = 1'025;
        chunk_limit_options.max_raw_chunks = 1;
        MdbxResourceImporter chunk_limited{std::move(chunk_limit_options)};
        bool chunk_limit_rejected = false;
        try {
            (void)chunk_limited.import(original(
                "resource:raw-boundary-rejected",
                "https://example.test/raw-boundary-rejected",
                body
            ));
        } catch(const std::invalid_argument&) { chunk_limit_rejected = true; }
        expect(chunk_limit_rejected,
               "raw chunk-count limit did not reject before durable writes");
    }
    std::filesystem::remove(boundary_path, error);

    const auto lifecycle_path = test_path("raw_lifecycle");
    std::filesystem::remove(lifecycle_path, error);
    {
        const auto context = MdbxStorageContext::open(lifecycle_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        MdbxResourceImporter importer{std::move(options)};
        const auto lifecycle_case = [&](const char* id,
                                        KnowledgeUnitLifecycleState state) {
            const auto result = importer.import(original(
                id, "https://example.test/lifecycle", "lifecycle source"
            ));
            auto unit = store->find(result.raw_units.front());
            expect(unit.has_value(), "lifecycle fixture lost its raw unit");
            static_cast<void>(store->update_mutable_fields(
                unit->id,
                KnowledgeUnitMutablePatch{std::nullopt, std::nullopt, state, std::nullopt, std::nullopt}
            ));
            bool rejected = false;
            try {
                (void)importer.import(original(
                    id, "https://example.test/lifecycle", "lifecycle source"
                ));
            } catch(const std::runtime_error&) { rejected = true; }
            expect(rejected, "non-active lifecycle unit was silently republished");
        };
        lifecycle_case("resource:deprecated", KnowledgeUnitLifecycleState::Deprecated);
        lifecycle_case("resource:superseded", KnowledgeUnitLifecycleState::Superseded);
        lifecycle_case("resource:erased", KnowledgeUnitLifecycleState::Erased);
    }
    std::filesystem::remove(lifecycle_path, error);
    {
        const auto context = MdbxStorageContext::open(wired_recovery_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(options, 100);
        options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterLexicalPublication) {
                throw std::runtime_error("leave wired import pending");
            }
        };
        MdbxResourceImporter importer{std::move(options)};
        bool interrupted = false;
        try {
            (void)importer.import(original(
                "resource:wired-recovery",
                "https://example.test/wired-recovery",
                "wired recovery source with durable units"
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "wired recovery fixture did not interrupt publication");
        expect(store->scan_by_scope_and_kind(
                    ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                ).empty(),
               "raw units were published before the wired recovery point");
    }
    {
        const auto context = MdbxStorageContext::open(wired_recovery_path.string());
        MdbxKnowledgeUnitStoreOptions store_options;
        store_options.context = context;
        auto store = std::make_shared<MdbxKnowledgeUnitStore>(std::move(store_options));
        MdbxResourceImporterOptions options;
        options.context = context;
        options.publication_profile = ResourceImportPublicationProfile::LexicalWithRawChunks;
        use_test_clock(options, 1'000);
        MdbxResourceImporter importer{std::move(options)};
        const auto recovered = importer.find_manifest(ResourceId{"resource:wired-recovery"});
        expect(recovered && recovered->records.size() == 3,
               "wired recovery did not publish the manifest binding");
        expect(store->scan_by_scope_and_kind(
                    ScopeId{"tenant-a"}, KnowledgeUnitKind::Chunk
                ).size() == 1,
               "wired recovery did not create the raw unit");
    }
    std::filesystem::remove(wired_recovery_path, error);
    {
        const auto context = MdbxStorageContext::open(concurrency_path.string());
        MdbxResourceImporterOptions first_options;
        first_options.context = context;
        use_test_clock(first_options, 100);
        first_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterIntent) {
                throw std::runtime_error("leave pending intent");
            }
        };
        MdbxResourceImporter first{std::move(first_options)};
        bool first_interrupted = false;
        try {
            (void)first.import(original(
                "resource:concurrent", "https://example.test/concurrent", "concurrent source"
            ));
        } catch(const std::runtime_error&) { first_interrupted = true; }
        expect(first_interrupted, "concurrency fixture did not leave pending intent");
        MdbxResourceImporterOptions second_options;
        second_options.context = context;
        std::uint64_t second_now = 150;
        second_options.ownership_lease_ms = 100;
        second_options.clock_ms = [&second_now] { return second_now; };
        MdbxResourceImporter second{std::move(second_options)};
        expect(!second.find_manifest(ResourceId{"resource:concurrent"}),
               "opening another importer revoked an unexpired owner lease");
        bool foreign_rejected = false;
        try {
            (void)second.import(original(
                "resource:concurrent", "https://example.test/other", "different source"
            ));
        } catch(const std::runtime_error&) { foreign_rejected = true; }
        expect(foreign_rejected, "foreign concurrent payload replaced pending intent");
        bool same_payload_rejected = false;
        try {
            (void)second.import(original(
                "resource:concurrent", "https://example.test/concurrent", "concurrent source"
            ));
        } catch(const std::runtime_error&) { same_payload_rejected = true; }
        expect(same_payload_rejected,
               "same payload was allowed to execute without owning the live lease");
        second_now = 1'000;
        const auto recovered_result = second.import(original(
            "resource:concurrent", "https://example.test/other", "new concurrent source"
        ));
        expect(recovered_result.revision.generation == 2,
               "an existing executor did not recover P before importing Q on demand");
        const auto completed = second.find_manifest(ResourceId{"resource:concurrent"});
        expect(completed && completed->revision.generation == 2,
               "on-demand recovery did not publish the replacement payload");
    }
    std::filesystem::remove(concurrency_path, error);

    const auto fencing_path = test_path("publication_fencing");
    std::filesystem::remove(fencing_path, error);
    {
        const auto context = MdbxStorageContext::open(fencing_path.string());
        std::mutex coordination_mutex;
        std::condition_variable coordination;
        bool paused_before_lexical = false;
        bool resume_stale_writer = false;
        bool stale_writer_fenced = false;

        MdbxResourceImporterOptions stale_options;
        stale_options.context = context;
        use_test_clock(stale_options, 100);
        stale_options.fault_injector = [&](ResourceImporterFaultPoint point) {
            if(point != ResourceImporterFaultPoint::BeforeLexicalPublication) return;
            std::unique_lock<std::mutex> lock(coordination_mutex);
            paused_before_lexical = true;
            coordination.notify_all();
            coordination.wait(lock, [&] { return resume_stale_writer; });
        };
        MdbxResourceImporter stale{std::move(stale_options)};
        std::thread stale_thread([&] {
            try {
                (void)stale.import(original(
                    "resource:fenced",
                    "https://example.test/fenced-p",
                    "obsoletealpha"
                ));
            } catch(const std::runtime_error&) {
                stale_writer_fenced = true;
            }
        });

        {
            std::unique_lock<std::mutex> lock(coordination_mutex);
            coordination.wait(lock, [&] { return paused_before_lexical; });
        }

        MdbxResourceImporterOptions successor_options;
        successor_options.context = context;
        use_test_clock(successor_options, 1'000);
        MdbxResourceImporter successor{std::move(successor_options)};
        const auto recovered = successor.find_manifest(ResourceId{"resource:fenced"});
        expect(recovered && recovered->revision.generation == 1,
               "expired owner recovery did not publish payload P");
        const auto latest = successor.import(original(
            "resource:fenced",
            "https://example.test/fenced-q",
            "currentomega"
        ));
        expect(latest.revision.generation == 2,
               "successor did not publish payload Q after recovery");

        {
            std::lock_guard<std::mutex> lock(coordination_mutex);
            resume_stale_writer = true;
        }
        coordination.notify_all();
        stale_thread.join();
        expect(stale_writer_fenced,
               "stale executor was not rejected at lexical publication");
        MdbxCanonicalContentStore canonical(MdbxCanonicalContentStoreOptions{
            {}, "agent_memory", false, kDefaultMdbxMaxDbs, context
        });
        const auto current_canonical = canonical.read_current(DocumentId{
            canonical_document_id("tenant-a", "resource:fenced")
        });
        expect(current_canonical && !current_canonical->blocks.empty() &&
                   current_canonical->blocks.front().text == "currentomega",
               "stale executor changed the active canonical head after fencing");
        const auto repeated = successor.import(original(
            "resource:fenced",
            "https://example.test/fenced-q",
            "currentomega"
        ));
        expect(repeated.revision.generation == 2,
               "idempotent resource import did not publish its current manifest generation");
        const auto after_repeat = canonical.read_current(DocumentId{
            canonical_document_id("tenant-a", "resource:fenced")
        });
        expect(after_repeat && after_repeat->revision == current_canonical->revision,
               "idempotent payload advanced the canonical revision");
        expect(successor.search(ScopeId{"tenant-a"}, "currentomega", 5).size() == 1,
               "stale executor replaced payload Q in lexical search");
        expect(successor.search(ScopeId{"tenant-a"}, "obsoletealpha", 5).empty(),
               "stale payload P remained active after payload Q");

        MdbxResourceImporterOptions reopen_options;
        reopen_options.context = context;
        use_test_clock(reopen_options, 1'000);
        MdbxResourceImporter reopened{std::move(reopen_options)};
        const auto reopened_manifest = reopened.find_manifest(ResourceId{"resource:fenced"});
        expect(reopened_manifest && reopened_manifest->revision.generation == 2,
               "reopen lost the successor manifest generation");
        expect(reopened.search(ScopeId{"tenant-a"}, "currentomega", 5).size() == 1,
               "reopen did not preserve payload Q lexical visibility");
        const auto reopened_canonical = canonical.read_current(DocumentId{
            canonical_document_id("tenant-a", "resource:fenced")
        });
        expect(reopened_canonical && !reopened_canonical->blocks.empty() &&
                   reopened_canonical->blocks.front().text == "currentomega",
               "reopen changed the fenced canonical head");
    }
    std::filesystem::remove(fencing_path, error);

    std::filesystem::remove(path, error);
    std::filesystem::remove(recovery_path, error);
    return 0;
}

#else
int main() { return 0; }
#endif
