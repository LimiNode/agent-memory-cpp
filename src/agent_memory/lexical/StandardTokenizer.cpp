#include "StandardTokenizer.hpp"

#include <algorithm>

namespace agent_memory {
namespace {

    [[nodiscard]] bool is_ascii_alpha(const unsigned char value) noexcept {
        return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z');
    }

    [[nodiscard]] bool is_ascii_upper(const unsigned char value) noexcept {
        return value >= 'A' && value <= 'Z';
    }

    [[nodiscard]] bool is_ascii_lower(const unsigned char value) noexcept {
        return value >= 'a' && value <= 'z';
    }

    [[nodiscard]] bool is_ascii_digit(const unsigned char value) noexcept {
        return value >= '0' && value <= '9';
    }

    [[nodiscard]] bool is_ascii_space(const unsigned char value) noexcept {
        return value == ' ' || value == '\t' || value == '\r' || value == '\n' ||
            value == '\f' || value == '\v';
    }

    [[nodiscard]] bool is_non_ascii(const unsigned char value) noexcept {
        return value >= 0x80U;
    }

    [[nodiscard]] bool is_token_start(const unsigned char value) noexcept {
        return is_ascii_alpha(value) || is_ascii_digit(value) || is_non_ascii(value) ||
            value == '_' || value == '-' || value == '.' || value == '/' ||
            value == '\\';
    }

    [[nodiscard]] bool is_token_body(const unsigned char value) noexcept {
        return is_ascii_alpha(value) || is_ascii_digit(value) || is_non_ascii(value) ||
            value == '_' || value == '-' || value == '.' || value == '/' ||
            value == '\\' || value == ':';
    }

    [[nodiscard]] bool is_identifier_delimiter(const unsigned char value) noexcept {
        return value == '_' || value == '-' || value == '.' || value == ':';
    }

    [[nodiscard]] bool is_trailing_punctuation_byte(const unsigned char value) noexcept {
        return value == '.' || value == ':' || value == ',' ||
            value == ';' || value == '!' || value == '?';
    }

    [[nodiscard]] bool is_cli_flag_like(const std::string_view text) noexcept {
        if(text.size() < 2) return false;
        if(text[0] != '-') return false;
        return text[1] == '-' ||
            !is_ascii_digit(static_cast<unsigned char>(text[1]));
    }

    [[nodiscard]] char ascii_lower(const unsigned char value) noexcept {
        if(value >= 'A' && value <= 'Z') {
            return static_cast<char>(value - 'A' + 'a');
        }
        return static_cast<char>(value);
    }

    [[nodiscard]] std::string normalize_text(
        const std::string_view text,
        const TokenizeOptions& options
    ) {
        std::string normalized{text};
        if(!options.lowercase_ascii) {
            return normalized;
        }

        std::transform(
            normalized.begin(),
            normalized.end(),
            normalized.begin(),
            [](const unsigned char value) -> char {
                return ascii_lower(value);
            }
        );
        return normalized;
    }

    [[nodiscard]] bool is_number_text(const std::string_view text) noexcept {
        bool has_digit = false;
        bool has_dot = false;
        std::size_t index = 0;

        if(!text.empty() && (text[0] == '-' || text[0] == '+')) {
            ++index;
        }
        if(index >= text.size()) return false;

        for(; index < text.size(); ++index) {
            const auto value = static_cast<unsigned char>(text[index]);
            if(is_ascii_digit(value)) {
                has_digit = true;
                continue;
            }

            if(value == '.' && !has_dot) {
                has_dot = true;
                continue;
            }

            return false;
        }

        return has_digit;
    }

    [[nodiscard]] bool has_path_separator(const std::string_view text) noexcept {
        return text.find('/') != std::string_view::npos ||
            text.find('\\') != std::string_view::npos;
    }

    [[nodiscard]] bool has_identifier_delimiter(const std::string_view text) noexcept {
        for(const char raw : text) {
            if(is_identifier_delimiter(static_cast<unsigned char>(raw))) {
                return true;
            }
        }
        return false;
    }

    [[nodiscard]] bool is_camel_boundary_at(
        const std::string_view text,
        const std::size_t index
    ) noexcept {
        if(index == 0 || index >= text.size()) {
            return false;
        }

        const auto previous = static_cast<unsigned char>(text[index - 1]);
        const auto current = static_cast<unsigned char>(text[index]);
        if((is_ascii_lower(previous) || is_ascii_digit(previous)) &&
           is_ascii_upper(current)) {
            return true;
        }

        if(index >= 2) {
            const auto prev_prev = static_cast<unsigned char>(text[index - 2]);
            return is_ascii_upper(prev_prev) &&
                is_ascii_upper(previous) &&
                is_ascii_lower(current);
        }

        return false;
    }

    [[nodiscard]] std::vector<std::size_t> find_camel_boundaries(
        const std::string_view text
    ) {
        std::vector<std::size_t> boundaries;
        for(std::size_t index = 1; index < text.size(); ++index) {
            if(is_camel_boundary_at(text, index)) {
                const auto previous = static_cast<unsigned char>(text[index - 1]);
                if(is_ascii_lower(previous) || is_ascii_digit(previous)) {
                    boundaries.push_back(index);
                    continue;
                }

                // An acronym boundary belongs before the final uppercase
                // character (for example, HTTPServer -> HTTP / Server).
                boundaries.push_back(index - 1);
            }
        }
        return boundaries;
    }

