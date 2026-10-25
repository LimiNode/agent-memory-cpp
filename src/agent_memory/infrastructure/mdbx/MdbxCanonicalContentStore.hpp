#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_CONTENT_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_CONTENT_STORE_HPP_INCLUDED

/// \file MdbxCanonicalContentStore.hpp
/// \brief Raw/plain MDBX persistence for canonical document content.
///
/// The logical document, block, edit, revision, and body-binding rules are
/// defined by `guides/canonical-content-storage-roadmap.md`. Shared connection
/// ownership and transaction boundaries are defined by
/// `guides/storage-backend-integration-roadmap.md`. The four physical DBIs are
/// registered in `guides/dbi-manifest.yaml` and projected into
/// `guides/mdbx-containers-extension-tz.md`.

#include <agent_memory/storage/CanonicalContentStore.hpp>

#if AGENT_MEMORY_HAS_MDBX
namespace mdbxc { class Transaction; }
#include "MdbxStorageContext.hpp"

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace agent_memory {

namespace detail {

/// \brief Computes the C1 logical decoded-content SHA-256 bytes.
///
/// This backend-only primitive intentionally excludes body revision, codec,
/// physical encoding generation, framing, and physical checksums. It is used
/// by the MDBX adapter and its regression fixtures to preserve the identity
/// boundary described by `guides/canonical-content-storage-roadmap.md`.
[[nodiscard]] std::array<std::uint8_t, 32> canonical_decoded_content_digest(
    const std::vector<ContentBlock>& blocks);

} // namespace detail

/// \brief Construction options for the C1 raw/plain MDBX profile.
struct MdbxCanonicalContentStoreOptions final {
    std::string path; ///< MDBX path used when no shared context is supplied.
    std::string table_prefix = "agent_memory"; ///< Prefix for the four C1 DBIs.
    bool relative_to_exe = false; ///< Resolve an owned relative path from the executable.
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;   ///< Capacity passed to an owned context.
    std::shared_ptr<MdbxStorageContext> context; ///< Optional host/shared MDBX context.
    /// Optional pre-opened table used by guarded canonical publication.
    std::string publication_guard_table_name;
    /// Optional compact fencing-token table used by guarded publication.
    std::string publication_token_guard_table_name;
};

/// \brief Durable compare token checked in the canonical write transaction.
struct MdbxCanonicalPublicationGuard final {
    std::string table_name;
    std::string key;
    std::string expected_value;
    std::string token_table_name;
    std::string expected_token;
};

/// \brief Durable raw/plain canonical document-content store.
///
/// The adapter persists the current head, immutable revision records, logical
/// body-revision bindings, raw physical body generations, and the historical
/// block-ID no-reuse ledger. One accepted creation or edit is published in one
/// writable MDBX transaction. A current read resolves its head, revision, and
/// body inside one read-only snapshot.
///
/// Revision records bind a `BodyRevision` to an algorithm-tagged SHA-256 logical
/// decoded-content digest;
/// they do not embed a physical codec or `PhysicalEncodingGeneration`. Those
/// physical fields live in the body key/value descriptor, alongside a
/// generation-specific checksum, so a future re-encoding can be published
/// without rewriting immutable semantic revision identities.
///
/// C2 compression, dictionaries, and byte-identical physical re-encoding are
/// deliberately outside this class. Core/domain headers remain MDBX-free.
/// Domain outcomes are returned through the backend-neutral interfaces; MDBX
/// I/O, lifecycle, corruption, and binding failures propagate as exceptions
/// instead of being reported as `NotFound` or `InvalidEdit`.
///
/// \see `guides/canonical-content-storage-roadmap.md`
/// \see `guides/storage-backend-integration-roadmap.md`
/// \see `guides/mdbx-containers-extension-tz.md`
/// \see `guides/dbi-manifest.yaml`
class MdbxCanonicalContentStore final : public ICanonicalContentStore,
                                        public ICanonicalContentEditor {
  public:
    /// \brief Opens the four profile-local tables over an owned or shared context.
    explicit MdbxCanonicalContentStore(MdbxCanonicalContentStoreOptions options);
    ~MdbxCanonicalContentStore() override;
    MdbxCanonicalContentStore(const MdbxCanonicalContentStore&) = delete;
    MdbxCanonicalContentStore& operator=(const MdbxCanonicalContentStore&) = delete;

    /// \copydoc ICanonicalContentEditor::create_document
    bool create_document(CanonicalDocumentRevision initial) override;
    /// \brief Creates a document only while the durable publication guard matches.
    bool create_document_guarded(
        CanonicalDocumentRevision initial,
        MdbxCanonicalPublicationGuard guard
    );
    /// rief Applies a creation through a coordinator-owned writable transaction.
    ///
    /// The caller retains commit/rollback ownership; this method never opens,
    /// commits, or stores the transaction handle.
    bool create_document_in_transaction(
        CanonicalDocumentRevision initial,
        const mdbxc::Transaction& transaction
    );
    /// \copydoc ICanonicalContentStore::read_current
    [[nodiscard]] std::optional<CanonicalDocumentRevision>
    read_current(const DocumentId&) const override;
    [[nodiscard]] std::optional<CanonicalDocumentRevision>
    read_current_in_transaction(
        const DocumentId&,
        const mdbxc::Transaction& transaction
    ) const;
    /// \copydoc ICanonicalContentStore::read_revision
    [[nodiscard]] std::optional<CanonicalDocumentRevision>
    read_revision(const DocumentId&, std::uint64_t) const override;
    /// \copydoc ICanonicalContentStore::read_block
    [[nodiscard]] std::optional<ContentBlock>
    read_block(const DocumentId&,
               const ContentBlockId&,
               std::optional<std::uint64_t> = std::nullopt) const override;
    /// \copydoc ICanonicalContentStore::materialize_markdown
    [[nodiscard]] std::optional<std::string>
    materialize_markdown(const DocumentId&,
                         std::optional<std::uint64_t> = std::nullopt) const override;
    /// \copydoc ICanonicalContentEditor::commit
    [[nodiscard]] CanonicalEditResult commit(const CanonicalEditRequest&) override;
    /// \brief Publishes an edit only while the durable publication guard matches.
    [[nodiscard]] CanonicalEditResult commit_guarded(
        const CanonicalEditRequest&,
        MdbxCanonicalPublicationGuard guard
    );
    /// rief Applies an edit through a coordinator-owned writable transaction.
    [[nodiscard]] CanonicalEditResult commit_in_transaction(
        const CanonicalEditRequest&,
        const mdbxc::Transaction& transaction
    );

  private:
    bool create_document_impl(
        CanonicalDocumentRevision initial,
        std::optional<MdbxCanonicalPublicationGuard> guard
    );
    [[nodiscard]] CanonicalEditResult commit_impl(
        const CanonicalEditRequest&,
        std::optional<MdbxCanonicalPublicationGuard> guard
    );
    class Impl;
    std::unique_ptr<Impl> m_impl; ///< MDBX-dependent implementation state.
};

} // namespace agent_memory
#endif

#endif
