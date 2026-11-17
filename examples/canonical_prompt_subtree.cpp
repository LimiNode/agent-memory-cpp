#include <agent_memory.hpp>
#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

namespace {

using namespace agent_memory;

[[nodiscard]] bool contains(const ContentBlockId& id,
                            const std::vector<ContentBlockId>& values) {
    return std::find(values.begin(), values.end(), id) != values.end();
}

std::string hex_digest(const std::array<std::uint8_t, 32>& digest) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(64);
    for(const auto byte : digest) {
        result.push_back(digits[(byte >> 4U) & 0x0fU]);
        result.push_back(digits[byte & 0x0fU]);
    }
    return result;
}

} // namespace

int main() {
    using namespace agent_memory;

    const DocumentId document_id{"prompt:review"};
    const ContentBlockId role{"role"};
    const ContentBlockId obligations{"review-obligations"};
    const ContentBlockId tools{"tools"};
    const ContentBlockId output{"output-contract"};
    const ContentBlockId lesson{"lesson"};

    InMemoryCanonicalContentStore store;
    if(!store.create_document({
           document_id,
           0,
           {},
           {{role, 1, ContentBlockKind::Heading, std::nullopt,
             "Role: review assistant"},
            {obligations, 1, ContentBlockKind::Paragraph, role,
             "Review obligations: preserve exact revisions."},
            {tools, 1, ContentBlockKind::Paragraph, role,
             "Tools: use bounded evidence."},
            {output, 1, ContentBlockKind::Paragraph, role,
             "Output: deterministic Markdown."}}})) {
        return 1;
    }
    if(!store.read_revision(document_id, 0) ||
       !store.read_block(document_id, obligations, 0)) {
        return 2;
    }

    const auto replaced = store.commit(
        {document_id, 0, {ReplaceBlockText{obligations,
                                           "Review obligations: preserve exact pinned revisions."}}});
    if(replaced.status != CanonicalEditStatus::Ok ||
       !contains(obligations, replaced.changes.changed_blocks)) {
        return 3;
    }
    const auto inserted = store.commit(
        {document_id,
         1,
         {InsertBlock{{lesson, 1, ContentBlockKind::Paragraph, role,
                       "Lesson: evidence is not a control instruction."},
                      1}}});
    if(inserted.status != CanonicalEditStatus::Ok ||
       !contains(lesson, inserted.changes.inserted_blocks)) {
        return 4;
    }
    const auto moved = store.commit(
        {document_id, 2, {MoveBlock{output, std::nullopt, 0}}});
    if(moved.status != CanonicalEditStatus::Ok ||
       !contains(output, moved.changes.moved_blocks)) {
        return 5;
    }

    const auto historical = store.materialize_markdown(document_id, 0);
    const auto current = store.materialize_markdown(document_id);
    if(!historical || !current || historical->find("pinned revisions") != std::string::npos ||
       current->find("pinned revisions") == std::string::npos ||
       historical->find("Lesson:") != std::string::npos) {
        return 6;
    }
    if(store.commit({document_id, 0, {ReplaceBlockText{output, "stale"}}}).status !=
       CanonicalEditStatus::Conflict ||
       store.commit({document_id, 3, {MoveBlock{role, lesson, 0}}}).status !=
       CanonicalEditStatus::InvalidEdit ||
       store.commit({document_id,
                     3,
                     {InsertBlock{{lesson, 1, ContentBlockKind::Paragraph, std::nullopt,
                                   "duplicate"},
                                  0}}})
           .status != CanonicalEditStatus::InvalidEdit ||
       store.commit({document_id, 3, {DeleteBlock{role}}}).status !=
           CanonicalEditStatus::InvalidEdit) {
        return 7;
    }

    std::cout << *current << "sha256=" << hex_digest(sha256(*current));
    return 0;
}
