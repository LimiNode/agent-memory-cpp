#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PUBLICATION_WRITE_SCOPE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PUBLICATION_WRITE_SCOPE_HPP_INCLUDED

/// \file MdbxPublicationWriteScope.hpp
/// \brief Backend-specific ownership scope for one coordinated MDBX write.

#include "MdbxStorageContext.hpp"

#include <memory>
#include <thread>

#if AGENT_MEMORY_HAS_MDBX
namespace mdbxc {
class Transaction;
}

namespace agent_memory {

/// \brief Internal participant boundary for a coordinator-owned transaction.
///
/// Participants receive a const transaction reference, which is sufficient for
/// mdbx-containers table mutations while preventing them from committing or
/// rolling back the coordinator's transaction through this interface.
class MdbxPublicationParticipant {
public:
    virtual ~MdbxPublicationParticipant() = default;

    [[nodiscard]] virtual const MdbxStorageContext& context() const noexcept = 0;

    virtual void apply(const mdbxc::Transaction& transaction) = 0;
};

/// \brief Owns one thread-bound writable transaction for a publication.
///
/// The scope is the only component allowed to commit or abort. It is
/// deliberately backend-specific and remains separate from public storage
/// interfaces. Prepare data before opening the scope; after it starts, only
/// bounded participant work and the final commit are permitted.
class MdbxPublicationWriteScope final {
public:
    static MdbxPublicationWriteScope begin(
        std::shared_ptr<MdbxStorageContext> context
    );

    MdbxPublicationWriteScope(const MdbxPublicationWriteScope&) = delete;
    MdbxPublicationWriteScope& operator=(const MdbxPublicationWriteScope&) = delete;
    MdbxPublicationWriteScope(MdbxPublicationWriteScope&&) = delete;
    MdbxPublicationWriteScope& operator=(MdbxPublicationWriteScope&&) = delete;

    ~MdbxPublicationWriteScope() noexcept;

    void apply(MdbxPublicationParticipant& participant);
    void commit();
    void abort();

    [[nodiscard]] bool active() const noexcept;
    [[nodiscard]] bool on_owner_thread() const noexcept;
    [[nodiscard]] const std::shared_ptr<MdbxStorageContext>& context() const noexcept;

private:
    explicit MdbxPublicationWriteScope(std::shared_ptr<MdbxStorageContext> context);

    void require_owner_thread() const;
    void require_active() const;

    std::shared_ptr<MdbxStorageContext> m_context;
    std::unique_ptr<mdbxc::Transaction> m_transaction;
    std::thread::id m_owner_thread;
    bool m_active = true;
};

} // namespace agent_memory
#endif

#endif
