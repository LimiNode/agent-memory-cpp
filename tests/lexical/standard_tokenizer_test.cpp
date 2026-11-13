#include <agent_memory.hpp>

#include <algorithm>
#include <cstdio>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {

    int fail(const std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    [[nodiscard]] std::vector<std::string> token_texts(
        const agent_memory::TokenizationResult& result
    ) {
        std::vector<std::string> texts;
        for(const auto& token : result.tokens) {
            texts.push_back(token.text);
        }
        return texts;
    }

    [[nodiscard]] bool contains_bytes(
        const std::string& text,
        const std::string_view needle
    ) {
        return text.find(needle) != std::string::npos;
    }

    // --- Group 1: empty / boundary ---

    int test_tokenize_empty_input() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("");
        if(!result.tokens.empty()) {
            return fail("empty input must produce no tokens");
        }
        return 0;
    }

    int test_tokenize_whitespace_only() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("   \t\n");
        if(!result.tokens.empty()) {
            return fail("whitespace-only input must produce no tokens");
        }
        return 0;
    }

    // --- Group 2: leading punctuation ---

    int test_tokenize_leading_dot() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize(".gitignore");
        if(result.tokens.empty() || result.tokens[0].text != ".gitignore") {
            return fail("dot-prefixed token must keep leading dot");
        }
        return 0;
    }

    int test_tokenize_leading_underscore() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("_foo");
        if(result.tokens.empty() || result.tokens[0].text != "_foo") {
            return fail("underscore-prefixed token must keep leading underscore");
        }
        return 0;
    }

    int test_tokenize_double_underscore() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("__init");
        if(result.tokens.empty() || result.tokens[0].text != "__init") {
            return fail("double-underscore token must keep leading underscores");
        }
        return 0;
    }

    // --- Group 3: leading dash (CLI flag) ---

    int test_tokenize_single_dash_flag() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("-Wall");
        if(
            result.tokens.size() != 1 ||
            result.tokens[0].text != "-wall" ||
            result.tokens[0].kind != agent_memory::TokenKind::Identifier
        ) {
            return fail("dash-prefixed flag must produce single identifier token");
        }
        return 0;
    }

    int test_tokenize_double_dash_flag() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("--flag");
        if(
            result.tokens.size() != 1 ||
            result.tokens[0].text != "--flag" ||
            result.tokens[0].kind != agent_memory::TokenKind::Identifier
        ) {
            return fail("double-dash flag must produce single identifier token");
        }
        return 0;
    }

    // --- Group 4: numbers ---

    int test_tokenize_decimal_number() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("1.5");
        if(
            result.tokens.size() != 1 ||
            result.tokens[0].text != "1.5" ||
            result.tokens[0].kind != agent_memory::TokenKind::Number
        ) {
            return fail("decimal number must stay as single number token");
        }
        return 0;
    }

    int test_tokenize_negative_decimal_number() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("-1.5");
        if(result.tokens.size() != 1 || result.tokens[0].text != "-1.5") {
            return fail("negative decimal number must stay as single token");
        }
        return 0;
    }

    // --- Group 5: Windows paths and UNC ---

    int test_tokenize_windows_path() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("C:\\Users\\foo");
        if(
            result.tokens.size() != 1 ||
            result.tokens[0].text != "c:\\users\\foo" ||
            result.tokens[0].kind != agent_memory::TokenKind::Path
        ) {
            return fail("Windows path must produce single path token");
        }
        return 0;
    }

    int test_tokenize_unc_path() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("\\\\server\\share");
        if(
            result.tokens.size() != 1 ||
            result.tokens[0].kind != agent_memory::TokenKind::Path
        ) {
            return fail("UNC path must produce single path token");
        }
        const std::string text = result.tokens[0].text;
        if(text.rfind("\\\\", 0) != 0) {
            return fail("UNC path must preserve leading backslash pair");
        }
        return 0;
    }

    // --- Group 6: trailing punctuation ---

    int test_tokenize_trailing_punctuation_default() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("Hello, world!");
        if(
            result.tokens.size() != 2 ||
            result.tokens[0].text != "hello" ||
            result.tokens[1].text != "world"
        ) {
            return fail("default options must strip trailing punctuation without emitting symbols");
        }
        return 0;
    }

    int test_tokenize_trailing_punctuation_with_symbols() {
        const agent_memory::StandardTokenizer tokenizer;
        agent_memory::TokenizeOptions options;
        options.emit_symbol_tokens = true;
        options.emit_identifier_parts = false;
        const auto result = tokenizer.tokenize("Hello, world!", options);
        if(result.tokens.size() != 4) {
            return fail("symbol mode must emit punctuation as separate tokens");
        }
        if(
            result.tokens[1].kind != agent_memory::TokenKind::Symbol ||
            result.tokens[1].text != ","
        ) {
            return fail("comma must be emitted as Symbol token");
        }
        if(
            result.tokens[3].kind != agent_memory::TokenKind::Symbol ||
            result.tokens[3].text != "!"
        ) {
            return fail("exclamation must be emitted as Symbol token");
        }
        return 0;
    }

    int test_tokenize_trailing_dot_on_path() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("docs/readme.md.");
        if(
            result.tokens.size() != 1 ||
            result.tokens[0].text != "docs/readme.md"
        ) {
            return fail("trailing dot on path must be stripped in default mode");
        }
        return 0;
    }

    int test_tokenize_only_punctuation() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("...");
        if(!result.tokens.empty()) {
            return fail("three dots must produce no tokens (all trailing punctuation)");
        }
        return 0;
    }

    // --- Group 7: CamelCase acronyms ---

    int test_tokenize_acronym_httpserver() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("HTTPServer");
        const auto texts = token_texts(result);
        const bool has_whole = std::find(texts.begin(), texts.end(), "httpserver") != texts.end();
        const bool has_http = std::find(texts.begin(), texts.end(), "http") != texts.end();
        const bool has_server = std::find(texts.begin(), texts.end(), "server") != texts.end();
        if(!has_whole) {
            return fail("HTTPServer must emit the whole identifier 'httpserver'");
        }
        if(!has_http) {
            return fail("HTTPServer must emit identifier part 'http'");
        }
        if(!has_server) {
            return fail("HTTPServer must emit identifier part 'server'");
        }
        return 0;
    }

    int test_tokenize_acronym_xmlhttprequest() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("XMLHttpRequest");
        const auto texts = token_texts(result);
        const bool has_whole = std::find(texts.begin(), texts.end(), "xmlhttprequest") != texts.end();
        const bool has_xml = std::find(texts.begin(), texts.end(), "xml") != texts.end();
        const bool has_http = std::find(texts.begin(), texts.end(), "http") != texts.end();
        const bool has_request = std::find(texts.begin(), texts.end(), "request") != texts.end();
        if(!has_whole) {
            return fail("XMLHttpRequest must emit the whole identifier 'xmlhttprequest'");
        }
        if(!has_xml) {
            return fail("XMLHttpRequest must emit identifier part 'xml'");
        }
        if(!has_http) {
            return fail("XMLHttpRequest must emit identifier part 'http'");
        }
        if(!has_request) {
            return fail("XMLHttpRequest must emit identifier part 'request'");
        }
        return 0;
    }

    // --- Group 8: 4-byte UTF-8 (emoji) ---

    int test_tokenize_emoji_opaque() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto result = tokenizer.tokenize("hello \xF0\x9F\x8E\x89 world");
        if(result.tokens.size() != 3) {
            return fail("emoji must not split the surrounding tokens into more pieces");
        }
        const std::string emoji_bytes{'\xF0', '\x9F', '\x8E', '\x89'};
        if(!contains_bytes(result.tokens[1].text, emoji_bytes)) {
            return fail("middle token must carry the emoji UTF-8 bytes intact");
        }
        return 0;
    }

    int test_tokenize_camel_fixture_is_deterministic() {
        const agent_memory::StandardTokenizer tokenizer;
        const auto input = u8"camelCase PascalCase HTTPServer version2Value, Привет!";
        const auto first = tokenizer.tokenize(input);
        const auto second = tokenizer.tokenize(input);

        if(first.tokens.size() != second.tokens.size()) {
            return fail("camel fixture must produce a deterministic token count");
        }
        for(std::size_t index = 0; index < first.tokens.size(); ++index) {
            const auto& lhs = first.tokens[index];
            const auto& rhs = second.tokens[index];
            if(lhs.text != rhs.text || lhs.source_range.offset != rhs.source_range.offset ||
               lhs.source_range.length != rhs.source_range.length ||
               lhs.position != rhs.position || lhs.kind != rhs.kind) {
                return fail("camel fixture must produce deterministic tokens and offsets");
            }
        }

        const auto texts = token_texts(first);
        const auto contains = [&texts](const std::string_view value) {
            return std::find(texts.begin(), texts.end(), value) != texts.end();
        };
        if(!contains("camelcase") || !contains("camel") || !contains("case") ||
           !contains("pascalcase") || !contains("pascal") || !contains("httpserver") ||
           !contains("http") || !contains("server") || !contains("version2value") ||
           !contains("version2") || !contains("value") || !contains(u8"Привет")) {
            return fail("camel fixture must preserve camel, acronym, numeric and UTF-8 terms");
        }

        return 0;
    }

} // namespace