    [[nodiscard]] bool has_camel_boundary(const std::string_view text) noexcept {
        for(std::size_t index = 1; index < text.size(); ++index) {
            if(is_camel_boundary_at(text, index)) {
                return true;
            }
        }
        return false;
    }

    [[nodiscard]] TokenKind classify_token(
        const std::string_view text,
        const bool camel_boundary
    ) noexcept {
        if(has_path_separator(text)) {
            return TokenKind::Path;
        }

        if(is_number_text(text)) {
            return TokenKind::Number;
        }

        if(has_identifier_delimiter(text) || camel_boundary) {
            return TokenKind::Identifier;
        }

        return TokenKind::Word;
    }

    void append_token(
        TokenizationResult& result,
        const std::string_view text,
        const std::size_t offset,
        const TokenKind kind,
        const TokenizeOptions& options
    ) {
        const auto normalized = normalize_text(text, options);
        if(normalized.empty()) {
            return;
        }

        result.tokens.push_back(Token{
            normalized,
            TextRange{offset, text.size()},
            result.tokens.size(),
            kind
        });
    }

    void append_identifier_parts(
        TokenizationResult& result,
        const std::string_view text,
        const std::size_t offset,
        const TokenizeOptions& options,
        const std::vector<std::size_t>& boundaries
    ) {
        std::size_t part_begin = 0;
        std::size_t emitted_parts = 0;
        std::size_t boundary_index = 0;

        const auto flush_part = [&](const std::size_t part_end) {
            if(part_end <= part_begin) {
                return;
            }

            const auto part = text.substr(part_begin, part_end - part_begin);
            append_token(
                result,
                part,
                offset + part_begin,
                is_number_text(part) ? TokenKind::Number : TokenKind::Word,
                options
            );
            ++emitted_parts;
        };

        for(std::size_t index = 0; index < text.size(); ++index) {
            const auto value = static_cast<unsigned char>(text[index]);
            if(is_identifier_delimiter(value)) {
                flush_part(index);
                part_begin = index + 1;
                continue;
            }

            if(index > part_begin) {
                if(boundary_index < boundaries.size() && boundaries[boundary_index] == index) {
                    flush_part(index);
                    part_begin = index;
                    ++boundary_index;
                }
            }
        }

        flush_part(text.size());

        if(emitted_parts == 1 && !result.tokens.empty()) {
            const auto whole = normalize_text(text, options);
            if(result.tokens.back().text == whole) {
                result.tokens.pop_back();
            }
        }
    }

} // namespace

    TokenizationResult StandardTokenizer::tokenize(
        const std::string_view text,
        const TokenizeOptions& options
    ) const {
        TokenizationResult result;

        for(std::size_t index = 0; index < text.size();) {
            const auto value = static_cast<unsigned char>(text[index]);

            if(is_token_start(value)) {
                const auto begin = index;
                ++index;
                while(index < text.size() && is_token_body(static_cast<unsigned char>(text[index]))) {
                    ++index;
                }

                const auto token_text = text.substr(begin, index - begin);
                std::vector<std::size_t> camel_boundaries;
                bool camel_boundary = false;
                if(options.emit_identifier_parts) {
                    camel_boundaries = find_camel_boundaries(token_text);
                    camel_boundary = !camel_boundaries.empty();
                } else {
                    camel_boundary = has_camel_boundary(token_text);
                }
                const auto kind = classify_token(token_text, camel_boundary);

                std::string_view body = token_text;
                std::size_t trim_count = 0;
                if(kind != TokenKind::Number) {
                    while(!body.empty()) {
                        const auto last = static_cast<unsigned char>(body.back());
                        if(is_trailing_punctuation_byte(last)) {
                            body.remove_suffix(1);
                            ++trim_count;
                        } else {
                            break;
                        }
                    }
                }

                append_token(result, body, begin, kind, options);

                if(trim_count > 0 && options.emit_symbol_tokens) {
                    const auto symbol_begin = begin + body.size();
                    for(std::size_t s = 0; s < trim_count; ++s) {
                        const auto pos = symbol_begin + s;
                        append_token(
                            result,
                            text.substr(pos, 1),
                            pos,
                            TokenKind::Symbol,
                            options
                        );
                    }
                }

                if(!body.empty() &&
                   kind == TokenKind::Identifier &&
                   options.emit_identifier_parts &&
                   !is_cli_flag_like(body)) {
                    append_identifier_parts(
                        result,
                        body,
                        begin,
                        options,
                        camel_boundaries
                    );
                }
                continue;
            }

            if(options.emit_symbol_tokens && !is_ascii_space(value) && !is_token_body(value)) {
                append_token(
                    result,
                    text.substr(index, 1),
                    index,
                    TokenKind::Symbol,
                    options
                );
            }

            ++index;
        }

        return result;
    }

} // namespace agent_memory
