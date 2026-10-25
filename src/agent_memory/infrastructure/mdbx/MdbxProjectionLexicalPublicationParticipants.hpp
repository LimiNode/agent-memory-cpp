#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PROJECTION_LEXICAL_PUBLICATION_PARTICIPANTS_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PROJECTION_LEXICAL_PUBLICATION_PARTICIPANTS_HPP_INCLUDED

/// \file MdbxProjectionLexicalPublicationParticipants.hpp
/// \brief Original projection and bounded flat-v2 lexical participant seams.

#include "MdbxPersistentLexicalIndex.hpp"
#include "MdbxProjectionStore.hpp"
#include "MdbxPublicationWriteScope.hpp"

#include <memory>
#include <optional>

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory {

/// \brief Publishes one revision-bound Original/search projection row.
class MdbxProjectionPublicationParticipant final : public MdbxPublicationParticipant {
public:
    MdbxProjectionPublicationParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        MdbxProjectionStore& store,
        SearchProjection projection
    );

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void apply(const mdbxc::Transaction& transaction) override;

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxProjectionStore* m_store;
    std::optional<SearchProjection> m_projection;
};

/// \brief Publishes one bounded flat-v2 lexical contribution and statistics state.
class MdbxLexicalPublicationParticipant final : public MdbxPublicationParticipant {
public:
    MdbxLexicalPublicationParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        MdbxPersistentLexicalIndex& index,
        FlatLexicalUnitInput input,
        std::optional<FlatLexicalExpectedState> expected_current = std::nullopt
    );

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void apply(const mdbxc::Transaction& transaction) override;

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxPersistentLexicalIndex* m_index;
    std::optional<FlatLexicalUnitInput> m_input;
    std::optional<FlatLexicalExpectedState> m_expected_current;
};

} // namespace agent_memory
#endif

#endif