int run_test(const char* name, int(*fn)()) {
    const int rc = fn();
    if(rc != 0) {
        std::fprintf(stderr, "  [FAIL] %s\n", name);
        return 1;
    }
    std::printf("  [ OK ] %s\n", name);
    return 0;
}

int main() {
    int failures = 0;

    {
        const agent_memory::StandardTokenizer tokenizer;

        const auto result = tokenizer.tokenize("Hello AGENT_MEMORY_HAS_MDBX error 11001");
        const auto texts = token_texts(result);

        const std::vector<std::string> expected{
            "hello",
            "agent_memory_has_mdbx",
            "agent",
            "memory",
            "has",
            "mdbx",
            "error",
            "11001"
        };

        if(texts != expected) {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: standard tokenizer must normalize words and split identifiers\n");
        } else if(result.tokens[1].kind != agent_memory::TokenKind::Identifier) {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: identifier token must be classified as identifier\n");
        } else if(result.tokens[7].kind != agent_memory::TokenKind::Number) {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: numeric token must be classified as number\n");
        } else if(result.tokens[0].source_range.offset != 0 || result.tokens[0].source_range.length != 5) {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: word token must preserve source byte range\n");
        } else {
            for(std::size_t index = 0; index < result.tokens.size(); ++index) {
                if(result.tokens[index].position != index) {
                    ++failures;
                    std::fprintf(stderr, "  [FAIL] smoke: token positions must match emitted order\n");
                    break;
                }
            }
        }
    }

    {
        const agent_memory::StandardTokenizer tokenizer;
        const auto path_result = tokenizer.tokenize("Open docs/readme.md");
        if(path_result.tokens.size() != 2 || path_result.tokens[1].kind != agent_memory::TokenKind::Path) {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: path-like token must be classified as path\n");
        }
    }

    {
        const agent_memory::StandardTokenizer tokenizer;
        const auto utf8_result = tokenizer.tokenize("Привет Мир");
        if(utf8_result.tokens.size() != 2 || utf8_result.tokens[0].text != "Привет") {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: std tokenizer must keep non-ASCII UTF-8 bytes intact\n");
        }
    }

    {
        const agent_memory::StandardTokenizer tokenizer;
        agent_memory::TokenizeOptions preserve_case;
        preserve_case.lowercase_ascii = false;

        const auto case_result = tokenizer.tokenize("CamelCase", preserve_case);
        if(case_result.tokens.empty() || case_result.tokens[0].text != "CamelCase") {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: lowercase_ascii=false must preserve ASCII case\n");
        }
    }

    {
        const agent_memory::StandardTokenizer tokenizer;
        agent_memory::TokenizeOptions symbols;
        symbols.emit_symbol_tokens = true;
        symbols.emit_identifier_parts = false;

        const auto symbol_result = tokenizer.tokenize("a+b", symbols);
        if(
            symbol_result.tokens.size() != 3 ||
            symbol_result.tokens[1].kind != agent_memory::TokenKind::Symbol ||
            symbol_result.tokens[1].text != "+"
        ) {
            ++failures;
            std::fprintf(stderr, "  [FAIL] smoke: symbol tokens must be emitted when requested\n");
        }
    }

    failures += run_test("test_tokenize_empty_input", test_tokenize_empty_input);
    failures += run_test("test_tokenize_whitespace_only", test_tokenize_whitespace_only);

    failures += run_test("test_tokenize_leading_dot", test_tokenize_leading_dot);
    failures += run_test("test_tokenize_leading_underscore", test_tokenize_leading_underscore);
    failures += run_test("test_tokenize_double_underscore", test_tokenize_double_underscore);

    failures += run_test("test_tokenize_single_dash_flag", test_tokenize_single_dash_flag);
    failures += run_test("test_tokenize_double_dash_flag", test_tokenize_double_dash_flag);

    failures += run_test("test_tokenize_decimal_number", test_tokenize_decimal_number);
    failures += run_test("test_tokenize_negative_decimal_number", test_tokenize_negative_decimal_number);

    failures += run_test("test_tokenize_windows_path", test_tokenize_windows_path);
    failures += run_test("test_tokenize_unc_path", test_tokenize_unc_path);

    failures += run_test("test_tokenize_trailing_punctuation_default", test_tokenize_trailing_punctuation_default);
    failures += run_test("test_tokenize_trailing_punctuation_with_symbols", test_tokenize_trailing_punctuation_with_symbols);
    failures += run_test("test_tokenize_trailing_dot_on_path", test_tokenize_trailing_dot_on_path);
    failures += run_test("test_tokenize_only_punctuation", test_tokenize_only_punctuation);

    failures += run_test("test_tokenize_acronym_httpserver", test_tokenize_acronym_httpserver);
    failures += run_test("test_tokenize_acronym_xmlhttprequest", test_tokenize_acronym_xmlhttprequest);

    failures += run_test("test_tokenize_emoji_opaque", test_tokenize_emoji_opaque);
    failures += run_test(
        "test_tokenize_camel_fixture_is_deterministic",
        test_tokenize_camel_fixture_is_deterministic
    );

    if(failures > 0) {
        std::fprintf(stderr, "\nFAILED: %d test(s)\n", failures);
        return 1;
    }
    return 0;
}
