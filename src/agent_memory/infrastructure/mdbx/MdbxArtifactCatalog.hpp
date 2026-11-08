#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_ARTIFACT_CATALOG_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_ARTIFACT_CATALOG_HPP_INCLUDED

/// \file MdbxArtifactCatalog.hpp
/// \brief Raw-only artifact provenance catalog over MDBX and a file CAS.

#include <agent_memory/domain/Artifact.hpp>
#include <agent_memory/storage/FileRawBlobStore.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"

namespace mdbxc {
class Transaction;
}

#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

enum class ArtifactCatalogStatus : std::uint8_t {
    Ok,
    InvalidArgument,
    VerificationBudgetExceeded,
    NotFound,
    Conflict,
    Corrupt,
    IoError
};

enum class ArtifactCatalogFaultPoint : std::uint8_t {
    BeforeIntent,
    AfterIntent,
    BeforeCasPublication,
    AfterCasPublication,
    BeforeCatalogCommit,
    AfterCatalogCommit,
    BeforeReceiptFinalization
};

using ArtifactCatalogFaultInjector =
    std::function<void(ArtifactCatalogFaultPoint)>;

struct ArtifactCatalogIngestRequest final {
    SourceRevisionId source_revision_id;
    ArtifactBindingId binding_id;
    std::string role;
    ArtifactRetentionClass retention = ArtifactRetentionClass::SourceOriginal;
    std::optional<std::string> media_type;
    std::optional<std::string> original_name;
    std::uint64_t bound_at_ms = 0;
    std::string body;
};

struct ArtifactCatalogRecord final {
    Artifact artifact;
    ArtifactBinding binding;
    BindingBodyState body_state = BindingBodyState::NeverRetained;
    bool receipt_finalized = false;
};

struct ArtifactCatalogResult final {
    ArtifactCatalogStatus status = ArtifactCatalogStatus::IoError;
    std::optional<ArtifactCatalogRecord> record;
    std::string diagnostic;
};

struct ArtifactCatalogReadResult final {
    ArtifactCatalogStatus status = ArtifactCatalogStatus::IoError;
    std::string bytes;
    std::string diagnostic;
};

struct ArtifactCatalogCollectionResult final {
    ArtifactCatalogStatus status = ArtifactCatalogStatus::IoError;
    std::vector<ArtifactCatalogRecord> records;
    std::string diagnostic;
};

struct MdbxArtifactCatalogOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
    std::filesystem::path raw_cas_root;
    FileRawBlobStoreOptions raw_cas_options{};
    /// Maximum size accepted by read_body(). Use read_body_range() for larger bodies.
    std::uint64_t max_full_body_bytes = 64U * 1024U * 1024U;
    ArtifactCatalogFaultInjector fault_injector;
};

/// \brief Durable source-revision/artifact binding catalog for the Raw profile.
///
/// File publication and MDBX publication are deliberately separate durability
/// domains. The catalog never claims that they form one atomic transaction.
/// A versioned ingest intent bridges the two operations; reopening the catalog
/// reconciles intents whose CAS body was published before the MDBX commit.
/// Compression, decoded codecs, derived artifacts, and production retrieval are
/// outside this adapter.
class MdbxArtifactCatalog final {
public:
    explicit MdbxArtifactCatalog(MdbxArtifactCatalogOptions options);
    ~MdbxArtifactCatalog();
    MdbxArtifactCatalog(const MdbxArtifactCatalog&) = delete;
    MdbxArtifactCatalog& operator=(const MdbxArtifactCatalog&) = delete;

    [[nodiscard]] ArtifactCatalogResult ingest(
        const ArtifactCatalogIngestRequest& request);
    [[nodiscard]] ArtifactCatalogResult read_binding(
        const ArtifactBindingId& binding_id) const;

    /// Reads binding metadata without probing or scanning the Raw CAS body.
    /// Callers must perform a bounded digest-verified read separately.
    [[nodiscard]] ArtifactCatalogResult read_binding_metadata(
        const ArtifactBindingId& binding_id) const;

    /// Reads catalog metadata from a caller-owned read transaction. CAS bytes
    /// are intentionally not probed here; the caller must perform the bounded
    /// digest-verified read separately and then revalidate this metadata.
    [[nodiscard]] ArtifactCatalogResult read_binding_in_transaction(
        const ArtifactBindingId& binding_id,
        const mdbxc::Transaction& transaction
    ) const;
    [[nodiscard]] ArtifactCatalogResult read_source_revision(
        const SourceRevisionId& source_revision_id) const;
    [[nodiscard]] ArtifactCatalogCollectionResult read_source_revision_bindings(
        const SourceRevisionId& source_revision_id) const;
    [[nodiscard]] ArtifactCatalogReadResult read_body(
        const ArtifactBindingId& binding_id) const;
    [[nodiscard]] ArtifactCatalogReadResult read_body_range(
        const ArtifactBindingId& binding_id,
        std::uint64_t offset,
        std::uint64_t length
    ) const;
    /// Reads a range only after a complete CAS digest scan bounded by the
    /// supplied physical-byte budget. Metadata is read without a preliminary
    /// unbounded CAS probe.
    [[nodiscard]] ArtifactCatalogReadResult read_body_range(
        const ArtifactBindingId& binding_id,
        std::uint64_t offset,
        std::uint64_t length,
        std::uint64_t verification_budget_bytes
    ) const;

    /// Reconciles durable intents after a process or publication failure.
    /// Missing CAS bodies remain pending and are safe to retry with ingest().
    void recover();

    /// Returns materialized artifacts which have no catalog binding. This is a
    /// report-only boundary; this class never deletes CAS files.
    [[nodiscard]] std::vector<ArtifactId> gc_candidates() const;

    [[nodiscard]] const FileRawBlobStore& raw_cas() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif

#endif
