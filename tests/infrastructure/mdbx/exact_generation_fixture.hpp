#pragma once
#ifndef AGENT_MEMORY_HEADER_TESTS_INFRASTRUCTURE_MDBX_EXACT_GENERATION_FIXTURE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_TESTS_INFRASTRUCTURE_MDBX_EXACT_GENERATION_FIXTURE_HPP_INCLUDED
#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace exact_fixture {
using namespace agent_memory;
inline void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
inline std::string hex(std::string_view value) {
    std::ostringstream out;
    for(unsigned char c : value) out << std::hex << std::setfill('0') << std::setw(2) << unsigned(c);
    return out.str();
}
inline std::string hash(std::string_view value) {
    const auto d = sha256(value);
    return hex({reinterpret_cast<const char*>(d.data()), d.size()});
}
inline void check(int code) {
    if(code != MDBX_SUCCESS) throw std::runtime_error(mdbx_strerror(code));
}
inline RawMarkdownProductIngestionOptions ingestion_options(
    const std::shared_ptr<MdbxStorageContext>& context, const std::filesystem::path& cas) {
    RawMarkdownProductIngestionOptions o; o.context = context; o.raw_cas_root = cas; return o;
}
inline std::vector<KnowledgeUnitId> ids(const std::shared_ptr<MdbxStorageContext>& context,
                                       const ScopeId& scope) {
    MdbxKnowledgeUnitStoreOptions o; o.context = context;
    MdbxKnowledgeUnitStore store(o); return store.scan_by_scope(scope);
}
inline void import(const std::shared_ptr<MdbxStorageContext>& context,
                   const std::filesystem::path& cas, const ScopeId& scope,
                   const char* id, const std::string& text, std::uint64_t time = 1) {
    MdbxRawMarkdownProductIngestion ingestion(ingestion_options(context, cas));
    const auto result = ingestion.import(ObservedResource{scope, ResourceId{id},
        "https://example.test/" + std::string(id) + ".md", text,
        SourceTextOrigin::OriginalText, std::nullopt, time}, time);
    expect(result.status == RawMarkdownProductImportStatus::Published, "fixture import failed");
}
inline void create(const std::shared_ptr<MdbxStorageContext>& context, const std::filesystem::path& cas) {
    import(context, cas, ScopeId{"alpha"}, "resource:a", "# Alpha\n\nRetained source alpha.\n");
    import(context, cas, ScopeId{"alpha"}, "resource:b", "# Beta\n\nRetained source beta.\n");
    import(context, cas, ScopeId{"other"}, "resource:other", "# Other\n\nSeparate scope source.\n");
}

// Snapshot all existing named DBIs except the one explicitly owned by the new
// derived profile. Hash logical key/value rows, NOT MDBX pages/DBI metadata.
// Existing indexes, source evidence and schema rows are included, not just text.
inline std::string snapshot(const std::shared_ptr<MdbxStorageContext>& context,
                            const std::filesystem::path& cas) {
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    MDBX_dbi root; check(mdbx_dbi_open(txn.handle(), nullptr, MDBX_DB_DEFAULTS, &root));
    MDBX_cursor* cursor = nullptr; check(mdbx_cursor_open(txn.handle(), root, &cursor));
    MDBX_val key{}, value{}; std::vector<std::string> names;
    int rc = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
    while(rc == MDBX_SUCCESS) {
        names.emplace_back(static_cast<const char*>(key.iov_base), key.iov_len);
        rc = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
    }
    mdbx_cursor_close(cursor); expect(rc == MDBX_NOTFOUND, "catalog cursor failed");
    std::sort(names.begin(), names.end()); std::ostringstream out;
    for(const auto& name : names) {
        if(name == "agent_memory_exact_index_generations") continue;
        MDBX_dbi dbi; check(mdbx_dbi_open(txn.handle(), name.c_str(), MDBX_DB_ACCEDE, &dbi));
        check(mdbx_cursor_open(txn.handle(), dbi, &cursor));
        Sha256Accumulator hash_rows; std::size_t count = 0;
        rc = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
        while(rc == MDBX_SUCCESS) {
            hash_rows.update(std::to_string(key.iov_len) + ":");
            hash_rows.update({static_cast<const char*>(key.iov_base), key.iov_len});
            hash_rows.update(std::to_string(value.iov_len) + ":");
            hash_rows.update({static_cast<const char*>(value.iov_base), value.iov_len}); ++count;
            rc = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
        }
        mdbx_cursor_close(cursor); expect(rc == MDBX_NOTFOUND, "row cursor failed");
        const auto d = hash_rows.finish();
        out << name << ' ' << count << ' '
            << hex({reinterpret_cast<const char*>(d.data()), d.size()}) << '\n';
    }
    txn.commit();
    std::vector<std::filesystem::path> files;
    for(const auto& entry : std::filesystem::recursive_directory_iterator(cas))
        if(entry.is_regular_file()) files.push_back(entry.path());
    std::sort(files.begin(), files.end());
    for(const auto& path : files) {
        std::ifstream input(path, std::ios::binary);
        const std::string body((std::istreambuf_iterator<char>(input)), {});
        expect(input.good() || input.eof(), "CAS snapshot read failed");
        out << "CAS " << path.lexically_relative(cas).generic_string() << ' ' << body.size() << ' '
            << hash(body) << '\n';
    }
    return out.str();
}
} // namespace exact_fixture
#endif
