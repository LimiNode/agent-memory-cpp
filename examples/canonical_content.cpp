#include <agent_memory.hpp>

#include <iostream>
#include <optional>
#include <string>
#include <utility>
#include <vector>

int main() {
    using namespace agent_memory;

    InMemoryCanonicalContentStore store;
    const DocumentId document_id{"doc:notes"};
    const ContentBlockId heading{"heading"};
    const ContentBlockId body{"body"};

    CanonicalDocumentRevision initial{
        document_id,
        0,
        {},
        {
            ContentBlock{heading, 1, ContentBlockKind::Heading,
                std::nullopt, "Notes"},
            ContentBlock{body, 1, ContentBlockKind::Paragraph,
                heading, "First draft."}
        }
    };

    if (!store.create_document(std::move(initial))) {
        return 1;
    }

    std::vector<CanonicalEditOperation> operations;
    operations.emplace_back(ReplaceBlockText{body, "Edited paragraph."});
    const auto result = store.commit(
        CanonicalEditRequest{document_id, 0, std::move(operations)}
    );
    if (result.status != CanonicalEditStatus::Ok) {
        return 1;
    }

    const auto previous_markdown = store.materialize_markdown(document_id, 0);
    const auto current_markdown = store.materialize_markdown(document_id);
    if (!previous_markdown || !current_markdown ||
        previous_markdown == current_markdown ||
        previous_markdown->find("First draft.") == std::string::npos ||
        previous_markdown->find("Edited paragraph.") != std::string::npos ||
        current_markdown->find("Edited paragraph.") == std::string::npos ||
        current_markdown->find("First draft.") != std::string::npos) {
        return 1;
    }

    std::cout << "Before:\n" << *previous_markdown
              << "\nAfter:\n" << *current_markdown;
    return 0;
}
