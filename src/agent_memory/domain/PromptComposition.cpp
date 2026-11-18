#include "PromptComposition.hpp"

#include "Sha256.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <limits>
#include <set>
#include <sstream>
#include <string_view>
#include <type_traits>

namespace agent_memory {
namespace {

constexpr std::size_t kMaxKeyBytes = 256U;

std::string hex_digest(const std::array<std::uint8_t, 32>& digest) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(64U);
    for(const auto byte : digest) {
        result.push_back(digits[(byte >> 4U) & 0x0fU]);
        result.push_back(digits[byte & 0x0fU]);
    }
    return result;
}

void append_field(std::string& bytes, std::string_view value) {
    bytes += std::to_string(value.size());
    bytes.push_back(':');
    bytes.append(value);
    bytes.push_back('|');
}

std::string value_text(const PromptBindingValue& value) {
    return std::visit([](const auto& item) -> std::string {
        using T = std::decay_t<decltype(item)>;
        if constexpr(std::is_same_v<T, std::string>) {
            return item;
        } else if constexpr(std::is_same_v<T, bool>) {
            return item ? "true" : "false";
        } else if constexpr(std::is_same_v<T, std::int64_t>) {
            return std::to_string(item);
        } else {
            if(!std::isfinite(item)) {
                return {};
            }
            std::ostringstream stream;
            stream << std::setprecision(std::numeric_limits<double>::max_digits10) << item;
            return stream.str();
        }
    }, value);
}

std::string escape_untrusted(std::string_view value) {
    std::string result;
    result.reserve(value.size() + 2U);
    result.push_back('"');
    for(const unsigned char character : value) {
        switch(character) {
        case '\\': result += "\\\\"; break;
        case '"': result += "\\\""; break;
        case '\n': result += "\\n"; break;
        case '\r': result += "\\r"; break;
        case '\t': result += "\\t"; break;
        default:
            if(character < 0x20U) {
                result += "\\u00";
                static constexpr char digits[] = "0123456789abcdef";
                result.push_back(digits[(character >> 4U) & 0x0fU]);
                result.push_back(digits[character & 0x0fU]);
            } else {
                result.push_back(static_cast<char>(character));
            }
        }
    }
    result.push_back('"');
    return result;
}

const PromptBlockSelection* selection_for(
    const PromptAssemblyProfile& profile,
    const ContentBlockId& id) {
    const auto it = std::find_if(profile.selections.begin(), profile.selections.end(),
        [&](const PromptBlockSelection& selection) { return selection.block_id == id; });
    return it == profile.selections.end() ? nullptr : &*it;
}

bool condition_matches(const PromptBlockSelection& selection,
                       const PromptRenderContext& context) {
    return (!selection.locale || *selection.locale == context.locale) &&
        (!selection.style || *selection.style == context.style) &&
        (!selection.consumer_profile || *selection.consumer_profile == context.consumer_profile);
}

const PromptTemplateBinding* binding_for(const PromptRenderContext& context,
                                         std::string_view key) {
    const auto it = context.bindings.find(std::string(key));
    return it == context.bindings.end() ? nullptr : &it->second;
}

PromptCompilationResult failure(PromptCompileStatus status, std::string message) {
    return PromptCompilationResult{status, std::nullopt, std::move(message)};
}

bool is_valid_key(std::string_view key) {
    if(key.empty() || key.size() > kMaxKeyBytes) return false;
    for(const auto character : key) {
        if(!(std::isalnum(static_cast<unsigned char>(character)) || character == '.' ||
             character == '_' || character == '-')) return false;
    }
    return true;
}

PromptCompilationResult render_block(std::string_view source,
                                     bool allow_templates,
                                     const PromptRenderContext& context,
                                     std::size_t& expansions,
                                     std::size_t max_expansions,
                                     std::size_t max_template_bytes,
                                     std::vector<std::string>& referenced_bindings,
                                     std::string& output) {
    if(allow_templates && source.size() > max_template_bytes) {
        return failure(PromptCompileStatus::BudgetExceeded, "template input is too large");
    }
    if(!allow_templates) {
        output.append(source);
        return {PromptCompileStatus::Ok, std::nullopt, {}};
    }
    std::size_t cursor = 0;
    while(cursor < source.size()) {
        const auto open = source.find("{{", cursor);
        if(open == std::string_view::npos) {
            output.append(source.substr(cursor));
            break;
        }
        output.append(source.substr(cursor, open - cursor));
        const auto close = source.find("}}", open + 2U);
        if(close == std::string_view::npos) {
            return failure(PromptCompileStatus::InvalidTemplate, "unterminated template binding");
        }
        const auto key = source.substr(open + 2U, close - open - 2U);
        if(!is_valid_key(key)) {
            return failure(PromptCompileStatus::InvalidTemplate, "invalid template binding key");
        }
        if(++expansions > max_expansions) {
            return failure(PromptCompileStatus::BudgetExceeded, "template expansion budget exceeded");
        }
        const auto* binding = binding_for(context, key);
        if(!binding) {
            return failure(PromptCompileStatus::MissingBinding,
                           "missing template binding: " + std::string(key));
        }
        const auto value = value_text(binding->value);
        if(value.empty() && !std::holds_alternative<std::string>(binding->value)) {
            return failure(PromptCompileStatus::InvalidBinding, "non-finite template binding");
        }
        output += binding->trust == PromptBindingTrust::TrustedControl
            ? value : escape_untrusted(value);
        if(std::find(referenced_bindings.begin(), referenced_bindings.end(), key) == referenced_bindings.end()) {
            referenced_bindings.emplace_back(key);
        }
        cursor = close + 2U;
    }
    return {PromptCompileStatus::Ok, std::nullopt, {}};
}

} // namespace

