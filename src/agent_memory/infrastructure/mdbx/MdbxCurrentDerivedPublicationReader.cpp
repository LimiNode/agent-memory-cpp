#include "MdbxCurrentDerivedPublicationReader.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxDerivedPublicationStore.hpp"
#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxResourceManifestStorage.hpp"
#include "MdbxResourceFrontierSnapshot.hpp"

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace agent_memory {
namespace {

std::string digest_token(const ResourceBodyDigest& digest) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string value{"sha256:"};
    value.reserve(71);
    for(const auto byte : digest.bytes) {
        value.push_back(kHex[(byte >> 4U) & 0x0fU]);
        value.push_back(kHex[byte & 0x0fU]);
    }
    return value;
}

bool same_root_revision(const ResourceRevisionRef& source, const ResourceManifest& root) {
    return root.revision.resource_id == source.resource_id &&
        root.revision.generation == source.generation &&
        root.revision.body_digest.has_value() &&
        root.revision.body_digest->algorithm == source.body_digest.algorithm &&
        root.revision.body_digest->bytes == source.body_digest.bytes;
}

} // namespace

MdbxCurrentDerivedPublicationReader::MdbxCurrentDerivedPublicationReader(
    std::shared_ptr<MdbxStorageContext> context,
    std::string table_prefix
)
    : m_context(std::move(context)), m_table_prefix(std::move(table_prefix)) {
    if(!m_context) throw std::invalid_argument("current derived reader requires a storage context");
    if(m_table_prefix.empty()) m_table_prefix = "agent_memory";
}

void MdbxCurrentDerivedPublicationReader::set_read_hook_for_testing(
    std::function<void()> hook
) {
    m_read_hook_for_testing = std::move(hook);
}

MdbxCurrentDerivedPublicationReadResult MdbxCurrentDerivedPublicationReader::read_historical(
    const ScopeId& access_scope,
    const std::string& publication_id
) const {
    try {
        MdbxDerivedPublicationStoreOptions publication_options;
        publication_options.context = m_context;
        publication_options.table_prefix = m_table_prefix;
        MdbxDerivedPublicationStore publications(std::move(publication_options));
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto stored = publications.read_in_transaction(access_scope, publication_id, txn);
        txn.commit();
        if(stored.status == DerivedPublicationReadStatus::NotFound)
            return {MdbxCurrentDerivedPublicationStatus::NotFound, std::nullopt};
        if(stored.status != DerivedPublicationReadStatus::StoredMatch || !stored.publication)
            return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
        return {MdbxCurrentDerivedPublicationStatus::Historical, stored.publication};
    } catch(const std::exception&) {
        return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
    }
}

MdbxCurrentDerivedPublicationReadResult MdbxCurrentDerivedPublicationReader::read_current(
    const ScopeId& access_scope,
    const std::string& publication_id
) const {
    if(access_scope.empty() || publication_id.empty())
        return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
    try {
        MdbxDerivedPublicationStoreOptions publication_options;
        publication_options.context = m_context;
        publication_options.table_prefix = m_table_prefix;
        MdbxDerivedPublicationStore publications(std::move(publication_options));
        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = m_context;
        unit_options.table_prefix = m_table_prefix;
        MdbxKnowledgeUnitStore units(std::move(unit_options));
        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = m_context;
        manifest_options.table_prefix = m_table_prefix;
        MdbxResourceManifestStorage manifests(std::move(manifest_options));

        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto stored = publications.read_in_transaction(access_scope, publication_id, txn);
        if(stored.status == DerivedPublicationReadStatus::NotFound) {
            txn.commit();
            return {MdbxCurrentDerivedPublicationStatus::NotFound, std::nullopt};
        }
        if(stored.status != DerivedPublicationReadStatus::StoredMatch || !stored.publication) {
            txn.commit();
            return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
        }
        if(m_read_hook_for_testing) m_read_hook_for_testing();
        const auto& request = stored.publication->request;
        std::vector<MdbxResourceFrontierEntry> frontier_entries;
        frontier_entries.reserve(request.inputs.size());
        for(const auto& input : request.inputs) {
            const auto unit = units.find_in_transaction(input.unit_id, txn);
            if(!unit) {
                txn.commit();
                return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
            }
            if(unit->scope_id != access_scope || unit->lifecycle_state != KnowledgeUnitLifecycleState::Active ||
               unit->revision != input.unit_revision)
            {
                txn.commit();
                return {MdbxCurrentDerivedPublicationStatus::Stale, std::nullopt};
            }
            if(unit->sources.size() != 1 || unit->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
               !unit->sources.front().resource_revision ||
               unit->sources.front().resource_revision->body_digest.algorithm !=
                   ResourceBodyDigestAlgorithm::Sha256)
            {
                txn.commit();
                return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
            }
            const auto& source = *unit->sources.front().resource_revision;
            if(source.generation != input.source_generation || digest_token(source.body_digest) != input.source_digest)
            {
                txn.commit();
                return {MdbxCurrentDerivedPublicationStatus::Stale, std::nullopt};
            }
            const auto root = manifests.find_manifest_in_transaction(source.resource_id, txn);
            if(!root) {
                txn.commit();
                return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
            }
            if(root->state != ResourceManifestState::Active || !same_root_revision(source, *root)) {
                txn.commit();
                return {MdbxCurrentDerivedPublicationStatus::Stale, std::nullopt};
            }
            frontier_entries.push_back({*unit, *root});
        }
        MdbxResourceFrontierSnapshot frontier;
        frontier.scope_id = access_scope;
        frontier.entries = std::move(frontier_entries);
        const auto expected_frontier_prefix = frontier.identity_token() + '|';
        if(request.dependency_frontier.size() < expected_frontier_prefix.size() ||
           request.dependency_frontier.compare(
               0, expected_frontier_prefix.size(), expected_frontier_prefix) != 0) {
            txn.commit();
            return {MdbxCurrentDerivedPublicationStatus::Stale, std::nullopt};
        }
        txn.commit();
        return {MdbxCurrentDerivedPublicationStatus::Current, stored.publication};
    } catch(const std::exception&) {
        return {MdbxCurrentDerivedPublicationStatus::Unavailable, std::nullopt};
    }
}

} // namespace agent_memory

#endif
