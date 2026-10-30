#include "MdbxResourceFrontierSnapshot.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/common.hpp>

#include <algorithm>
#include <cstdlib>
#include <limits>
#include <stdexcept>
#include <thread>
#include <unordered_set>
#include <utility>

namespace agent_memory {

MdbxReadTransactionOwner::MdbxReadTransactionOwner(
    std::unique_ptr<mdbxc::Transaction> transaction
) : m_transaction(std::move(transaction)), m_owner_thread(std::this_thread::get_id()) {}

MdbxReadTransactionOwner::~MdbxReadTransactionOwner() {
    release();
}

MdbxReadTransactionOwner::MdbxReadTransactionOwner(
    MdbxReadTransactionOwner&& other
) noexcept : m_transaction(std::move(other.m_transaction)),
    m_owner_thread(other.m_owner_thread) {
    if(m_transaction && m_owner_thread != std::this_thread::get_id()) {
        std::terminate();
    }
}

MdbxReadTransactionOwner& MdbxReadTransactionOwner::operator=(
    MdbxReadTransactionOwner&& other
) noexcept {
    if(this == &other) return *this;
    release();
    m_transaction = std::move(other.m_transaction);
    m_owner_thread = other.m_owner_thread;
    if(m_transaction && m_owner_thread != std::this_thread::get_id()) {
        std::terminate();
    }
    return *this;
}

bool MdbxReadTransactionOwner::active() const noexcept {
    return static_cast<bool>(m_transaction);
}

void MdbxReadTransactionOwner::release() noexcept {
    if(!m_transaction) return;
    if(m_owner_thread != std::this_thread::get_id()) {
        std::terminate();
    }
    m_transaction.reset();
}

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

std::size_t estimate_entry_bytes(
    const KnowledgeUnitEnvelope& unit,
    const ResourceManifest& root
) {
    std::size_t total = unit.primary_text.size() + unit.display_text.size();
    for(const auto& source : unit.sources) {
        total += source.resource_id.value().size() + source.uri.size()
            + source.preview.size() + sizeof(SourceRefSummary);
    }
    total += root.revision.resource_id.value().size() + root.schema.schema_id.size();
    for(const auto& record : root.records) {
        total += record.chunk_id.value().size() + record.key.size()
            + sizeof(DerivedRecordRef);
    }
    for(const auto& record : root.pending_reclaim_records) {
        total += record.chunk_id.value().size() + record.key.size()
            + sizeof(DerivedRecordRef);
    }
    return total + sizeof(MdbxResourceFrontierEntry);
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
    if(scope_id.empty() || inputs.empty()
       || inputs.size() > kMdbxResourceFrontierMaxInputs) {
        return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
    }

    std::unordered_set<std::uint64_t> ids;
    ids.reserve(inputs.size());
    for(const auto& input : inputs) {
        if(input.unit_id.empty() || input.unit_revision == 0
           || input.root_revision.resource_id.empty()
           || input.root_revision.generation == 0) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }
        if(!ids.insert(input.unit_id.value()).second) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }
    }

    auto transaction = std::make_unique<mdbxc::Transaction>(
        m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY));
    MdbxResourceFrontierSnapshot snapshot;
    snapshot.scope_id = scope_id;
    snapshot.entries.reserve(inputs.size());
    std::size_t estimated_bytes = sizeof(MdbxResourceFrontierSnapshot);

    for(std::size_t index = 0; index < inputs.size(); ++index) {
        const auto& input = inputs[index];
        std::size_t unit_payload_bytes = 0;
        const auto unit = m_units.find_in_transaction(
            input.unit_id, *transaction, kMdbxResourceFrontierMaxDecodedBytes,
            &unit_payload_bytes);
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

        std::size_t root_payload_bytes = 0;
        const auto root = m_manifests.find_manifest_in_transaction(
            input.root_revision.resource_id, *transaction,
            kMdbxResourceFrontierMaxDecodedBytes, &root_payload_bytes);
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

        const auto entry_bytes = estimate_entry_bytes(*unit, *root);
        const auto serialized_bytes = unit_payload_bytes + root_payload_bytes;
        if(entry_bytes > kMdbxResourceFrontierMaxDecodedBytes
           || serialized_bytes > kMdbxResourceFrontierMaxDecodedBytes
           || estimated_bytes > kMdbxResourceFrontierMaxDecodedBytes - entry_bytes) {
            return {MdbxResourceFrontierSnapshotStatus::Unavailable, std::nullopt};
        }
        estimated_bytes += entry_bytes;
        snapshot.entries.push_back(MdbxResourceFrontierEntry{*unit, *root});
        if(m_read_hook_for_testing) {
            m_read_hook_for_testing(index);
        }
    }

    snapshot.read_transaction_owner.emplace(std::move(transaction));
    return {MdbxResourceFrontierSnapshotStatus::Available, std::move(snapshot)};
}

void MdbxResourceFrontierSnapshotReader::set_read_hook_for_testing(
    std::function<void(std::size_t)> hook
) {
    m_read_hook_for_testing = std::move(hook);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
