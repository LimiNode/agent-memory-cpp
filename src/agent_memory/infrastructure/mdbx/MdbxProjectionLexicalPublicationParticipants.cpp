#include "MdbxProjectionLexicalPublicationParticipants.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <stdexcept>
#include <utility>

namespace agent_memory {

MdbxProjectionPublicationParticipant::MdbxProjectionPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxProjectionStore& store,
    SearchProjection projection
) : m_context(std::move(context)),
    m_store(&store),
    m_projection(std::move(projection)) {}

const MdbxStorageContext& MdbxProjectionPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxProjectionPublicationParticipant::apply(
    const mdbxc::Transaction& transaction
) {
    if(!m_projection) {
        throw std::logic_error("projection publication participant has no operation");
    }
    m_store->put_in_transaction(std::move(*m_projection), transaction);
    m_projection.reset();
}

MdbxLexicalPublicationParticipant::MdbxLexicalPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxPersistentLexicalIndex& index,
    FlatLexicalUnitInput input,
    std::optional<FlatLexicalExpectedState> expected_current
) : m_context(std::move(context)),
    m_index(&index),
    m_input(std::move(input)),
    m_expected_current(std::move(expected_current)) {}

const MdbxStorageContext& MdbxLexicalPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxLexicalPublicationParticipant::apply(
    const mdbxc::Transaction& transaction
) {
    if(!m_input) {
        throw std::logic_error("lexical publication participant has no operation");
    }
    m_index->upsert_in_transaction(
        std::move(*m_input), std::move(m_expected_current), transaction
    );
    m_input.reset();
    m_expected_current.reset();
}

} // namespace agent_memory

#endif
