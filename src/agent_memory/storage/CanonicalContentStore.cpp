#include "CanonicalContentStore.hpp"
#include "CanonicalContentStoreInternal.hpp"

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <type_traits>
#include <utility>
#include <vector>

namespace agent_memory {

    ICanonicalContentStore::~ICanonicalContentStore() = default;
    ICanonicalContentEditor::~ICanonicalContentEditor() = default;

    struct InMemoryCanonicalContentStore::DocumentHistory final {
        std::map<std::uint64_t, CanonicalDocumentRevision> revisions;
        std::set<ContentBlockId> all_seen_block_ids;
    };

    struct InMemoryCanonicalContentStore::Impl final {
        mutable std::mutex mutex;
        std::map<DocumentId, DocumentHistory> documents;
    };

    namespace {

        bool same_metadata(const Metadata& lhs, const Metadata& rhs) {
            return lhs.values() == rhs.values();
        }

        std::size_t find_index(const std::vector<ContentBlock>& blocks,
            const ContentBlockId& id) {
            const auto it = std::find_if(blocks.begin(), blocks.end(), [&](const ContentBlock& block) {
                return block.id == id;
            });
            return it == blocks.end()
                ? blocks.size()
                : static_cast<std::size_t>(std::distance(blocks.begin(), it));
        }

        bool has_id(const std::vector<ContentBlock>& blocks, const ContentBlockId& id) {
            return find_index(blocks, id) != blocks.size();
        }

        ContentBlock* find_block(std::vector<ContentBlock>& blocks, const ContentBlockId& id) {
            const auto it = std::find_if(blocks.begin(), blocks.end(), [&](const ContentBlock& block) {
                return block.id == id;
            });
            return it == blocks.end() ? nullptr : &*it;
        }

        const ContentBlock* find_block(const std::vector<ContentBlock>& blocks,
            const ContentBlockId& id) {
            const auto it = std::find_if(blocks.begin(), blocks.end(), [&](const ContentBlock& block) {
                return block.id == id;
            });
            return it == blocks.end() ? nullptr : &*it;
        }

        bool is_descendant(const std::vector<ContentBlock>& blocks,
            const ContentBlockId& candidate,
            const ContentBlockId& ancestor) {
            const ContentBlock* current = find_block(blocks, candidate);
            std::set<ContentBlockId> visited;
            while(current && current->parent_id) {
                if(!visited.insert(current->id).second) {
                    return true;
                }
                if(*current->parent_id == ancestor) {
                    return true;
                }
                current = find_block(blocks, *current->parent_id);
            }
            return false;
        }

        std::size_t subtree_end_index(const std::vector<ContentBlock>& blocks,
            std::size_t root_index) {
            if(root_index == blocks.size()) {
                return root_index;
            }
            const auto root_id = blocks[root_index].id;
            std::size_t index = root_index + 1;
            while(index < blocks.size() && is_descendant(blocks, blocks[index].id, root_id)) {
                ++index;
            }
            return index;
        }

        std::vector<std::size_t> direct_child_indices(
            const std::vector<ContentBlock>& blocks,
            const std::optional<ContentBlockId>& parent) {
            std::vector<std::size_t> result;
            for(std::size_t index = 0; index < blocks.size(); ++index) {
                if(blocks[index].parent_id == parent) {
                    result.push_back(index);
                }
            }
            return result;
        }

        std::size_t insertion_index(const std::vector<ContentBlock>& blocks,
            const std::optional<ContentBlockId>& parent,
            std::size_t sibling_position) {
            const auto children = direct_child_indices(blocks, parent);
            if(sibling_position < children.size()) {
                return children[sibling_position];
            }
            if(parent) {
                return subtree_end_index(blocks, find_index(blocks, *parent));
            }
            return blocks.size();
        }

        std::size_t sibling_ordinal(const std::vector<ContentBlock>& blocks,
            const ContentBlockId& id) {
            const auto index = find_index(blocks, id);
            if(index == blocks.size()) {
                return blocks.size();
            }
            const auto parent = blocks[index].parent_id;
            std::size_t ordinal = 0;
            for(std::size_t prior = 0; prior < index; ++prior) {
                if(blocks[prior].parent_id == parent) {
                    ++ordinal;
                }
            }
            return ordinal;
        }

