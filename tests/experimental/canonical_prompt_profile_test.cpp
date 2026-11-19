#include <agent_memory.hpp>
#include <agent_memory/domain/Sha256.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>
#endif

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;

struct PromptPolicyBlock final {
    ContentBlockId block_id;
    bool required = true;
    bool editable = false;
    bool control_instruction = true;
};

struct PromptPolicyDocument final {
    DocumentId document_id;
    std::uint64_t baseline_revision = 0;
    std::vector<PromptPolicyBlock> blocks;
};

struct PromptPolicy final {
    std::string policy_id;
    std::string baseline_id;
    std::vector<PromptPolicyDocument> documents;
};

struct PromptCandidateDocument final {
    DocumentId document_id;
    std::uint64_t revision = 0;
    std::vector<ContentBlockId> selected_blocks;
};

struct PromptCandidate final {
    std::string baseline_id;
    std::vector<PromptCandidateDocument> documents;
};

struct PromptBuildResult final {
    std::string text;
    std::string text_digest;
    std::string config_digest;
};

[[noreturn]] void fail(const std::string& message) {
    throw std::runtime_error(message);
}

void expect(bool condition, const std::string& message) {
    if(!condition) {
        fail(message);
    }
}

std::string digest_hex(const std::array<std::uint8_t, 32>& digest) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(64);
    for(const auto byte : digest) {
        result.push_back(digits[(byte >> 4U) & 0x0fU]);
        result.push_back(digits[byte & 0x0fU]);
    }
    return result;
}

void append_config_field(std::string& bytes, std::string_view field) {
    bytes += std::to_string(field.size());
    bytes.push_back(':');
    bytes.append(field);
    bytes.push_back('|');
}

std::string prompt_digest(const std::string& prompt);

bool allows_candidate_edit(const PromptPolicyDocument& document,
                           const CanonicalEditOperation& operation,
                           std::string& error);

