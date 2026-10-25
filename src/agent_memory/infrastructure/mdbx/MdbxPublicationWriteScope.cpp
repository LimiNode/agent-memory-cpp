#include "MdbxPublicationWriteScope.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/common.hpp>

#include <exception>
#include <stdexcept>
#include <utility>

namespace agent_memory {

MdbxPublicationWriteScope MdbxPublicationWriteScope::begin(
    std::shared_ptr<MdbxStorageContext> context
) {
    return MdbxPublicationWriteScope(std::move(context));
}

MdbxPublicationWriteScope::MdbxPublicationWriteScope(
    std::shared_ptr<MdbxStorageContext> context
) : m_context(std::move(context)),
    m_owner_thread(std::this_thread::get_id()) {
    if(!m_context || !m_context->connection()) {
        throw std::invalid_argument("publication write scope requires an MDBX context");
    }
    if(m_context->connection()->current_txn()) {
        throw std::logic_error("nested MDBX publication transactions are not allowed");
    }
    m_transaction = std::make_unique<mdbxc::Transaction>(
        m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE)
    );
}

MdbxPublicationWriteScope::~MdbxPublicationWriteScope() noexcept {
    if(!m_active || !m_transaction) {
        return;
    }
    if(!on_owner_thread()) {
        std::terminate();
    }
    try {
        m_transaction->rollback();
    } catch(...) {
        std::terminate();
    }
}

void MdbxPublicationWriteScope::apply(MdbxPublicationParticipant& participant) {
    require_owner_thread();
    require_active();
    if(participant.context().connection() != m_context->connection()) {
        throw std::invalid_argument("publication participant belongs to another MDBX context");
    }
    participant.apply(*m_transaction);
}

void MdbxPublicationWriteScope::commit() {
    require_owner_thread();
    require_active();
    m_transaction->commit();
    m_active = false;
}

void MdbxPublicationWriteScope::abort() {
    require_owner_thread();
    require_active();
    m_transaction->rollback();
    m_active = false;
}

bool MdbxPublicationWriteScope::active() const noexcept {
    return m_active;
}

bool MdbxPublicationWriteScope::on_owner_thread() const noexcept {
    return std::this_thread::get_id() == m_owner_thread;
}

const std::shared_ptr<MdbxStorageContext>&
MdbxPublicationWriteScope::context() const noexcept {
    return m_context;
}

void MdbxPublicationWriteScope::require_owner_thread() const {
    if(!on_owner_thread()) {
        throw std::logic_error("publication transaction used from a non-owner thread");
    }
}

void MdbxPublicationWriteScope::require_active() const {
    if(!m_active || !m_transaction) {
        throw std::logic_error("publication transaction is no longer active");
    }
}

} // namespace agent_memory

#endif
