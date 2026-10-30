#include "MdbxResourceFrontierSnapshot.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/common.hpp>

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace agent_memory {

namespace {

MdbxResourceManifestStorageOptions manifest_options(
    const std::shared_ptr<MdbxStorageContext>& context,
    const std::string& prefix
) {
    MdbxResourceManifestStorageOptions options;
    options.context = context;
    options.table_prefix = prefix;
    return options;
}

MdbxKnowledgeUnitStoreOptions unit_options(
    const std::shared_ptr<MdbxStorageContext>& context,
    const std::string& prefix
) {
    MdbxKnowledgeUnitStoreOptions options;
    options.context = context;
    options.table_prefix = prefix;
    return options;
}

bool same_digest(const ResourceBodyDigest& left, const ResourceBodyDigest& right) {
    return left.algorithm == right.algorithm && left.bytes == right.bytes;
}

std::shared_ptr<MdbxStorageContext> require_context(
    std::shared_ptr<MdbxStorageContext> context
) {
    if(!context) {
        throw std::invalid_argument("resource frontier reader requires an MDBX context");
    }
    return context;
}

} // namespace

MdbxResourceFrontierSnapshotReader::MdbxResourceFrontierSnapshotReader(
    std::shared_ptr<MdbxStorageContext> context,
    std::string table_prefix
) : m_context(std::move(context)),
    m_manifests(manifest_options(require_context(m_context), table_prefix)),
    m_units(unit_options(require_context(m_context), std::move(table_prefix))) {}

MdbxResourceFrontierSnapshotResult MdbxResourceFrontierSnapshotReader::read(
    const ScopeId& scope_id,
    const std::vector<MdbxResourceFrontierInput>& inputs
) const {
    if(scope_id.empty() || inputs.empty()) {
        return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
    }

    std::vector<KnowledgeUnitId> ids;
    ids.reserve(inputs.size());
    for(const auto& input : inputs) {
        if(input.unit_id.empty() || input.unit_revision == 0
           || input.root_revision.resource_id.empty()
           || input.root_revision.generation == 0) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }
        if(std::find(ids.begin(), ids.end(), input.unit_id) != ids.end()) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }
        ids.push_back(input.unit_id);
    }

    auto transaction = std::make_shared<mdbxc::Transaction>(
        m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY));
    MdbxResourceFrontierSnapshot snapshot;
    snapshot.scope_id = scope_id;
    snapshot.entries.reserve(inputs.size());

    for(const auto& input : inputs) {
        const auto unit = m_units.find_in_transaction(input.unit_id, *transaction);
        if(!unit) {
            return {MdbxResourceFrontierSnapshotStatus::MissingInput, std::nullopt};
        }
        if(unit->scope_id != scope_id) {
            return {MdbxResourceFrontierSnapshotStatus::ScopeMismatch, std::nullopt};
        }
        if(unit->lifecycle_state != KnowledgeUnitLifecycleState::Active) {
            return {MdbxResourceFrontierSnapshotStatus::InactiveInput, std::nullopt};
        }
        if(unit->revision != input.unit_revision) {
            return {MdbxResourceFrontierSnapshotStatus::UnitRevisionMismatch, std::nullopt};
        }
        if(unit->sources.size() != 1 || !unit->sources.front().resource_revision) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }

        const auto& stored_root = *unit->sources.front().resource_revision;
        if(stored_root.resource_id != input.root_revision.resource_id
           || stored_root.generation != input.root_revision.generation
           || !same_digest(stored_root.body_digest, input.root_revision.body_digest)) {
            return {MdbxResourceFrontierSnapshotStatus::RootRevisionMismatch, std::nullopt};
        }

        const auto root = m_manifests.find_manifest_in_transaction(
            input.root_revision.resource_id, *transaction);
        if(!root || root->state != ResourceManifestState::Active) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }
        if(root->revision.generation != input.root_revision.generation) {
            return {MdbxResourceFrontierSnapshotStatus::RootRevisionMismatch, std::nullopt};
        }
        if(!root->revision.body_digest
           || !same_digest(*root->revision.body_digest, input.root_revision.body_digest)) {
            return {MdbxResourceFrontierSnapshotStatus::RootDigestMismatch, std::nullopt};
        }

        snapshot.entries.push_back(MdbxResourceFrontierEntry{*unit, *root});
    }

    snapshot.read_transaction_owner = std::static_pointer_cast<void>(std::move(transaction));
    return {MdbxResourceFrontierSnapshotStatus::Available, std::move(snapshot)};
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
