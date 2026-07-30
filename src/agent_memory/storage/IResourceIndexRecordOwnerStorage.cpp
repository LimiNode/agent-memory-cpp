#include "IResourceIndexRecordOwnerStorage.hpp"

namespace agent_memory {

    bool is_valid_resource_index_record_owner(
        const ResourceIndexRecordOwner& owner
    ) noexcept {
        return !owner.resource_id.empty() &&
            !owner.manifest_schema.schema_id.empty() &&
            owner.manifest_schema.schema_version != 0;
    }

    IResourceIndexRecordOwnerStorage::~IResourceIndexRecordOwnerStorage() = default;

} // namespace agent_memory