        std::size_t common_sibling_ordinal(const std::vector<ContentBlock>& blocks,
            const std::vector<ContentBlock>& other,
            const ContentBlock& block) {
            std::size_t ordinal = 0;
            for(const auto& prior : blocks) {
                if(prior.id == block.id) {
                    break;
                }
                if(prior.parent_id == block.parent_id) {
                    const auto* other_block = find_block(other, prior.id);
                    if(other_block && other_block->parent_id == block.parent_id) {
                        ++ordinal;
                    }
                }
            }
            return ordinal;
        }

        bool valid_tree(const std::vector<ContentBlock>& blocks, std::string& message) {
            std::set<ContentBlockId> seen;
            for(const auto& block : blocks) {
                if(block.id.empty()) {
                    message = "block id must not be empty";
                    return false;
                }
                if(!seen.insert(block.id).second) {
                    message = "duplicate active block id";
                    return false;
                }
                if(block.parent_id && !has_id(blocks, *block.parent_id)) {
                    message = "parent block does not exist";
                    return false;
                }
            }

            for(const auto& block : blocks) {
                std::set<ContentBlockId> path;
                auto current = block.parent_id;
                while(current) {
                    if(*current == block.id || !path.insert(*current).second) {
                        message = "structural cycle detected";
                        return false;
                    }
                    const auto* parent = find_block(blocks, *current);
                    if(!parent) {
                        message = "parent block does not exist";
                        return false;
                    }
                    current = parent->parent_id;
                }
            }

            std::vector<ContentBlockId> preorder;
            std::set<ContentBlockId> emitted;
            const auto append_subtree = [&](const auto& self, const ContentBlockId& id) -> void {
                if(!emitted.insert(id).second) {
                    return;
                }
                preorder.push_back(id);
                for(const auto& child : blocks) {
                    if(child.parent_id && *child.parent_id == id) {
                        self(self, child.id);
                    }
                }
            };
            for(const auto& block : blocks) {
                if(!block.parent_id) {
                    append_subtree(append_subtree, block.id);
                }
            }
            if(preorder.size() != blocks.size()) {
                message = "canonical preorder is incomplete";
                return false;
            }
            for(std::size_t index = 0; index < blocks.size(); ++index) {
                if(preorder[index] != blocks[index].id) {
                    message = "blocks must be stored in canonical tree preorder";
                    return false;
                }
            }
            return true;
        }

        bool same_logical_state(const CanonicalDocumentRevision& lhs,
            const CanonicalDocumentRevision& rhs) {
            if(!same_metadata(lhs.metadata, rhs.metadata) || lhs.blocks.size() != rhs.blocks.size()) {
                return false;
            }
            for(std::size_t index = 0; index < lhs.blocks.size(); ++index) {
                const auto& left = lhs.blocks[index];
                const auto& right = rhs.blocks[index];
                if(left.id != right.id || left.kind != right.kind ||
                    left.parent_id != right.parent_id || left.text != right.text) {
                    return false;
                }
            }
            return true;
        }

        CanonicalEditResult invalid(std::string message) {
            CanonicalEditResult result;
            result.status = CanonicalEditStatus::InvalidEdit;
            result.message = std::move(message);
            return result;
        }

        struct NetDiff final {
            std::set<ContentBlockId> changed;
            std::set<ContentBlockId> inserted;
            std::set<ContentBlockId> removed;
            std::set<ContentBlockId> moved;
        };

        NetDiff derive_net_diff(const CanonicalDocumentRevision& before,
            const CanonicalDocumentRevision& after) {
            NetDiff diff;
            for(const auto& block : after.blocks) {
                const auto* old_block = find_block(before.blocks, block.id);
                if(!old_block) {
                    diff.inserted.insert(block.id);
                    continue;
                }
                if(old_block->kind != block.kind || old_block->text != block.text) {
                    diff.changed.insert(block.id);
                }
                if(old_block->parent_id != block.parent_id ||
                    common_sibling_ordinal(before.blocks, after.blocks, *old_block) !=
                        common_sibling_ordinal(after.blocks, before.blocks, block)) {
                    diff.moved.insert(block.id);
                }
            }
            for(const auto& block : before.blocks) {
                if(!has_id(after.blocks, block.id)) {
                    diff.removed.insert(block.id);
                }
            }
            return diff;
        }

