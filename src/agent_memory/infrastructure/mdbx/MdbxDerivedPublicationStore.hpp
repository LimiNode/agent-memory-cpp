#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_DERIVED_PUBLICATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_DERIVED_PUBLICATION_STORE_HPP_INCLUDED

/// \file MdbxDerivedPublicationStore.hpp
/// \brief MDBX-backed atomic derived publication vertical slice.

#include <agent_memory/storage/IDerivedPublicationStore.hpp>

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"

namespace agent_memory {

    struct MdbxResourceFrontierSnapshot;

    /// \brief Caller-owned metadata for one fenced derived publication.
    ///
    /// The exact unit/root bindings are taken from the supplied B1 frontier
    /// snapshot. This request carries only publication identity and body
    /// metadata; callers cannot replace the snapshot's input revisions.
    struct MdbxFencedDerivedPublicationRequest final {
        std::string publication_id;
        ProjectionKind kind = ProjectionKind::QACombined;
        std::uint64_t output_revision = 0;
        std::string body;
        std::uint64_t generation = 0;
        std::string dependency_frontier;
    };

    enum class MdbxFencedDerivedPublicationStatus : std::uint8_t {
        Published,
        FenceRejected,
        InvalidInput,
        Unavailable
    };

    struct MdbxFencedDerivedPublicationResult final {
        MdbxFencedDerivedPublicationStatus status =
            MdbxFencedDerivedPublicationStatus::Unavailable;
        std::optional<DerivedPublication> publication;
    };

    /// rief Test-only write stages for transaction rollback coverage.
    enum class MdbxDerivedPublicationFaultPoint : std::uint8_t {
        AfterBodyWrite,
        AfterManifestWrite
    };

    struct MdbxDerivedPublicationStoreOptions final {
        std::string path;
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
        std::int64_t max_dbs = kDefaultMdbxMaxDbs;
        std::shared_ptr<MdbxStorageContext> context;
        /// Test-only fenced-write fault seam; production callers leave empty.
        std::function<void(MdbxDerivedPublicationFaultPoint)> fault_injector;
    };

    class MdbxDerivedPublicationStore final : public IDerivedPublicationStore {
    public:
        explicit MdbxDerivedPublicationStore(MdbxDerivedPublicationStoreOptions options);
        ~MdbxDerivedPublicationStore() override;

        MdbxDerivedPublicationStore(const MdbxDerivedPublicationStore&) = delete;
        MdbxDerivedPublicationStore& operator=(const MdbxDerivedPublicationStore&) = delete;

        void publish(DerivedPublicationRequest request) override;

        /// rief Revalidates one B1 frontier and publishes atomically.
        ///
        /// Root manifests, KnowledgeUnits and the three derived rows are
        /// checked or written in one MDBX writable transaction. A mismatch
        /// rolls back the complete operation and returns FenceRejected.
        /// The B1 snapshot's read transaction must be released before this
        /// call when both operations run on the same MDBX thread; the method
        /// then revalidates the materialized bindings in its write transaction.
        /// A snapshot not created by the matching B1 reader/context is rejected.
        [[nodiscard]] MdbxFencedDerivedPublicationResult publish_fenced(
            const MdbxResourceFrontierSnapshot& frontier,
            MdbxFencedDerivedPublicationRequest request
        );

        [[nodiscard]] DerivedPublicationReadResult read(
            const ScopeId& access_scope,
            const std::string& publication_id,
            const std::vector<DerivedPublicationInput>& current_inputs,
            const std::string& current_dependency_frontier
        ) const override;

    private:
        class Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory
#endif

#endif
