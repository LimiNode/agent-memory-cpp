#include "RevisionBoundRetrievalTrace.hpp"

#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <locale>
#include <sstream>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

void append_field(std::string& output, std::string_view value) {
    output += std::to_string(value.size());
    output.push_back(':');
    output.append(value.data(), value.size());
    output.push_back('|');
}

std::string hex_digest(const std::array<std::uint8_t, 32>& bytes) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(bytes.size() * 2U);
    for(const auto byte : bytes) {
        result.push_back(digits[(byte >> 4U) & 0x0fU]);
        result.push_back(digits[byte & 0x0fU]);
    }
    return result;
}

void append_u32_hex(std::string& output, float value) {
    std::uint32_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value), "unexpected float width");
    std::memcpy(&bits, &value, sizeof(bits));
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << std::hex << std::setw(8) << std::setfill('0') << bits;
    append_field(output, stream.str());
}

std::string query_material(const RetrievalQuery& query) {
    std::string material;
    append_field(material, query.text);
    append_field(material, std::to_string(query.limit));
    append_field(material, query.embedding ? "embedding" : "no-embedding");
    if(query.embedding) {
        append_field(material, std::to_string(query.embedding->values.size()));
        for(const auto value : query.embedding->values) append_u32_hex(material, value);
    }
    std::vector<std::pair<std::string, std::string>> filters;
    filters.reserve(query.metadata_filters.size());
    for(const auto& filter : query.metadata_filters) {
        filters.emplace_back(filter.key, filter.value);
    }
    std::sort(filters.begin(), filters.end());
    append_field(material, std::to_string(filters.size()));
    for(const auto& filter : filters) {
        append_field(material, filter.first);
        append_field(material, filter.second);
    }
    return material;
}

} // namespace

RevisionBoundRetrievalTrace make_revision_bound_retrieval_trace(
    const RetrievalQuery& query,
    const ScopeId& scope_id,
    const ProjectionAwareLexicalRetrieverOptions& options,
    const RevisionBoundRetrievalResponse& response,
    bool final_admission_snapshot_consistent) {
    const auto material = query_material(query);
    const auto digest = hex_digest(sha256(material));
    RevisionBoundRetrievalTrace trace;
    trace.query_digest = digest;
    trace.query_identity = "kb0.retrieval.v1:" + digest;
    trace.scope_id = scope_id;
    trace.limit = query.limit;
    trace.max_candidates = options.max_candidates;
    trace.projection_kind = options.projection_kind;
    trace.completeness = response.completeness;
    trace.diagnostic = response.diagnostic;
    trace.candidate_count = response.candidate_count;
    trace.admitted_count = response.admitted_count;
    trace.stale_count = response.stale_count;
    trace.unavailable_count = response.unavailable_count;
    trace.final_admission_snapshot_consistent = final_admission_snapshot_consistent;
    trace.ranking_snapshot_consistent = false;
    trace.hits.reserve(response.results.size());
    for(const auto& result : response.results) {
        trace.hits.push_back(RevisionBoundRetrievalTraceHit{
            result.knowledge_unit_id,
            result.unit_revision,
            result.projection_kind,
            result.root_resource_id,
            result.root_generation,
            result.root_digest,
            result.freshness,
            result.materialization
        });
    }
    return trace;
}

} // namespace agent_memory
