#include "MdbxStorageContext.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <mdbx_containers/common.hpp>
#include <mdbx_containers/common/Config.hpp>
#include <stdexcept>

namespace agent_memory {

std::shared_ptr<MdbxStorageContext> MdbxStorageContext::open(
    std::string path, bool relative_to_exe) {
    if(path.empty()) throw std::invalid_argument("MDBX path must not be empty");
    mdbxc::Config config;
    config.pathname = std::move(path);
    config.max_dbs = 32;
    config.no_subdir = true;
    config.relative_to_exe = relative_to_exe;
    return std::shared_ptr<MdbxStorageContext>(new MdbxStorageContext(
        mdbxc::Connection::create(config)));
}

std::shared_ptr<MdbxStorageContext> MdbxStorageContext::attach(
    std::shared_ptr<mdbxc::Connection> connection) {
    if(!connection) throw std::invalid_argument("MDBX connection must not be null");
    return std::shared_ptr<MdbxStorageContext>(new MdbxStorageContext(std::move(connection)));
}

MdbxStorageContext::MdbxStorageContext(std::shared_ptr<mdbxc::Connection> connection)
    : m_connection(std::move(connection)) {}

const std::shared_ptr<mdbxc::Connection>& MdbxStorageContext::connection() const noexcept {
    return m_connection;
}

} // namespace agent_memory
#endif