std::optional<PromptBuildResult> build_exact_prompt(
    const ICanonicalContentStore& store,
    const PromptPolicy& policy,
    const PromptCandidate& candidate,
    std::string& error) {
    error.clear();
    if(policy.policy_id.empty() || policy.baseline_id.empty() || policy.documents.empty() ||
       candidate.baseline_id.empty() || candidate.documents.empty()) {
        error = "prompt policy and candidate must be non-empty";
        return std::nullopt;
    }
    if(policy.baseline_id != candidate.baseline_id) {
        error = "candidate baseline does not match policy";
        return std::nullopt;
    }
    if(candidate.documents.size() != policy.documents.size()) {
        error = "candidate document set does not match policy";
        return std::nullopt;
    }
    for(std::size_t index = 0; index < policy.documents.size(); ++index) {
        for(std::size_t previous = 0; previous < index; ++previous) {
            if(policy.documents[index].document_id == policy.documents[previous].document_id ||
               candidate.documents[index].document_id == candidate.documents[previous].document_id) {
                error = "prompt document set contains a duplicate identifier";
                return std::nullopt;
            }
        }
    }

    std::string prompt;
    std::string config_bytes;
    append_config_field(config_bytes, policy.policy_id);
    append_config_field(config_bytes, policy.baseline_id);
    for(const auto& policy_document : policy.documents) {
        const auto candidate_document = std::find_if(
            candidate.documents.begin(), candidate.documents.end(),
            [&](const PromptCandidateDocument& document) {
                return document.document_id == policy_document.document_id;
            });
        if(candidate_document == candidate.documents.end()) {
            error = "candidate document set does not match policy";
            return std::nullopt;
        }
        const auto baseline = store.read_revision(
            policy_document.document_id, policy_document.baseline_revision);
        const auto revision = store.read_revision(
            candidate_document->document_id, candidate_document->revision);
        if(!baseline || !revision) {
            error = "pinned revision is unavailable";
            return std::nullopt;
        }
        if(policy_document.blocks.size() != baseline->blocks.size()) {
            error = "prompt policy does not classify the complete baseline";
            return std::nullopt;
        }

        append_config_field(config_bytes, policy_document.document_id.value());
        append_config_field(config_bytes, std::to_string(policy_document.baseline_revision));
        append_config_field(config_bytes, std::to_string(candidate_document->revision));
        if(baseline->blocks.size() != revision->blocks.size()) {
            error = "candidate changed prompt structure";
            return std::nullopt;
        }
        if(baseline->metadata.values() != revision->metadata.values()) {
            error = "candidate changed prompt metadata";
            return std::nullopt;
        }
        for(std::size_t index = 0; index < baseline->blocks.size(); ++index) {
            const auto& expected = baseline->blocks[index];
            const auto& actual = revision->blocks[index];
            if(expected.id != actual.id || expected.parent_id != actual.parent_id ||
               expected.kind != actual.kind) {
                error = "candidate changed prompt structure";
                return std::nullopt;
            }
            const auto reference = std::find_if(
                policy_document.blocks.begin(), policy_document.blocks.end(),
                [&](const PromptPolicyBlock& block) { return block.block_id == expected.id; });
            if(reference == policy_document.blocks.end()) {
                error = "baseline contains an unclassified prompt block";
                return std::nullopt;
            }
            if(policy_document.blocks[index].block_id != expected.id) {
                error = "prompt policy block order is not canonical";
                return std::nullopt;
            }
            if(expected.text != actual.text &&
               !allows_candidate_edit(
                   policy_document,
                   ReplaceBlockText{expected.id, actual.text},
                   error)) {
                return std::nullopt;
            }
            append_config_field(config_bytes, expected.id.value());
            append_config_field(config_bytes, reference->required ? "required" : "optional");
            append_config_field(config_bytes, reference->editable ? "editable" : "fixed");
            append_config_field(config_bytes,
                                reference->control_instruction ? "control" : "context");
        }

        std::vector<bool> selected(policy_document.blocks.size(), false);
        std::size_t previous_selected_index = 0;
        bool first_selected = true;
        for(const auto& selected_id : candidate_document->selected_blocks) {
            const auto reference = std::find_if(
                policy_document.blocks.begin(), policy_document.blocks.end(),
                [&](const PromptPolicyBlock& block) { return block.block_id == selected_id; });
            if(reference == policy_document.blocks.end()) {
                error = "candidate references an unknown prompt block";
                return std::nullopt;
            }
            const auto index = static_cast<std::size_t>(
                std::distance(policy_document.blocks.begin(), reference));
            if(selected[index]) {
                error = "candidate references a prompt block more than once";
                return std::nullopt;
            }
            if(!first_selected && index <= previous_selected_index) {
                error = "candidate prompt block order is not canonical";
                return std::nullopt;
            }
            selected[index] = true;
            first_selected = false;
            previous_selected_index = index;
        }
        // Activation is part of the assembly configuration, including
        // selections that do not contribute control text.  Record the
        // canonical candidate order so distinct masks cannot alias.
        append_config_field(config_bytes, "selected-blocks");
        for(const auto& selected_id : candidate_document->selected_blocks) {
            append_config_field(config_bytes, selected_id.value());
        }
        for(std::size_t index = 0; index < policy_document.blocks.size(); ++index) {
            const auto& reference = policy_document.blocks[index];
            if(reference.required && !selected[index]) {
                error = "required prompt block is missing";
                return std::nullopt;
            }
        }

        // The production compiler owns canonical rendering, trust and budget
        // semantics. This multi-document research harness only adapts its
        // candidate mask to the canonical profile API and combines the
        // independently compiled document prompts.
        PromptAssemblyProfile canonical_profile;
        canonical_profile.profile_id = policy.policy_id + ":" +
            policy_document.document_id.value();
        canonical_profile.revision = 1;
        canonical_profile.default_enabled = false;
        canonical_profile.limits.max_result_bytes = 256U * 1024U;
        canonical_profile.selections.reserve(policy_document.blocks.size());
        for(std::size_t index = 0; index < policy_document.blocks.size(); ++index) {
            const auto& reference = policy_document.blocks[index];
            PromptBlockSelection selection;
            selection.block_id = reference.block_id;
            selection.enabled = selected[index] && reference.control_instruction;
            selection.control_instruction = reference.control_instruction;
            selection.required = false;
            canonical_profile.selections.push_back(std::move(selection));
        }
        const auto compiled = compile_prompt(*revision, canonical_profile, {});
        if(!compiled.succeeded()) {
            error = compiled.message.empty() ? "canonical prompt compilation failed" :
                compiled.message;
            return std::nullopt;
        }
        if(!compiled.prompt->text.empty()) {
            if(!prompt.empty()) {
                prompt += "\n\n";
            }
            prompt += compiled.prompt->text;
        }
        append_config_field(config_bytes, "canonical-configuration");
        append_config_field(config_bytes, compiled.prompt->manifest.configuration_digest);
    }
    if(prompt.empty()) {
        error = "resolved control prompt is empty";
        return std::nullopt;
    }
    return PromptBuildResult{prompt, prompt_digest(prompt), prompt_digest(config_bytes)};
}