        std::size_t block_depth(const std::vector<ContentBlock>& blocks,
            const ContentBlock& block) {
            std::size_t depth = 1;
            auto current = block.parent_id;
            std::set<ContentBlockId> visited;
            while(current && visited.insert(*current).second) {
                ++depth;
                const auto* parent = find_block(blocks, *current);
                current = parent ? parent->parent_id : std::nullopt;
            }
            return depth;
        }

        std::string code_fence(const std::string& text) {
            std::size_t longest_run = 0;
            std::size_t run = 0;
            for(const char character : text) {
                if(character == '`') {
                    ++run;
                    longest_run = std::max(longest_run, run);
                } else {
                    run = 0;
                }
            }
            return std::string(std::max<std::size_t>(3, longest_run + 1), '`');
        }

        std::string block_markdown(const std::vector<ContentBlock>& blocks,
            const ContentBlock& block) {
            switch(block.kind) {
            case ContentBlockKind::Heading:
                return std::string(std::min<std::size_t>(6, block_depth(blocks, block)), '#') + " " + block.text;
            case ContentBlockKind::Code: {
                const auto fence = code_fence(block.text);
                return fence + "\n" + block.text + "\n" + fence;
            }
            case ContentBlockKind::Paragraph:
                return block.text;
            }
            return block.text;
        }

    } // namespace

    InMemoryCanonicalContentStore::InMemoryCanonicalContentStore()
        : m_impl(std::make_unique<Impl>()) {}

    InMemoryCanonicalContentStore::~InMemoryCanonicalContentStore() = default;

    bool detail::CanonicalContentHistoryLoader::restore_current(
        InMemoryCanonicalContentStore& store,
        CanonicalDocumentRevision current,
        std::set<ContentBlockId> all_seen_block_ids) {
        std::lock_guard<std::mutex> lock(store.m_impl->mutex);
        if(current.document_id.empty() ||
            store.m_impl->documents.find(current.document_id) != store.m_impl->documents.end()) {
            return false;
        }

        InMemoryCanonicalContentStore::DocumentHistory history;
        std::string message;
        if(!valid_tree(current.blocks, message)) {
            return false;
        }
        for(const auto& block : current.blocks) {
            if(all_seen_block_ids.count(block.id) == 0) {
                return false;
            }
        }

        const auto document_id = current.document_id;
        history.revisions.emplace(current.revision, std::move(current));
        history.all_seen_block_ids = std::move(all_seen_block_ids);
        store.m_impl->documents.emplace(document_id, std::move(history));
        return true;
    }

    bool InMemoryCanonicalContentStore::create_document(CanonicalDocumentRevision initial) {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        if(initial.document_id.empty() || initial.revision != 0 ||
            m_impl->documents.find(initial.document_id) != m_impl->documents.end()) {
            return false;
        }
        std::string message;
        if(!valid_tree(initial.blocks, message)) {
            return false;
        }
        for(auto& block : initial.blocks) {
            block.revision = 1;
        }
        DocumentHistory history;
        for(const auto& block : initial.blocks) {
            history.all_seen_block_ids.insert(block.id);
        }
        history.revisions.emplace(0, initial);
        m_impl->documents.emplace(initial.document_id, std::move(history));
        return true;
    }

    std::optional<CanonicalDocumentRevision> InMemoryCanonicalContentStore::read_current(
        const DocumentId& document_id) const {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        const auto document = m_impl->documents.find(document_id);
        if(document == m_impl->documents.end() || document->second.revisions.empty()) {
            return std::nullopt;
        }
        return document->second.revisions.rbegin()->second;
    }

