#include "MdbxWorkspaceStorageRegistry.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <cctype>
#include <utility>

namespace agent_memory {

namespace {

std::string normalize_table_prefix(std::string value) {
    if (value.empty())
        value = "agent_memory";
    for (char& character : value) {
        if (!std::isalnum(static_cast<unsigned char>(character)))
            character = '_';
    }
    return value;
}

} // namespace

MdbxWorkspacePlacement::MdbxWorkspacePlacement(
    std::string workspace_id,
    std::uint64_t generation,
    std::shared_ptr<MdbxStorageContext> context,
    std::shared_ptr<MdbxCanonicalContentStore> canonical_content,
    std::string table_prefix)
    : m_workspace_id(std::move(workspace_id)),
      m_generation(generation),
      m_context(std::move(context)),
      m_canonical_content(std::move(canonical_content)),
      m_table_prefix(std::move(table_prefix)) {}

const std::string& MdbxWorkspacePlacement::workspace_id() const noexcept {
    return m_workspace_id;
}

std::uint64_t MdbxWorkspacePlacement::generation() const noexcept {
    return m_generation;
}

const std::shared_ptr<MdbxStorageContext>& MdbxWorkspacePlacement::context() const noexcept {
    return m_context;
}

std::shared_ptr<const MdbxCanonicalContentStore>
MdbxWorkspacePlacement::canonical_content() const noexcept {
    return m_canonical_content;
}

const std::shared_ptr<MdbxCanonicalContentStore>&
MdbxWorkspacePlacement::mutable_canonical_content() const noexcept {
    return m_canonical_content;
}

const std::string& MdbxWorkspacePlacement::table_prefix() const noexcept {
    return m_table_prefix;
}

std::shared_ptr<const MdbxWorkspacePlacement> MdbxWorkspaceStorageRegistry::bind(
    std::string workspace_id,
    std::shared_ptr<MdbxStorageContext> context,
    std::uint64_t generation,
    std::string table_prefix) {
    if (workspace_id.empty())
        throw MdbxWorkspaceRoutingError("workspace placement requires a non-empty workspace id");
    if (!context)
        throw MdbxWorkspaceRoutingError("workspace placement requires an MDBX context");
    if (generation == 0)
        throw MdbxWorkspaceRoutingError("workspace placement generation must be non-zero");
    table_prefix = normalize_table_prefix(std::move(table_prefix));

    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_placements.find(workspace_id) != m_placements.end())
        throw MdbxPlacementConflictError("workspace placement is already bound: " + workspace_id);
    for (const auto& entry : m_placements) {
        const auto& existing = entry.second;
        if (existing->context()->connection().get() == context->connection().get() &&
            existing->table_prefix() == table_prefix) {
            throw MdbxPlacementConflictError(
                "workspace placement reuses an MDBX context/table namespace: " + workspace_id);
        }
    }

    MdbxCanonicalContentStoreOptions options;
    options.table_prefix = table_prefix;
    options.context = context;
    auto store = std::make_shared<MdbxCanonicalContentStore>(std::move(options));
    auto placement = std::shared_ptr<const MdbxWorkspacePlacement>(new MdbxWorkspacePlacement(
        workspace_id, generation, std::move(context), std::move(store), std::move(table_prefix)));
    m_placements.emplace(workspace_id, placement);
    return placement;
}

std::shared_ptr<const MdbxWorkspacePlacement> MdbxWorkspaceStorageRegistry::resolve(
    const std::string& workspace_id, std::uint64_t expected_generation) const {
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto found = m_placements.find(workspace_id);
    if (found == m_placements.end())
        throw MdbxUnknownWorkspaceError("unknown workspace placement: " + workspace_id);
    if (found->second->generation() != expected_generation)
        throw MdbxStalePlacementError("stale workspace placement generation: " + workspace_id);
    return found->second;
}

} // namespace agent_memory
#endif