PromptCompilationResult compile_prompt(const CanonicalDocumentRevision& document,
                                       const PromptAssemblyProfile& profile,
                                       const PromptRenderContext& context) {
    if(document.document_id.empty() || document.revision == 0U || profile.profile_id.empty() ||
       profile.revision == 0U || profile.renderer_revision.empty() ||
       profile.limits.max_expansions == 0U || profile.limits.max_result_bytes == 0U) {
        return failure(PromptCompileStatus::InvalidProfile, "document or profile identity is invalid");
    }

    std::set<ContentBlockId> seen_selections;
    for(const auto& selection : profile.selections) {
        if(selection.block_id.empty() || !seen_selections.insert(selection.block_id).second) {
            return failure(PromptCompileStatus::InvalidProfile, "profile contains duplicate block selection");
        }
        if(selection.variant_group.empty() != selection.variant_value.empty()) {
            return failure(PromptCompileStatus::InvalidProfile, "variant group and value must be paired");
        }
        if(selection.required && !selection.enabled) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "a required block cannot be disabled");
        }
    }

    std::string prompt;
    std::string configuration;
    PromptAssemblyManifest manifest;
    manifest.document_id = document.document_id;
    manifest.document_revision = document.revision;
    manifest.profile_id = profile.profile_id;
    manifest.profile_revision = profile.revision;
    manifest.renderer_revision = profile.renderer_revision;
    manifest.limits = profile.limits;
    append_field(configuration, profile.profile_id);
    append_field(configuration, std::to_string(profile.revision));
    append_field(configuration, profile.renderer_revision);
    append_field(configuration, profile.default_enabled ? "default-enabled" : "default-disabled");
    append_field(configuration, document.document_id.value());
    append_field(configuration, std::to_string(document.revision));
    append_field(configuration, context.locale);
    append_field(configuration, context.style);
    append_field(configuration, context.consumer_profile);

    std::set<ContentBlockId> active;
    std::set<ContentBlockId> known;
    std::set<ContentBlockId> seen_blocks;
    for(const auto& block : document.blocks) {
        if(block.id.empty() || !known.insert(block.id).second) {
            return failure(PromptCompileStatus::InvalidProfile, "document contains duplicate or empty block id");
        }
        if(block.parent_id && seen_blocks.count(*block.parent_id) == 0U) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "document blocks are not in canonical preorder");
        }
        seen_blocks.insert(block.id);
    }
    for(const auto& selection : profile.selections) {
        if(known.count(selection.block_id) == 0U) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "profile references an unknown block");
        }
    }
    for(const auto& [key, binding] : context.bindings) {
        if(!is_valid_key(key) || binding.key != key) {
            return failure(PromptCompileStatus::InvalidBinding,
                           "template binding key is invalid or inconsistent");
        }
        if(std::holds_alternative<double>(binding.value) &&
           !std::isfinite(std::get<double>(binding.value))) {
            return failure(PromptCompileStatus::InvalidBinding,
                           "template binding contains a non-finite number");
        }
    }

    std::map<std::string, std::optional<ContentBlockId>> selected_variant;
    for(const auto& selection : profile.selections) {
        if(selection.variant_group.empty() || !condition_matches(selection, context) || !selection.enabled) continue;
        const auto variant = profile.variants.find(selection.variant_group);
        if(variant == profile.variants.end()) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "variant group has no declared value: " + selection.variant_group);
        }
        if(variant->second != selection.variant_value) continue;
        auto& selected = selected_variant[selection.variant_group];
        if(!selected || selection.variant_priority < selection_for(profile, *selected)->variant_priority) {
            selected = selection.block_id;
        } else if(selection.variant_priority == selection_for(profile, *selected)->variant_priority) {
            return failure(PromptCompileStatus::AmbiguousVariant,
                           "variant group has no unique selected alternative: " + selection.variant_group);
        }
    }

    std::size_t expansions = 0U;
    std::vector<std::string> referenced_bindings;
    for(const auto& block : document.blocks) {
        const auto* selection = selection_for(profile, block.id);
        const bool parent_active = !block.parent_id || active.count(*block.parent_id) != 0U;
        if(!parent_active) {
            manifest.excluded_blocks.push_back({block.id, "parent-disabled"});
            continue;
        }
        bool variant_active = true;
        if(selection && !selection->variant_group.empty()) {
            const auto variant = selected_variant.find(selection->variant_group);
            variant_active = variant != selected_variant.end() && variant->second &&
                *variant->second == block.id;
        }
        const bool enabled = (!selection && profile.default_enabled) ||
            (selection && condition_matches(*selection, context) && selection->enabled && variant_active);
        if(!enabled) {
            manifest.excluded_blocks.push_back({block.id, selection && !selection->variant_group.empty()
                ? "variant-not-selected" : selection ? "disabled-or-condition" : "not-selected"});
            continue;
        }
        active.insert(block.id);
    }

    for(const auto& block : document.blocks) {
        if(active.count(block.id) == 0U) continue;
        const auto* selection = selection_for(profile, block.id);
        PromptBlockSelection default_selection;
        default_selection.block_id = block.id;
        default_selection.enabled = profile.default_enabled;
        const auto* effective_selection = selection ? selection : &default_selection;
        if(effective_selection->required && !condition_matches(*effective_selection, context)) {
            return failure(PromptCompileStatus::InvalidProfile, "required block condition is not satisfied");
        }
        std::string rendered;
        const auto rendered_result = render_block(block.text, effective_selection->allow_templates, context,
                                                  expansions, profile.limits.max_expansions,
                                                  profile.limits.max_template_bytes,
                                                  referenced_bindings,
                                                  rendered);
        if(rendered_result.status != PromptCompileStatus::Ok) {
            return rendered_result;
        }
        if(rendered.size() > profile.limits.max_result_bytes ||
           prompt.size() > profile.limits.max_result_bytes - rendered.size()) {
            return failure(PromptCompileStatus::BudgetExceeded, "compiled prompt exceeds result budget");
        }
        if(!prompt.empty()) prompt += "\n\n";
        prompt += rendered;
        manifest.included_blocks.push_back(block.id);
    }

    manifest.binding_keys = referenced_bindings;

    for(const auto& key : manifest.binding_keys) {
        const auto* binding = binding_for(context, key);
        if(!binding) {
            return failure(PromptCompileStatus::MissingBinding,
                           "missing template binding: " + key);
        }
        append_field(configuration, key);
        append_field(configuration, value_text(binding->value));
        append_field(configuration, binding->trust == PromptBindingTrust::TrustedControl ? "trusted" : "data");
    }
    std::vector<const PromptBlockSelection*> ordered_selections;
    ordered_selections.reserve(profile.selections.size());
    for(const auto& selection : profile.selections) ordered_selections.push_back(&selection);
    std::sort(ordered_selections.begin(), ordered_selections.end(),
              [](const auto* lhs, const auto* rhs) { return lhs->block_id < rhs->block_id; });
    for(const auto* selection : ordered_selections) {
        append_field(configuration, selection->block_id.value());
        append_field(configuration, selection->enabled ? "enabled" : "disabled");
        append_field(configuration, selection->required ? "required" : "optional");
        append_field(configuration, selection->control_instruction ? "control" : "data");
        append_field(configuration, selection->allow_templates ? "templated" : "literal");
        append_field(configuration, selection->locale.value_or(""));
        append_field(configuration, selection->style.value_or(""));
        append_field(configuration, selection->consumer_profile.value_or(""));
        append_field(configuration, selection->variant_group);
        append_field(configuration, selection->variant_value);
        append_field(configuration, std::to_string(selection->variant_priority));
    }
    for(const auto& id : manifest.included_blocks) append_field(configuration, id.value());
    for(const auto& [group, value] : profile.variants) {
        append_field(configuration, group);
        append_field(configuration, value);
    }
    manifest.text_digest = hex_digest(sha256(prompt));
    manifest.configuration_digest = hex_digest(sha256(configuration));
    return PromptCompilationResult{PromptCompileStatus::Ok,
                                   CompiledPrompt{std::move(prompt), std::move(manifest)}, {}};
}

} // namespace agent_memory