    std::optional<CanonicalDocumentRevision> InMemoryCanonicalContentStore::read_revision(
        const DocumentId& document_id, std::uint64_t revision) const {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        const auto document = m_impl->documents.find(document_id);
        if(document == m_impl->documents.end()) {
            return std::nullopt;
        }
        const auto state = document->second.revisions.find(revision);
        return state == document->second.revisions.end()
            ? std::nullopt : std::optional<CanonicalDocumentRevision>(state->second);
    }

    std::optional<ContentBlock> InMemoryCanonicalContentStore::read_block(
        const DocumentId& document_id, const ContentBlockId& block_id,
        std::optional<std::uint64_t> revision) const {
        const auto state = revision ? read_revision(document_id, *revision)
            : read_current(document_id);
        if(!state) {
            return std::nullopt;
        }
        const auto* block = find_block(state->blocks, block_id);
        return block ? std::optional<ContentBlock>(*block) : std::nullopt;
    }

    std::optional<std::string> InMemoryCanonicalContentStore::materialize_markdown(
        const DocumentId& document_id, std::optional<std::uint64_t> revision) const {
        const auto state = revision ? read_revision(document_id, *revision)
            : read_current(document_id);
        if(!state) {
            return std::nullopt;
        }
        std::ostringstream output;
        for(std::size_t index = 0; index < state->blocks.size(); ++index) {
            if(index != 0) {
                output << "\n\n";
            }
            output << block_markdown(state->blocks, state->blocks[index]);
        }
        if(!state->blocks.empty()) {
            output << '\n';
        }
        return output.str();
    }

