#include "CanonicalNavigation.hpp"

#include <algorithm>
#include <map>
#include <set>

namespace agent_memory {
namespace {

bool validate_document(const CanonicalDocumentRevision& document, std::string& message) {
    if(document.document_id.empty()) {
        message = "document identity or revision is invalid";
        return false;
    }
    std::set<ContentBlockId> seen;
    for(const auto& block : document.blocks) {
        if(block.id.empty() || !seen.insert(block.id).second) {
            message = "document contains an empty or duplicate block id";
            return false;
        }
        if(block.parent_id && seen.count(*block.parent_id) == 0U) {
            message = "document blocks are not in canonical preorder";
            return false;
        }
    }
    std::vector<ContentBlockId> preorder;
    std::set<ContentBlockId> emitted;
    const auto append_subtree = [&](const auto& self, const ContentBlockId& id) -> void {
        if(!emitted.insert(id).second) return;
        preorder.push_back(id);
        for(const auto& candidate : document.blocks) {
            if(candidate.parent_id && *candidate.parent_id == id) {
                self(self, candidate.id);
            }
        }
    };
    for(const auto& block : document.blocks) {
        if(!block.parent_id) append_subtree(append_subtree, block.id);
    }
    if(preorder.size() != document.blocks.size()) {
        message = "canonical preorder is incomplete";
        return false;
    }
    for(std::size_t index = 0; index < document.blocks.size(); ++index) {
        if(preorder[index] != document.blocks[index].id) {
            message = "blocks must be stored in canonical tree preorder";
            return false;
        }
    }
    return true;
}

std::size_t depth_of(const std::map<ContentBlockId, const ContentBlock*>& blocks,
                     const ContentBlock& block) {
    std::size_t depth = 0;
    auto parent = block.parent_id;
    std::set<ContentBlockId> visited;
    while(parent) {
        if(!visited.insert(*parent).second) return 0;
        const auto it = blocks.find(*parent);
        if(it == blocks.end()) return 0;
        ++depth;
        parent = it->second->parent_id;
    }
    return depth;
}

} // namespace

std::optional<CanonicalOutline> build_canonical_outline(
    const CanonicalDocumentRevision& document, std::size_t max_entries) {
    std::string error;
    if(max_entries == 0U || document.blocks.size() > max_entries ||
       !validate_document(document, error)) return std::nullopt;

    std::map<ContentBlockId, const ContentBlock*> blocks;
    for(const auto& block : document.blocks) blocks.emplace(block.id, &block);
    CanonicalOutline result{document.document_id, document.revision, {}};
    result.entries.reserve(document.blocks.size());
    for(const auto& block : document.blocks) {
        std::size_t child_count = 0U;
        for(const auto& child : document.blocks) {
            if(child.parent_id && *child.parent_id == block.id) ++child_count;
        }
        result.entries.push_back({
            {document.document_id, document.revision, block.id, block.revision},
            block.kind,
            block.parent_id,
            depth_of(blocks, block),
            block.text.size(),
            child_count
        });
    }
    return result;
}

CanonicalSubtreeResult read_canonical_subtree(
    const CanonicalDocumentRevision& document,
    const CanonicalSubtreeRequest& request) {
    CanonicalSubtreeResult result;
    result.document_id = document.document_id;
    result.document_revision = document.revision;
    std::string error;
    if(request.max_blocks == 0U || !validate_document(document, error)) {
        result.status = CanonicalNavigationStatus::InvalidDocument;
        result.message = request.max_blocks == 0U ? "max_blocks must be non-zero" : std::move(error);
        return result;
    }

    std::size_t start = 0U;
    if(request.root_id) {
        const auto it = std::find_if(document.blocks.begin(), document.blocks.end(),
            [&](const ContentBlock& block) { return block.id == *request.root_id; });
        if(it == document.blocks.end()) {
            result.status = CanonicalNavigationStatus::NotFound;
            result.message = "subtree root was not found";
            return result;
        }
        start = static_cast<std::size_t>(std::distance(document.blocks.begin(), it));
    }

    const auto root = request.root_id ? std::optional<ContentBlockId>(*request.root_id) : std::nullopt;
    std::size_t text_bytes = 0U;
    for(std::size_t index = start; index < document.blocks.size(); ++index) {
        const auto& block = document.blocks[index];
        if(root) {
            bool in_subtree = block.id == *root;
            auto parent = block.parent_id;
            std::set<ContentBlockId> visited;
            while(!in_subtree && parent) {
                if(!visited.insert(*parent).second) break;
                if(*parent == *root) in_subtree = true;
                const auto parent_it = std::find_if(document.blocks.begin(), document.blocks.end(),
                    [&](const ContentBlock& candidate) { return candidate.id == *parent; });
                if(parent_it == document.blocks.end()) break;
                parent = parent_it->parent_id;
            }
            if(!in_subtree) {
                if(!result.blocks.empty()) break;
                continue;
            }
        }
        if(result.blocks.size() >= request.max_blocks ||
           (request.include_text && (block.text.size() > request.max_text_bytes ||
            text_bytes > request.max_text_bytes - block.text.size()))) {
            result.status = CanonicalNavigationStatus::BudgetExceeded;
            result.truncated = true;
            result.message = "subtree budget exhausted";
            return result;
        }
        ContentBlock copy = block;
        if(!request.include_text) copy.text.clear();
        result.blocks.push_back(std::move(copy));
        if(request.include_text) text_bytes += block.text.size();
    }
    result.status = CanonicalNavigationStatus::Ok;
    return result;
}

} // namespace agent_memory
