#include "MdbxCanonicalContentRouter.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <utility>
namespace agent_memory {

MdbxCanonicalContentRouter::MdbxCanonicalContentRouter(
    MdbxWorkspaceStorageRegistry& registry) noexcept
    : m_registry(&registry) {}

const MdbxCanonicalContentStore& MdbxCanonicalContentRouter::store(
    const std::string& workspace_id, std::uint64_t placement_generation) const {
    return *m_registry->resolve(workspace_id, placement_generation)->canonical_content();
}

MdbxCanonicalContentStore& MdbxCanonicalContentRouter::mutable_store(
    const std::string& workspace_id, std::uint64_t placement_generation) {
    return *m_registry->resolve(workspace_id, placement_generation)
                ->mutable_canonical_content();
}

bool MdbxCanonicalContentRouter::create_document(const std::string& workspace_id,
                                                 std::uint64_t placement_generation,
                                                 CanonicalDocumentRevision initial) {
    return mutable_store(workspace_id, placement_generation).create_document(std::move(initial));
}

std::optional<CanonicalDocumentRevision> MdbxCanonicalContentRouter::read_current(
    const std::string& workspace_id,
    std::uint64_t placement_generation,
    const DocumentId& document_id) const {
    return store(workspace_id, placement_generation).read_current(document_id);
}

std::optional<CanonicalDocumentRevision> MdbxCanonicalContentRouter::read_revision(
    const std::string& workspace_id,
    std::uint64_t placement_generation,
    const DocumentId& document_id,
    std::uint64_t revision) const {
    return store(workspace_id, placement_generation).read_revision(document_id, revision);
}

std::optional<ContentBlock> MdbxCanonicalContentRouter::read_block(
    const std::string& workspace_id,
    std::uint64_t placement_generation,
    const DocumentId& document_id,
    const ContentBlockId& block_id,
    std::optional<std::uint64_t> revision) const {
    return store(workspace_id, placement_generation)
        .read_block(document_id, block_id, revision);
}

std::optional<std::string> MdbxCanonicalContentRouter::materialize_markdown(
    const std::string& workspace_id,
    std::uint64_t placement_generation,
    const DocumentId& document_id,
    std::optional<std::uint64_t> revision) const {
    return store(workspace_id, placement_generation).materialize_markdown(document_id, revision);
}

CanonicalEditResult MdbxCanonicalContentRouter::commit(
    const std::string& workspace_id,
    std::uint64_t placement_generation,
    const CanonicalEditRequest& request) {
    return mutable_store(workspace_id, placement_generation).commit(request);
}

} // namespace agent_memory
#endif
