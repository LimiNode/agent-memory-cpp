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

struct FixtureDocument final {
    std::string chunk_id;
    std::string resource_id;
    std::string scope;
    std::string language;
    std::string text;
    agent_memory::TokenizationResult tokenization;
};

struct FixtureQuery final {
    std::string name;
    std::string text;
    std::vector<std::string> terms;
    std::vector<agent_memory::MetadataFilter> metadata_filters;
};

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
    const FixtureDocument& document,
    std::vector<Token> tokens
) {
    Metadata metadata;
    metadata.set("scope", document.scope);
    metadata.set("lang", document.language);
    return LexicalDocumentRecord{
        ChunkId{document.chunk_id},
        ResourceRevision{ResourceId{document.resource_id}, 1, 1, 1},
        std::move(tokens),
        std::move(metadata)
    };
}

FixtureDocument make_document(
    const StandardTokenizer& tokenizer,
    const std::string_view chunk_id,
    const std::string_view resource_id,
    const std::string_view scope,
    const std::string_view language,
    const std::string_view text
) {
    return FixtureDocument{
        std::string{chunk_id},
        std::string{resource_id},
        std::string{scope},
        std::string{language},
        std::string{text},
        tokenizer.tokenize(text)
    };
}

FixtureQuery make_query(
    const StandardTokenizer& tokenizer,
    const std::string_view name,
    const std::string_view text,
    std::vector<agent_memory::MetadataFilter> metadata_filters = {}
) {
    const auto tokenization = tokenizer.tokenize(text);
    std::vector<std::string> terms;
    terms.reserve(tokenization.tokens.size());
    for(const auto& token : tokenization.tokens) {
        terms.push_back(token.text);
    }
    return FixtureQuery{
        std::string{name},
        std::string{text},
        std::move(terms),
        std::move(metadata_filters)
    };
}

LexicalSearchQuery to_search_query(const FixtureQuery& fixture) {
    LexicalSearchQuery query;
    query.terms = fixture.terms;
    query.limit = 10;
    query.metadata_filters = fixture.metadata_filters;
    return query;
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

void emit_filters(
    std::ostream& output,
    const std::vector<agent_memory::MetadataFilter>& filters
) {
    output << '[';
    for(std::size_t index = 0; index < filters.size(); ++index) {
        if(index != 0) output << ',';
        output << "{\"key\":" << json_quote(filters[index].key)
               << ",\"value\":" << json_quote(filters[index].value) << '}';
    }
    output << ']';
}

void emit_document(std::ostream& output, const FixtureDocument& document) {
    output << "{\"chunk_id\":" << json_quote(document.chunk_id)
           << ",\"resource_id\":" << json_quote(document.resource_id)
           << ",\"scope\":" << json_quote(document.scope)
           << ",\"lang\":" << json_quote(document.language)
           << ",\"text\":" << json_quote(document.text)
           << ",\"tokens\":";
    emit_tokens(output, document.tokenization);
    output << '}';
}

void emit_query(
    std::ostream& output,
    const FixtureQuery& query,
    const std::vector<agent_memory::LexicalSearchResult>& results
) {
    output << "{\"name\":" << json_quote(query.name)
           << ",\"text\":" << json_quote(query.text)
           << ",\"query_tokens\":";
    const StandardTokenizer tokenizer;
    emit_tokens(output, tokenizer.tokenize(query.text));
    output << ",\"terms\":[";
    for(std::size_t index = 0; index < query.terms.size(); ++index) {
        if(index != 0) output << ',';
        output << json_quote(query.terms[index]);
    }
    output << "],\"metadata_filters\":";
    emit_filters(output, query.metadata_filters);
    output << ",\"results\":";
    emit_results(output, results);
    output << '}';
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

    const std::vector<FixtureDocument> documents{
        make_document(tokenizer, "chunk:a", "resource:alpha", "public", "en", "agent memory agent system"),
        make_document(tokenizer, "chunk:b", "resource:alpha", "private", "en", "memory system"),
        make_document(tokenizer, "chunk:c", "resource:beta", "public", "ru", "agent"),
        make_document(tokenizer, "chunk:d", "resource:tie", "public", "en", "tie"),
        make_document(tokenizer, "chunk:e", "resource:tie", "public", "en", "tie"),
        make_document(tokenizer, "chunk:f", "resource:tie", "public", "fr", "tie tie")
    };
    const std::vector<FixtureQuery> queries{
        make_query(tokenizer, "agent_memory", "agent memory"),
        make_query(
            tokenizer,
            "system_public_en",
            "system",
            {agent_memory::MetadataFilter{"scope", "public"}, agent_memory::MetadataFilter{"lang", "en"}}
        ),
        make_query(tokenizer, "agent_repeated", "agent agent memory"),
        make_query(tokenizer, "tie", "tie")
    };

    ExactLexicalIndex index;
    for(const auto& document : documents) {
        index.upsert(make_record(document, document.tokenization.tokens));
    }

    std::vector<std::vector<agent_memory::LexicalSearchResult>> query_results;
    query_results.reserve(queries.size());
    for(const auto& fixture : queries) {
        query_results.push_back(index.search(to_search_query(fixture)));
    }

    const auto lifecycle_query = to_search_query(queries.front());
    index.upsert(make_record(
        make_document(tokenizer, "chunk:a", "resource:alpha", "public", "en", "updated"),
        tokenizer.tokenize("updated").tokens
    ));
    const auto after_update = index.search(lifecycle_query);
    const auto erased = index.erase(ChunkId{"chunk:b"});
    const auto after_erase = index.search(lifecycle_query);
    const auto after_erase_size = index.size();

    std::cout << "\"documents\":[";
    for(std::size_t index_value = 0; index_value < documents.size(); ++index_value) {
        if(index_value != 0) std::cout << ',';
        emit_document(std::cout, documents[index_value]);
    }
    std::cout << "],\"queries\":[";
    for(std::size_t index_value = 0; index_value < queries.size(); ++index_value) {
        if(index_value != 0) std::cout << ',';
        emit_query(std::cout, queries[index_value], query_results[index_value]);
    }
    std::cout << "],\"lifecycle\":{\"query_name\":\"agent_memory\",\"after_update_results\":";
    emit_results(std::cout, after_update);
    std::cout << ",\"after_erase_results\":";
    emit_results(std::cout, after_erase);
    std::cout << ",\"erase_chunk_b\":" << (erased ? "true" : "false")
              << ",\"size_after_erase\":" << after_erase_size << "}}}\n";
}
