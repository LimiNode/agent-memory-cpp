#include "PromptComposition.hpp"

#include "Sha256.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
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
            stream.imbue(std::locale::classic());
            stream << std::setprecision(std::numeric_limits<double>::max_digits10) << item;
            return stream.str();
        }
    }, value);
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
        const bool ascii_alpha = (character >= 'a' && character <= 'z') ||
            (character >= 'A' && character <= 'Z');
        const bool ascii_digit = character >= '0' && character <= '9';
        if(!(ascii_alpha || ascii_digit || character == '.' || character == '_' ||
             character == '-')) return false;
    }
    return true;
}

PromptCompilationResult render_block(std::string_view source,
                                     bool allow_templates,
                                     const PromptRenderContext& context,
                                     std::size_t& expansions,
                                     std::size_t max_expansions,
                                     std::size_t max_template_bytes,
                                     std::size_t max_output_bytes,
                                     std::vector<std::string>& referenced_bindings,
                                     std::string& output) {
    const auto append_bounded = [&](std::string_view value) {
        if(value.size() > max_output_bytes || output.size() > max_output_bytes - value.size()) {
            return false;
        }
        output.append(value);
        return true;
    };
    if(allow_templates && source.size() > max_template_bytes) {
        return failure(PromptCompileStatus::BudgetExceeded, "template input is too large");
    }
    if(!allow_templates) {
        if(!append_bounded(source)) {
            return failure(PromptCompileStatus::BudgetExceeded, "compiled block exceeds result budget");
        }
        return {PromptCompileStatus::Ok, std::nullopt, {}};
    }
    std::size_t cursor = 0;
    while(cursor < source.size()) {
        const auto open = source.find("{{", cursor);
        if(open == std::string_view::npos) {
            if(!append_bounded(source.substr(cursor))) {
                return failure(PromptCompileStatus::BudgetExceeded, "compiled block exceeds result budget");
            }
            break;
        }
        if(!append_bounded(source.substr(cursor, open - cursor))) {
            return failure(PromptCompileStatus::BudgetExceeded, "compiled block exceeds result budget");
        }
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
        if(binding->trust == PromptBindingTrust::TrustedControl) {
            if(!append_bounded(value)) {
                return failure(PromptCompileStatus::BudgetExceeded, "compiled block exceeds result budget");
            }
        } else {
            if(!append_bounded("\"")) {
                return failure(PromptCompileStatus::BudgetExceeded, "compiled block exceeds result budget");
            }
            for(const unsigned char character : value) {
                std::string escaped;
                switch(character) {
                case '\\': escaped = "\\\\"; break;
                case '"': escaped = "\\\""; break;
                case '\n': escaped = "\\n"; break;
                case '\r': escaped = "\\r"; break;
                case '\t': escaped = "\\t"; break;
                default:
                    if(character < 0x20U) {
                        static constexpr char digits[] = "0123456789abcdef";
                        escaped = "\\u00";
                        escaped.push_back(digits[(character >> 4U) & 0x0fU]);
                        escaped.push_back(digits[character & 0x0fU]);
                    } else {
                        escaped.push_back(static_cast<char>(character));
                    }
                }
                if(!append_bounded(escaped)) {
                    return failure(PromptCompileStatus::BudgetExceeded,
                                   "compiled block exceeds result budget");
                }
            }
            if(!append_bounded("\"")) {
                return failure(PromptCompileStatus::BudgetExceeded, "compiled block exceeds result budget");
            }
        }
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
    if(document.document_id.empty() || profile.profile_id.empty() ||
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
    append_field(configuration, std::to_string(profile.limits.max_template_bytes));
    append_field(configuration, std::to_string(profile.limits.max_expansions));
    append_field(configuration, std::to_string(profile.limits.max_result_bytes));

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
    std::vector<ContentBlockId> canonical_preorder;
    std::set<ContentBlockId> emitted;
    const auto append_subtree = [&](const auto& self, const ContentBlockId& id) -> void {
        if(!emitted.insert(id).second) return;
        canonical_preorder.push_back(id);
        for(const auto& candidate : document.blocks) {
            if(candidate.parent_id && *candidate.parent_id == id) {
                self(self, candidate.id);
            }
        }
    };
    for(const auto& block : document.blocks) {
        if(!block.parent_id) append_subtree(append_subtree, block.id);
    }
    if(canonical_preorder.size() != document.blocks.size() ||
       !std::equal(canonical_preorder.begin(), canonical_preorder.end(), document.blocks.begin(),
                   [](const ContentBlockId& id, const ContentBlock& block) {
                       return id == block.id;
                   })) {
        return failure(PromptCompileStatus::InvalidProfile,
                       "document blocks are not in canonical preorder");
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

    struct VariantChoice final {
        std::int32_t priority = 0;
        ContentBlockId block_id;
        std::size_t matches = 0U;
        bool initialized = false;
    };
    std::map<std::string, VariantChoice> variant_choices;
    for(const auto& selection : profile.selections) {
        if(selection.variant_group.empty() || !condition_matches(selection, context) || !selection.enabled) continue;
        const auto variant = profile.variants.find(selection.variant_group);
        if(variant == profile.variants.end()) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "variant group has no declared value: " + selection.variant_group);
        }
        if(variant->second != selection.variant_value) continue;
        auto& choice = variant_choices[selection.variant_group];
        if(!choice.initialized || selection.variant_priority < choice.priority) {
            choice.priority = selection.variant_priority;
            choice.block_id = selection.block_id;
            choice.matches = 1U;
            choice.initialized = true;
        } else if(selection.variant_priority == choice.priority) {
            ++choice.matches;
        }
    }
    std::map<std::string, ContentBlockId> selected_variant;
    for(const auto& [group, choice] : variant_choices) {
        if(choice.matches != 1U) {
            return failure(PromptCompileStatus::AmbiguousVariant,
                           "variant group has no unique selected alternative: " + group);
        }
        selected_variant.emplace(group, choice.block_id);
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
            variant_active = variant != selected_variant.end() && variant->second == block.id;
        }
        const bool enabled = (!selection && profile.default_enabled) ||
            (selection && condition_matches(*selection, context) && selection->enabled && variant_active);
        if(selection && selection->required && !condition_matches(*selection, context)) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "required block condition is not satisfied");
        }
        if(selection && selection->required && !variant_active) {
            return failure(PromptCompileStatus::InvalidProfile,
                           "required block variant is not selected");
        }
        if(!enabled) {
            const auto reason = !selection ? "not-selected" :
                !condition_matches(*selection, context) || !selection->enabled
                    ? "disabled-or-condition"
                    : !variant_active ? "variant-not-selected" : "not-selected";
            manifest.excluded_blocks.push_back({block.id, reason});
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
        // Required conditions and variants are rejected in the activation pass above.
        if(prompt.size() > profile.limits.max_result_bytes) {
            return failure(PromptCompileStatus::BudgetExceeded, "compiled prompt exceeds result budget");
        }
        const auto separator_bytes = prompt.empty() ? 0U : 2U;
        const auto remaining = profile.limits.max_result_bytes - prompt.size();
        if(separator_bytes > remaining) {
            return failure(PromptCompileStatus::BudgetExceeded, "compiled prompt exceeds result budget");
        }
        std::string rendered;
        const auto rendered_result = render_block(block.text, effective_selection->allow_templates, context,
                                                  expansions, profile.limits.max_expansions,
                                                  profile.limits.max_template_bytes,
                                                  remaining - separator_bytes,
                                                  referenced_bindings,
                                                  rendered);
        if(rendered_result.status != PromptCompileStatus::Ok) {
            return rendered_result;
        }
        if(rendered.size() > remaining - separator_bytes) {
            return failure(PromptCompileStatus::BudgetExceeded, "compiled prompt exceeds result budget");
        }
        if(!prompt.empty()) prompt += "\n\n";
        prompt += rendered;
        manifest.included_blocks.push_back(block.id);
        manifest.included_block_revisions.push_back({block.id, block.revision});
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
    for(const auto& included : manifest.included_block_revisions) {
        append_field(configuration, included.block_id.value());
        append_field(configuration, std::to_string(included.block_revision));
    }
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
