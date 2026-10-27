#include "MdbxProjectionLexicalPublicationParticipants.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <stdexcept>
#include <algorithm>
#include <string>
#include <utility>

namespace agent_memory {

namespace {

bool projection_matches_target(
    const SearchProjection& projection,
    const PublicationPlan& plan
) {
    const auto& expected = plan.target_evidence.projection_version;
    const auto& actual = projection.version;
    return projection.scope_id.value() == plan.identity.scope_id.value()
        && projection.unit_id.value() == plan.target_evidence.unit_id.value()
        && actual.unit_revision_at_build == plan.target_evidence.unit_revision
        && actual.unit_revision_at_build == expected.unit_revision_at_build
        && actual.projection_revision == expected.projection_revision
        && actual.derivation_generation == expected.derivation_generation
        && actual.derivation_fingerprint == expected.derivation_fingerprint
        && compute_projection_payload_digest(projection)
            == plan.target_evidence.projection_payload_digest;
}

} // namespace

MdbxProjectionPublicationParticipant::MdbxProjectionPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxProjectionStore& store,
    SearchProjection projection
) : m_context(std::move(context)),
    m_store(&store),
    m_projection(std::move(projection)) {
    if(!m_context || !m_context->connection()
        || !store.context().connection()
        || m_context->connection() != store.context().connection()) {
        throw std::invalid_argument(
            "publication projection participant context does not own the underlying store connection"
        );
    }
}

const MdbxStorageContext& MdbxProjectionPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxProjectionPublicationParticipant::apply(
    const mdbxc::Transaction& transaction
) {
    if(!m_projection || m_applied) {
        throw std::logic_error("projection publication participant has no operation");
    }
    m_store->put_in_transaction(*m_projection, transaction);
    m_applied = true;
}

void MdbxProjectionPublicationParticipant::reset_after_abort() noexcept {
    m_applied = false;
}

bool MdbxProjectionPublicationParticipant::validates(
    const PublicationPlan& plan,
    const mdbxc::Transaction& transaction
) const {
    if(!m_applied || !m_projection
        || !projection_matches_target(*m_projection, plan)) {
        return false;
    }
    const auto rows = m_store->list_in_transaction(
        plan.identity.scope_id,
        m_projection->unit_id,
        transaction,
        m_projection->kind
    );
    return std::any_of(rows.begin(), rows.end(), [this, &plan](const auto& row) {
        return row.kind == m_projection->kind
            && projection_matches_target(row, plan)
            && compute_projection_payload_digest(row)
                == compute_projection_payload_digest(*m_projection);
    });
}

MdbxLexicalPublicationParticipant::MdbxLexicalPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxPersistentLexicalIndex& index,
    FlatLexicalUnitInput input,
    std::optional<FlatLexicalExpectedState> expected_current
) : m_context(std::move(context)),
    m_index(&index),
    m_input(std::move(input)),
    m_expected_current(std::move(expected_current)) {
    if(!m_context || !m_context->connection()
        || !index.context().connection()
        || m_context->connection() != index.context().connection()) {
        throw std::invalid_argument(
            "publication lexical participant context does not own the underlying index connection"
        );
    }
}

const MdbxStorageContext& MdbxLexicalPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxLexicalPublicationParticipant::apply(
    const mdbxc::Transaction& transaction
) {
    if(!m_input || m_applied) {
        throw std::logic_error("lexical publication participant has no operation");
    }
    m_index->upsert_in_transaction(
        *m_input, m_expected_current, transaction
    );
    m_applied = true;
}

void MdbxLexicalPublicationParticipant::reset_after_abort() noexcept {
    m_applied = false;
}

bool MdbxLexicalPublicationParticipant::validates(
    const PublicationPlan& plan,
    const mdbxc::Transaction& transaction
) const {
    if(!m_applied || !m_input) {
        return false;
    }
    const auto read = m_index->read_in_transaction(transaction);
    const auto stats = read.index.find_unit_stats(m_input->unit_id);
    if(!stats) {
        return false;
    }
    const auto& expected = plan.target_evidence.projection_version;
    const auto& actual = stats->projection_version;
    return stats
        && stats->unit_id.value() == plan.target_evidence.unit_id.value()
        && stats->resource_id == plan.target_evidence.resource_id
        && stats->resource_generation == plan.target_evidence.resource_generation
        && stats->unit_revision == plan.target_evidence.unit_revision
        && actual.unit_revision_at_build == expected.unit_revision_at_build
        && actual.projection_revision == expected.projection_revision
        && actual.derivation_generation == expected.derivation_generation
        && actual.derivation_fingerprint == expected.derivation_fingerprint
        && read.frontier.index_descriptor.ref.scope_id == plan.identity.scope_id
        && read.frontier.index_descriptor.ref.projection_kind == m_input->projection_kind;
}

} // namespace agent_memory

#endif