std::string prompt_digest(const std::string& prompt) {
    return digest_hex(sha256(prompt));
}

bool allows_candidate_edit(const PromptPolicyDocument& document,
                           const CanonicalEditOperation& operation,
                           std::string& error) {
    return std::visit([&](const auto& edit) {
        using Edit = std::decay_t<decltype(edit)>;
        if constexpr(std::is_same_v<Edit, ReplaceBlockText>) {
            const auto reference = std::find_if(
                document.blocks.begin(), document.blocks.end(),
                [&](const PromptPolicyBlock& value) { return value.block_id == edit.block_id; });
            if(reference == document.blocks.end() || !reference->editable) {
                error = "candidate may edit only explicitly editable blocks";
                return false;
            }
            return true;
        } else {
            error = "candidate structural edits are forbidden";
            return false;
        }
    }, operation);
}

CanonicalDocumentRevision prompt_document(const DocumentId& id) {
    return CanonicalDocumentRevision{
        id,
        0,
        {},
        {
            {ContentBlockId{"role"}, 1, ContentBlockKind::Heading, std::nullopt,
             "Role: concise systems assistant"},
            {ContentBlockId{"obligations"}, 1, ContentBlockKind::Paragraph,
             ContentBlockId{"role"}, "Obligations: preserve exact revisions and cite evidence."},
            {ContentBlockId{"tools"}, 1, ContentBlockKind::Paragraph,
             ContentBlockId{"role"}, "Tools: use retrieval only for candidate discovery."},
            {ContentBlockId{"output"}, 1, ContentBlockKind::Paragraph,
             ContentBlockId{"role"}, "Output: return a short, deterministic answer."},
        }};
}

