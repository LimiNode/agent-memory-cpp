#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_PROMPT_COMPOSITION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_PROMPT_COMPOSITION_HPP_INCLUDED

/// \file PromptComposition.hpp
/// \brief Deterministic, source-revision-pinned prompt composition.
///
/// This is a pure layer over CanonicalDocumentRevision. It does not mutate
/// canonical content, read implicit process state, or make authority
/// decisions. Durable profile storage is an opt-in separate adapter and never
/// selects or activates a profile for the compiler.

#include "CanonicalContent.hpp"

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace agent_memory {

    /// \brief Typed value accepted by the restricted template renderer.
    using PromptBindingValue = std::variant<std::string, bool, std::int64_t, double>;

    enum class PromptBindingTrust {
        TrustedControl,
        UntrustedData
    };

    /// \brief Explicitly supplied template value and its trust classification.
    struct PromptTemplateBinding final {
        std::string key;
        PromptBindingValue value;
        PromptBindingTrust trust = PromptBindingTrust::UntrustedData;
    };

    /// \brief Explicit inputs available to a composition build.
    struct PromptRenderContext final {
        std::string locale;
        std::string style;
        std::string consumer_profile;
        std::map<std::string, PromptTemplateBinding> bindings;
    };

    /// \brief Resource limits that make template expansion bounded.
    struct PromptCompileLimits final {
        std::size_t max_template_bytes = 64U * 1024U;
        std::size_t max_expansions = 256U;
        std::size_t max_result_bytes = 256U * 1024U;
    };

    /// \brief Declarative selection rule for one stable canonical block.
    struct PromptBlockSelection final {
        ContentBlockId block_id;
        bool enabled = true;
        bool required = false;
        bool control_instruction = false;
        bool allow_templates = false;
        std::optional<std::string> locale;
        std::optional<std::string> style;
        std::optional<std::string> consumer_profile;
        std::string variant_group;
        std::string variant_value;
        std::int32_t variant_priority = 0;
    };

    /// \brief Versioned, immutable-in-use rules for one composition profile.
    struct PromptAssemblyProfile final {
        std::string profile_id;
        std::uint64_t revision = 0;
        std::string renderer_revision = "prompt-renderer-v1";
        bool default_enabled = true;
        std::vector<PromptBlockSelection> selections;
        std::map<std::string, std::string> variants;
        PromptCompileLimits limits;
    };

    enum class PromptCompileStatus {
        Ok,
        InvalidProfile,
        StaleDocument,
        MissingBinding,
        InvalidTemplate,
        AmbiguousVariant,
        BudgetExceeded,
        InvalidBinding
    };

    /// \brief Why a canonical block was omitted from a compiled prompt.
    struct PromptExclusion final {
        ContentBlockId block_id;
        std::string reason;
    };

    /// \brief A selected block and the exact immutable revision used.
    struct PromptIncludedBlock final {
        ContentBlockId block_id;
        std::uint64_t block_revision = 0;
    };

    /// \brief Reproducibility record for one successful or failed build.
    struct PromptAssemblyManifest final {
        DocumentId document_id;
        std::uint64_t document_revision = 0;
        std::string profile_id;
        std::uint64_t profile_revision = 0;
        std::string renderer_revision;
        std::vector<ContentBlockId> included_blocks;
        std::vector<PromptIncludedBlock> included_block_revisions;
        std::vector<PromptExclusion> excluded_blocks;
        std::vector<std::string> binding_keys;
        std::string text_digest;
        std::string configuration_digest;
        PromptCompileLimits limits;
    };

    /// \brief Immutable result of deterministic prompt compilation.
    struct CompiledPrompt final {
        std::string text;
        PromptAssemblyManifest manifest;
    };

    /// \brief Result with a typed failure and no partially compiled output.
    struct PromptCompilationResult final {
        PromptCompileStatus status = PromptCompileStatus::InvalidProfile;
        std::optional<CompiledPrompt> prompt;
        std::string message;

        [[nodiscard]] bool succeeded() const noexcept {
            return status == PromptCompileStatus::Ok && prompt.has_value();
        }
    };

    /// \brief Compiles a prompt without changing `document` or reading ambient state.
    [[nodiscard]] PromptCompilationResult compile_prompt(
        const CanonicalDocumentRevision& document,
        const PromptAssemblyProfile& profile,
        const PromptRenderContext& context);

} // namespace agent_memory

#endif
