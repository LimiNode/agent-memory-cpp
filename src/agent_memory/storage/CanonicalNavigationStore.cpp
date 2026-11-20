#include "CanonicalNavigationStore.hpp"

#include <algorithm>
#include <limits>
#include <utility>

namespace agent_memory {
namespace {

bool matches_pin(const CanonicalDocumentRevision& document,
                const DocumentId& document_id,
                std::uint64_t revision) noexcept {
    return document.document_id == document_id && document.revision == revision;
}

CanonicalChildrenResult invalid_result(const DocumentId& document_id,
                                       std::uint64_t revision,
                                       std::optional<ContentBlockId> parent_id,
                                       std::string message) {
    CanonicalChildrenResult result;
    result.document_id = document_id;
    result.document_revision = revision;
    result.parent_id = std::move(parent_id);
    result.message = std::move(message);
    return result;
}

} // namespace

CanonicalNavigationStore::CanonicalNavigationStore(
    const ICanonicalContentStore& store) noexcept
    : m_store(&store) {}

std::optional<CanonicalOutline> CanonicalNavigationStore::read_outline(
    const DocumentId& document_id,
    std::uint64_t revision,
    std::size_t max_entries) const {
    const auto document = m_store->read_revision(document_id, revision);
    if(!document || !matches_pin(*document, document_id, revision)) {
        return std::nullopt;
    }
    return build_canonical_outline(*document, max_entries);
}

CanonicalChildrenResult CanonicalNavigationStore::read_children(
    const DocumentId& document_id,
    std::uint64_t revision,
    std::optional<ContentBlockId> parent_id,
    std::size_t max_children) const {
    auto result = invalid_result(document_id, revision, parent_id, {});
    const auto document = m_store->read_revision(document_id, revision);
    if(!document) {
        result.status = CanonicalNavigationStatus::NotFound;
        result.message = "document revision was not found";
        return result;
    }
    if(!matches_pin(*document, document_id, revision)) {
        result.message = "storage returned a mismatched document revision";
        return result;
    }
    if(max_children == 0U) {
        result.message = "max_children must be non-zero";
        return result;
    }

    const auto outline = build_canonical_outline(
        *document, std::numeric_limits<std::size_t>::max());
    if(!outline) {
        result.message = "document is malformed or exceeds navigation bounds";
        return result;
    }
    if(parent_id) {
        const auto parent = std::find_if(
            document->blocks.begin(), document->blocks.end(),
            [&](const ContentBlock& block) { return block.id == *parent_id; });
        if(parent == document->blocks.end()) {
            result.status = CanonicalNavigationStatus::NotFound;
            result.message = "parent block was not found";
            return result;
        }
    }

    result.document_id = document_id;
    result.document_revision = revision;
    result.status = CanonicalNavigationStatus::Ok;
    for(const auto& entry : outline->entries) {
        if(entry.parent_id != parent_id) {
            continue;
        }
        if(result.entries.size() == max_children) {
            result.status = CanonicalNavigationStatus::BudgetExceeded;
            result.truncated = true;
            result.message = "children budget exhausted";
            break;
        }
        result.entries.push_back(entry);
    }
    return result;
}

CanonicalSubtreeResult CanonicalNavigationStore::read_subtree(
    const DocumentId& document_id,
    std::uint64_t revision,
    const CanonicalSubtreeRequest& request) const {
    CanonicalSubtreeResult result;
    result.document_id = document_id;
    result.document_revision = revision;
    const auto document = m_store->read_revision(document_id, revision);
    if(!document) {
        result.status = CanonicalNavigationStatus::NotFound;
        result.message = "document revision was not found";
        return result;
    }
    if(!matches_pin(*document, document_id, revision)) {
        result.status = CanonicalNavigationStatus::InvalidDocument;
        result.message = "storage returned a mismatched document revision";
        return result;
    }
    return read_canonical_subtree(*document, request);
}

} // namespace agent_memory