void run_prompt_profile_suite(ICanonicalContentStore& store,
                              ICanonicalContentEditor& editor,
                              const std::string& backend_name) {
    const DocumentId document_id{"prompt:baseline"};
    expect(editor.create_document(prompt_document(document_id)),
           backend_name + ": initial prompt document was not created");

    const auto baseline = store.read_revision(document_id, 0);
    expect(baseline && baseline->blocks.size() == 4,
           backend_name + ": exact baseline revision is unavailable");
    expect(store.read_block(document_id, ContentBlockId{"obligations"}, 0)->text.find("exact revisions") !=
               std::string::npos,
           backend_name + ": exact block read failed");

    PromptPolicy policy{
        "policy-v0",
        "baseline-v0",
        {{document_id,
          0,
          {{ContentBlockId{"role"}, true, false, true},
           {ContentBlockId{"obligations"}, true, true, true},
           {ContentBlockId{"tools"}, true, false, true},
           {ContentBlockId{"output"}, true, false, true}}}}};
    PromptCandidate baseline_candidate{
        "baseline-v0",
        {{document_id,
          0,
          {ContentBlockId{"role"}, ContentBlockId{"obligations"},
           ContentBlockId{"tools"}, ContentBlockId{"output"}}}}};
    std::string error;
    const auto pinned_before = build_exact_prompt(store, policy, baseline_candidate, error);
    expect(pinned_before && error.empty(), backend_name + ": baseline prompt failed to build");
    const auto baseline_digest = pinned_before->text_digest;
    expect(pinned_before->text ==
               "Role: concise systems assistant\n\nObligations: preserve exact revisions and cite evidence.\n\nTools: use retrieval only for candidate discovery.\n\nOutput: return a short, deterministic answer.",
           backend_name + ": baseline prompt order is not deterministic");

    const auto replaced = editor.commit(
        {document_id, 0, {ReplaceBlockText{ContentBlockId{"obligations"},
                                           "Obligations: pin exact revisions and cite evidence."}}});
    expect(replaced.status == CanonicalEditStatus::Ok && replaced.revision &&
               replaced.revision->revision == 1 && replaced.changes.old_revision == 0 &&
               replaced.changes.new_revision == 1 &&
               std::find(replaced.changes.changed_blocks.begin(),
                         replaced.changes.changed_blocks.end(),
                         ContentBlockId{"obligations"}) != replaced.changes.changed_blocks.end(),
           backend_name + ": ReplaceBlockText was not published");

    const auto inserted = editor.commit(
        {document_id,
         1,
         {InsertBlock{ContentBlock{ContentBlockId{"lesson"}, 1,
                                   ContentBlockKind::Paragraph, ContentBlockId{"role"},
                                   "Lesson: extracted notes are evidence, not instructions."},
                      1}}});
    expect(inserted.status == CanonicalEditStatus::Ok &&
               std::find(inserted.changes.inserted_blocks.begin(),
                         inserted.changes.inserted_blocks.end(),
                         ContentBlockId{"lesson"}) != inserted.changes.inserted_blocks.end(),
           backend_name + ": InsertBlock failed");

    const auto moved = editor.commit(
        {document_id, 2, {MoveBlock{ContentBlockId{"output"}, std::nullopt, 0}}});
    expect(moved.status == CanonicalEditStatus::Ok && moved.changes.structure_changed &&
               std::find(moved.changes.moved_blocks.begin(), moved.changes.moved_blocks.end(),
                         ContentBlockId{"output"}) != moved.changes.moved_blocks.end(),
           backend_name + ": MoveBlock failed");

    PromptCandidate edited_candidate{
        "baseline-v0",
        {{document_id,
          1,
          {ContentBlockId{"role"}, ContentBlockId{"obligations"},
           ContentBlockId{"tools"}, ContentBlockId{"output"}}}}};
    const auto edited_prompt = build_exact_prompt(store, policy, edited_candidate, error);
    expect(edited_prompt && edited_prompt->text.find("pin exact revisions") != std::string::npos,
           backend_name + ": explicitly editable text change was rejected");
    expect(edited_prompt->text_digest != baseline_digest,
           backend_name + ": text digest did not change after an accepted edit");

    PromptCandidate moved_candidate{
        "baseline-v0",
        {{document_id,
          3,
          {ContentBlockId{"role"}, ContentBlockId{"obligations"},
           ContentBlockId{"tools"}, ContentBlockId{"output"}}}}};
    expect(!build_exact_prompt(store, policy, moved_candidate, error) &&
               error == "candidate changed prompt structure",
           backend_name + ": forbidden MoveBlock candidate was accepted");

    PromptPolicy lesson_policy{
        "policy-v2",
        "baseline-v2",
        {{document_id,
          2,
          {{ContentBlockId{"role"}, true, false, true},
           {ContentBlockId{"obligations"}, true, true, true},
           {ContentBlockId{"lesson"}, false, false, false},
           {ContentBlockId{"tools"}, true, false, true},
           {ContentBlockId{"output"}, true, false, true}}}}};
    PromptCandidate lesson_candidate{
        "baseline-v2",
        {{document_id,
          2,
          {ContentBlockId{"role"}, ContentBlockId{"obligations"},
           ContentBlockId{"lesson"},
           ContentBlockId{"tools"}, ContentBlockId{"output"},
           }}}};
    const auto lesson_prompt = build_exact_prompt(store, lesson_policy, lesson_candidate, error);
    expect(lesson_prompt && lesson_prompt->text.find("Lesson:") == std::string::npos,
           backend_name + ": optional lesson entered the control prompt");
    PromptCandidate lesson_without_selection = lesson_candidate;
    lesson_without_selection.documents.front().selected_blocks.erase(
        lesson_without_selection.documents.front().selected_blocks.begin() + 2);
    const auto lesson_without =
        build_exact_prompt(store, lesson_policy, lesson_without_selection, error);
    expect(lesson_without && lesson_without->text == lesson_prompt->text &&
               lesson_without->config_digest != lesson_prompt->config_digest,
           backend_name + ": optional non-control selection did not change configuration digest");

    PromptPolicy optional_control_policy = policy;
    optional_control_policy.policy_id = "policy-optional-control";
    optional_control_policy.documents.front().blocks[2].required = false;
    PromptCandidate optional_control_candidate = baseline_candidate;
    optional_control_candidate.documents.front().selected_blocks.erase(
        optional_control_candidate.documents.front().selected_blocks.begin() + 2);
    const auto optional_control_without =
        build_exact_prompt(store, optional_control_policy, optional_control_candidate, error);
    const auto optional_control_with =
        build_exact_prompt(store, optional_control_policy, baseline_candidate, error);
    expect(optional_control_without && optional_control_with &&
               optional_control_without->text.find("Tools:") == std::string::npos &&
               optional_control_with->text.find("Tools:") != std::string::npos &&
               optional_control_without->config_digest != optional_control_with->config_digest,
           backend_name + ": optional control selection was not reflected in configuration digest");

    const auto historical = store.materialize_markdown(document_id, 0);
    const auto current = store.materialize_markdown(document_id);
    expect(historical && current && historical->find("preserve exact revisions") != std::string::npos &&
               historical->find("Lesson:") == std::string::npos &&
               current->find("pin exact revisions") != std::string::npos,
           backend_name + ": historical revision changed after edits");

    const auto stale = editor.commit(
        {document_id, 0, {ReplaceBlockText{ContentBlockId{"output"}, "stale"}}});
    expect(stale.status == CanonicalEditStatus::Conflict,
           backend_name + ": stale edit was accepted");

    std::string edit_error;
    expect(allows_candidate_edit(policy.documents.front(),
                                 ReplaceBlockText{ContentBlockId{"obligations"}, "candidate"},
                                 edit_error),
           backend_name + ": explicitly editable block was rejected");
    expect(!allows_candidate_edit(policy.documents.front(),
                                  ReplaceBlockText{ContentBlockId{"role"}, "candidate"},
                                  edit_error) &&
               !allows_candidate_edit(policy.documents.front(),
                                      MoveBlock{ContentBlockId{"obligations"}, std::nullopt, 0},
                                      edit_error),
           backend_name + ": forbidden candidate edit was accepted");

    const DocumentId second_document_id{"prompt:second"};
    expect(editor.create_document(prompt_document(second_document_id)),
           backend_name + ": second prompt document was not created");
    const auto forbidden_text_edit = editor.commit(
        {second_document_id, 0,
         {ReplaceBlockText{ContentBlockId{"role"}, "Role: forged"}}});
    expect(forbidden_text_edit.status == CanonicalEditStatus::Ok &&
               forbidden_text_edit.revision && forbidden_text_edit.revision->revision == 1,
           backend_name + ": non-editable test revision was not published");
    PromptPolicy fixed_policy{
        "policy-fixed",
        "baseline-fixed",
        {{second_document_id,
          0,
          {{ContentBlockId{"role"}, true, false, true},
           {ContentBlockId{"obligations"}, true, true, true},
           {ContentBlockId{"tools"}, true, false, true},
           {ContentBlockId{"output"}, true, false, true}}}}};
    PromptCandidate fixed_candidate{
        "baseline-fixed",
        {{second_document_id,
          1,
          {ContentBlockId{"role"}, ContentBlockId{"obligations"},
           ContentBlockId{"tools"}, ContentBlockId{"output"}}}}};
    expect(!build_exact_prompt(store, fixed_policy, fixed_candidate, error) &&
               error == "candidate may edit only explicitly editable blocks",
           backend_name + ": non-editable text edit was admitted");

    const auto cycle = editor.commit(
        {document_id, 3, {MoveBlock{ContentBlockId{"role"}, ContentBlockId{"lesson"}, 0}}});
    expect(cycle.status == CanonicalEditStatus::InvalidEdit,
           backend_name + ": structural cycle was accepted");
    const auto repeated = editor.commit(
        {document_id,
         3,
         {InsertBlock{ContentBlock{ContentBlockId{"lesson"}, 1,
                                   ContentBlockKind::Paragraph, std::nullopt, "duplicate"},
                      0}}});
    expect(repeated.status == CanonicalEditStatus::InvalidEdit,
           backend_name + ": repeated block id was accepted");
    const auto non_leaf_delete = editor.commit(
        {document_id, 3, {DeleteBlock{ContentBlockId{"role"}}}});
    expect(non_leaf_delete.status == CanonicalEditStatus::InvalidEdit,
           backend_name + ": non-leaf deletion was accepted");

    Metadata changed_metadata;
    changed_metadata.set("profile", "metadata-only-change");
    const auto metadata_only = editor.commit(
        {second_document_id, 1, {UpdateDocumentMetadata{changed_metadata}}});
    expect(metadata_only.status == CanonicalEditStatus::Ok && metadata_only.revision &&
               metadata_only.revision->revision == 2 && metadata_only.changes.metadata_changed,
           backend_name + ": metadata-only revision was not published");
    PromptPolicy metadata_policy = fixed_policy;
    metadata_policy.policy_id = "policy-fixed-metadata";
    metadata_policy.baseline_id = "baseline-fixed-metadata";
    metadata_policy.documents.front().baseline_revision = 1;
    PromptCandidate metadata_candidate{
        "baseline-fixed-metadata",
        {{second_document_id,
          2,
          {ContentBlockId{"role"}, ContentBlockId{"obligations"},
           ContentBlockId{"tools"}, ContentBlockId{"output"}}}}};
    expect(!build_exact_prompt(store, metadata_policy, metadata_candidate, error) &&
               error == "candidate changed prompt metadata",
           backend_name + ": metadata-only candidate revision was admitted");

    const auto pinned_after = build_exact_prompt(store, policy, baseline_candidate, error);
    expect(pinned_after && pinned_after->text_digest == baseline_digest &&
               pinned_after->text == pinned_before->text,
           backend_name + ": pinned baseline changed after current edits");

    PromptCandidate missing_required = baseline_candidate;
    missing_required.documents.front().selected_blocks.erase(
        missing_required.documents.front().selected_blocks.begin());
    expect(!build_exact_prompt(store, policy, missing_required, error) &&
               error == "required prompt block is missing",
           backend_name + ": missing required block was not rejected");

    PromptCandidate unknown_block = baseline_candidate;
    unknown_block.documents.front().selected_blocks.front() = ContentBlockId{"unknown"};
    expect(!build_exact_prompt(store, policy, unknown_block, error) &&
               error == "candidate references an unknown prompt block",
           backend_name + ": unknown candidate block was accepted");

    PromptCandidate duplicate_block = baseline_candidate;
    duplicate_block.documents.front().selected_blocks[1] = ContentBlockId{"role"};
    expect(!build_exact_prompt(store, policy, duplicate_block, error) &&
               error == "candidate references a prompt block more than once",
           backend_name + ": duplicate candidate block was accepted");

    PromptCandidate invalid_revision = baseline_candidate;
    invalid_revision.documents.front().revision = 99;
    expect(!build_exact_prompt(store, policy, invalid_revision, error),
           backend_name + ": invalid pinned revision was accepted");

    PromptCandidate wrong_baseline = baseline_candidate;
    wrong_baseline.baseline_id = "forged-baseline";
    expect(!build_exact_prompt(store, policy, wrong_baseline, error),
           backend_name + ": wrong baseline was accepted");

    PromptPolicy empty_policy;
    expect(!build_exact_prompt(store, empty_policy, baseline_candidate, error),
           backend_name + ": empty policy was accepted");
    PromptCandidate empty_candidate;
    expect(!build_exact_prompt(store, policy, empty_candidate, error),
           backend_name + ": empty candidate was accepted");

    PromptPolicy same_text_policy = policy;
    same_text_policy.policy_id = "policy-v0-with-new-id";
    const auto same_text = build_exact_prompt(store, same_text_policy, baseline_candidate, error);
    expect(same_text && same_text->text_digest == pinned_before->text_digest &&
               same_text->config_digest != pinned_before->config_digest,
           backend_name + ": policy identity was absent from resolved configuration digest");

    PromptPolicy mixed_policy = policy;
    mixed_policy.policy_id = "policy-mixed";
    mixed_policy.documents.push_back(
        {second_document_id, 0, policy.documents.front().blocks});
    PromptCandidate mixed_candidate{
        "baseline-v0",
        {{document_id, 0, baseline_candidate.documents.front().selected_blocks},
         {second_document_id, 99, baseline_candidate.documents.front().selected_blocks}}};
    expect(!build_exact_prompt(store, mixed_policy, mixed_candidate, error),
           backend_name + ": mixed-document revision mismatch was accepted");

    error = "stale error";
    expect(build_exact_prompt(store, policy, baseline_candidate, error).has_value() && error.empty(),
           backend_name + ": successful build did not clear stale error");

    expect(store.read_current(document_id)->revision == 3,
           backend_name + ": current revision did not advance through edits");
}

} // namespace

int main() {
    try {
        agent_memory::InMemoryCanonicalContentStore in_memory;
        run_prompt_profile_suite(in_memory, in_memory, "in-memory");

#if AGENT_MEMORY_HAS_MDBX
        const auto path = std::filesystem::temp_directory_path() /
                          "agent_memory_prompt_profile_experiment.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);
        auto context = agent_memory::MdbxStorageContext::open(path.string());
        agent_memory::MdbxCanonicalContentStoreOptions options;
        options.context = std::move(context);
        agent_memory::MdbxCanonicalContentStore mdbx(std::move(options));
        run_prompt_profile_suite(mdbx, mdbx, "mdbx-c1");
        std::filesystem::remove(path, error);
#endif
        std::cout << "canonical prompt profile contract: PASS\n";
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