    CanonicalEditResult InMemoryCanonicalContentStore::commit(
        const CanonicalEditRequest& request) {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        const auto document = m_impl->documents.find(request.document_id);
        if(document == m_impl->documents.end()) {
            CanonicalEditResult result;
            result.status = CanonicalEditStatus::NotFound;
            result.message = "document not found";
            return result;
        }
        auto& history = document->second;
        const auto& current = history.revisions.rbegin()->second;
        if(current.revision != request.expected_revision) {
            CanonicalEditResult result;
            result.status = CanonicalEditStatus::Conflict;
            result.message = "expected revision does not match current revision";
            return result;
        }

        CanonicalDocumentRevision candidate = current;
        std::set<ContentBlockId> reserved_ids;
        for(const auto& operation : request.operations) {
            const auto error = std::visit([&](const auto& op) -> std::optional<std::string> {
                using Operation = std::decay_t<decltype(op)>;
                if constexpr(std::is_same_v<Operation, ReplaceBlockText>) {
                    auto* block = find_block(candidate.blocks, op.block_id);
                    if(!block) return std::string("block not found");
                    block->text = op.text;
                } else if constexpr(std::is_same_v<Operation, InsertBlock>) {
                    if(op.block.id.empty() || has_id(candidate.blocks, op.block.id) ||
                        history.all_seen_block_ids.count(op.block.id) != 0 ||
                        reserved_ids.count(op.block.id) != 0) {
                        return std::string("block id has already been used");
                    }
                    if(op.block.parent_id && !has_id(candidate.blocks, *op.block.parent_id)) {
                        return std::string("parent block does not exist");
                    }
                    const auto child_count = direct_child_indices(candidate.blocks, op.block.parent_id).size();
                    if(op.position > child_count) {
                        return std::string("insert sibling position is out of range");
                    }
                    ContentBlock block = op.block;
                    block.revision = 1;
                    const auto index = insertion_index(candidate.blocks, block.parent_id, op.position);
                    candidate.blocks.insert(candidate.blocks.begin() + static_cast<std::ptrdiff_t>(index), std::move(block));
                    reserved_ids.insert(op.block.id);
                } else if constexpr(std::is_same_v<Operation, DeleteBlock>) {
                    const auto* block = find_block(candidate.blocks, op.block_id);
                    if(!block) return std::string("block not found");
                    const bool has_children = std::any_of(candidate.blocks.begin(), candidate.blocks.end(), [&](const ContentBlock& child) {
                        return child.parent_id && *child.parent_id == op.block_id;
                    });
                    if(has_children) return std::string("cannot delete a block with children");
                    candidate.blocks.erase(std::remove_if(candidate.blocks.begin(), candidate.blocks.end(), [&](const ContentBlock& value) {
                        return value.id == op.block_id;
                    }), candidate.blocks.end());
                } else if constexpr(std::is_same_v<Operation, MoveBlock>) {
                    auto* block = find_block(candidate.blocks, op.block_id);
                    if(!block) return std::string("block not found");
                    if(op.parent_id && !has_id(candidate.blocks, *op.parent_id)) {
                        return std::string("parent block does not exist");
                    }
                    if(op.parent_id && (*op.parent_id == op.block_id || is_descendant(candidate.blocks, *op.parent_id, op.block_id))) {
                        return std::string("move would create a structural cycle");
                    }
                    if(block->parent_id == op.parent_id && sibling_ordinal(candidate.blocks, op.block_id) == op.position) {
                        return std::optional<std::string>{};
                    }
                    std::vector<ContentBlock> subtree;
                    std::vector<ContentBlock> remaining;
                    for(const auto& value : candidate.blocks) {
                        if(value.id == op.block_id || is_descendant(candidate.blocks, value.id, op.block_id)) {
                            subtree.push_back(value);
                        } else {
                            remaining.push_back(value);
                        }
                    }
                    const auto child_count = direct_child_indices(remaining, op.parent_id).size();
                    if(op.position > child_count) {
                        return std::string("move sibling position is out of range");
                    }
                    auto moved_root = std::find_if(subtree.begin(), subtree.end(), [&](const ContentBlock& value) {
                        return value.id == op.block_id;
                    });
                    moved_root->parent_id = op.parent_id;
                    const auto index = insertion_index(remaining, moved_root->parent_id, op.position);
                    remaining.insert(remaining.begin() + static_cast<std::ptrdiff_t>(index),
                        std::make_move_iterator(subtree.begin()), std::make_move_iterator(subtree.end()));
                    candidate.blocks = std::move(remaining);
                } else if constexpr(std::is_same_v<Operation, UpdateDocumentMetadata>) {
                    candidate.metadata = op.metadata;
                }
                return std::optional<std::string>{};
            }, operation);
            if(error) {
                return invalid(*error);
            }
        }

        std::string tree_message;
        if(!valid_tree(candidate.blocks, tree_message)) {
            return invalid(tree_message);
        }
        const auto diff = derive_net_diff(current, candidate);
        if(same_metadata(current.metadata, candidate.metadata) && diff.changed.empty() &&
            diff.inserted.empty() && diff.removed.empty() && diff.moved.empty()) {
            CanonicalEditResult result;
            result.status = CanonicalEditStatus::NoChange;
            result.revision = current;
            result.changes.old_revision = current.revision;
            return result;
        }

        candidate.revision = current.revision + 1;
        for(auto& block : candidate.blocks) {
            const auto* old_block = find_block(current.blocks, block.id);
            if(!old_block) {
                block.revision = 1;
            } else if(diff.changed.count(block.id) != 0 || diff.moved.count(block.id) != 0) {
                block.revision = old_block->revision + 1;
            } else {
                block.revision = old_block->revision;
            }
        }

        ContentChangeSet changes;
        changes.old_revision = current.revision;
        changes.new_revision = candidate.revision;
        changes.changed_blocks.assign(diff.changed.begin(), diff.changed.end());
        changes.inserted_blocks.assign(diff.inserted.begin(), diff.inserted.end());
        changes.removed_blocks.assign(diff.removed.begin(), diff.removed.end());
        changes.moved_blocks.assign(diff.moved.begin(), diff.moved.end());
        changes.metadata_changed = !same_metadata(current.metadata, candidate.metadata);
        changes.structure_changed = !diff.inserted.empty() || !diff.removed.empty() || !diff.moved.empty();

        for(const auto& block : candidate.blocks) {
            history.all_seen_block_ids.insert(block.id);
        }
        history.revisions.emplace(candidate.revision, candidate);

        CanonicalEditResult result;
        result.status = CanonicalEditStatus::Ok;
        result.revision = candidate;
        result.changes = std::move(changes);
        return result;
    }

} // namespace agent_memory
