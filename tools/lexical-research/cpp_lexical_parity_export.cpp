#include <agent_memory.hpp>

#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace {

using agent_memory::ChunkId;
using agent_memory::ExactLexicalIndex;
using agent_memory::LexicalDocumentRecord;
using agent_memory::LexicalSearchQuery;
using agent_memory::Metadata;
using agent_memory::ResourceId;
using agent_memory::ResourceRevision;
using agent_memory::StandardTokenizer;
using agent_memory::Token;
using agent_memory::TokenizeOptions;

std::string json_quote(const std::string_view value) {
    std::ostringstream output;
    output << '"';
    for(const unsigned char character : value) {
        switch(character) {
        case '"': output << "\\\""; break;
        case '\\': output << "\\\\"; break;
        case '\b': output << "\\b"; break;
        case '\f': output << "\\f"; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if(character < 0x20U) {
                output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<unsigned int>(character) << std::dec;
            } else {
                output << static_cast<char>(character);
            }
            break;
        }
    }
    output << '"';
    return output.str();
}

void emit_token(std::ostream& output, const Token& token) {
    output << "{\"text\":" << json_quote(token.text)
           << ",\"kind\":" << json_quote(agent_memory::to_string(token.kind))
           << ",\"offset\":" << token.source_range.offset
           << ",\"length\":" << token.source_range.length
           << ",\"position\":" << token.position << '}';
}

void emit_tokens(std::ostream& output, const agent_memory::TokenizationResult& result) {
    output << '[';
    for(std::size_t index = 0; index < result.tokens.size(); ++index) {
        if(index != 0) output << ',';
        emit_token(output, result.tokens[index]);
    }
    output << ']';
}

LexicalDocumentRecord make_record(
    const std::string_view chunk_id,
    const std::string_view resource_id,
    const std::string_view scope,
    std::vector<Token> tokens
) {
    Metadata metadata;
    metadata.set("scope", std::string{scope});
    return LexicalDocumentRecord{
        ChunkId{std::string{chunk_id}},
        ResourceRevision{ResourceId{std::string{resource_id}}, 1, 1, 1},
        std::move(tokens),
        std::move(metadata)
    };
}

void emit_results(std::ostream& output, const std::vector<agent_memory::LexicalSearchResult>& results) {
    output << '[';
    for(std::size_t index = 0; index < results.size(); ++index) {
        if(index != 0) output << ',';
        output << "{\"chunk_id\":" << json_quote(results[index].chunk_id.value())
               << ",\"score\":" << std::setprecision(9) << results[index].score << '}';
    }
    output << ']';
}

} // namespace

int main() {
    const StandardTokenizer tokenizer;
    const std::vector<std::pair<std::string, std::string>> token_cases{
        {"empty", ""},
        {"camel_case", "HTTPServerResponse"},
        {"snake_case", "snake_case_value"},
        {"cpp_identifier", "std::vector<ChunkId>"},
        {"windows_path", "C:\\Users\\User\\repo"},
        {"posix_path", "/var/lib/agent-memory"},
        {"cli_flags", "--dry-run -Wall"},
        {"unicode", "Индекс памяти"},
        {"ascii_lowercase", "MixedCASE"},
        {"repeated_terms", "agent agent memory"},
        {"symbols", "a+b!=c"}
    };

    std::cout << "{\"schema_version\":1,\"tokenizer\":\"StandardTokenizer\",\"cases\":[";
    for(std::size_t index = 0; index < token_cases.size(); ++index) {
        if(index != 0) std::cout << ',';
        const auto& item = token_cases[index];
        const auto result = tokenizer.tokenize(item.second);
        std::cout << "{\"name\":" << json_quote(item.first)
                  << ",\"input\":" << json_quote(item.second)
                  << ",\"tokens\":";
        emit_tokens(std::cout, result);
        std::cout << "}";
    }
    const auto symbol_result = tokenizer.tokenize("a+b!=c", TokenizeOptions{true, true, true});
    std::cout << ", {\"name\":\"symbols_with_emission\",\"input\":\"a+b!=c\",\"tokens\":";
    emit_tokens(std::cout, symbol_result);
    std::cout << "}],\"index\":{";

    ExactLexicalIndex index;
    const auto d1 = tokenizer.tokenize("agent memory agent system");
    const auto d2 = tokenizer.tokenize("memory system");
    const auto d3 = tokenizer.tokenize("agent");
    index.upsert(make_record("chunk:a", "resource:alpha", "public", d1.tokens));
    index.upsert(make_record("chunk:b", "resource:alpha", "private", d2.tokens));
    index.upsert(make_record("chunk:c", "resource:beta", "public", d3.tokens));

    LexicalSearchQuery query;
    query.terms = {"agent", "memory"};
    query.limit = 10;
    const auto initial_results = index.search(query);
    LexicalSearchQuery filtered;
    filtered.terms = {"system"};
    filtered.limit = 10;
    filtered.metadata_filters = {agent_memory::MetadataFilter{"scope", "public"}};
    const auto filtered_results = index.search(filtered);

    index.upsert(make_record("chunk:a", "resource:alpha", "public", tokenizer.tokenize("updated").tokens));
    const auto after_update = index.search(query);
    const auto erased = index.erase(ChunkId{"chunk:b"});
    const auto after_erase_size = index.size();

    std::cout << "\"documents\":[{\"chunk_id\":\"chunk:a\",\"resource_id\":\"resource:alpha\",\"scope\":\"public\",\"tokens\":[\"agent\",\"memory\",\"agent\",\"system\"]},"
                 "{\"chunk_id\":\"chunk:b\",\"resource_id\":\"resource:alpha\",\"scope\":\"private\",\"tokens\":[\"memory\",\"system\"]},"
                 "{\"chunk_id\":\"chunk:c\",\"resource_id\":\"resource:beta\",\"scope\":\"public\",\"tokens\":[\"agent\"]}],";
    std::cout << "\"queries\":[{\"name\":\"agent_memory\",\"terms\":[\"agent\",\"memory\"],\"results\":";
    emit_results(std::cout, initial_results);
    std::cout << "},{\"name\":\"system_public\",\"terms\":[\"system\"],\"results\":";
    emit_results(std::cout, filtered_results);
    std::cout << "}],\"lifecycle\":{\"after_update_results\":";
    emit_results(std::cout, after_update);
    std::cout << ",\"erase_chunk_b\":" << (erased ? "true" : "false")
              << ",\"size_after_erase\":" << after_erase_size << "}}}\n";
}
