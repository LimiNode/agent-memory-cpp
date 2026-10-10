#include "MdbxFederatedCanonicalReader.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <algorithm>
#include <stdexcept>

namespace agent_memory {

MdbxFederatedCanonicalReader::MdbxFederatedCanonicalReader(
    const MdbxWorkspaceStorageRegistry& registry) noexcept
    : m_registry(&registry) {}

MdbxFederatedCanonicalReadResult MdbxFederatedCanonicalReader::read(
    const MdbxFederatedCanonicalReadRequest& request) const {
    if (request.routes.empty())
        throw std::invalid_argument("federated canonical read requires at least one route");

    auto routes = request.routes;
    std::sort(routes.begin(), routes.end(), [](const auto& left, const auto& right) {
        if (left.workspace_id != right.workspace_id)
            return left.workspace_id < right.workspace_id;
        return left.placement_generation < right.placement_generation;
    });
    for (std::size_t index = 1; index < routes.size(); ++index) {
        if (routes[index - 1].workspace_id == routes[index].workspace_id)
            throw MdbxWorkspaceRoutingError(
                "federated canonical read selects one workspace more than once: " +
                routes[index].workspace_id);
    }

    MdbxFederatedCanonicalReadResult result;
    result.routes.reserve(routes.size());
    result.documents.reserve(routes.size());
    std::size_t unavailable_count = 0;
    for (const auto& route : routes) {
        // Resolve outside the backend-failure boundary: unknown and stale
        // placement are caller errors and must fail closed, not become empty
        // or unavailable results.
        const auto placement = m_registry->resolve(route.workspace_id, route.placement_generation);
        MdbxFederatedRouteResult route_result;
        route_result.provenance = {placement->workspace_id(), placement->generation()};
        try {
            route_result.revision = request.revision
                                          ? placement->canonical_content()->read_revision(
                                                request.document_id, *request.revision)
                                          : placement->canonical_content()->read_current(
                                                request.document_id);
            route_result.status = route_result.revision
                                      ? MdbxFederatedRouteStatus::Found
                                      : MdbxFederatedRouteStatus::NotFound;
            if (route_result.revision)
                result.documents.push_back(*route_result.revision);
        } catch (const std::exception& error) {
            route_result.status = MdbxFederatedRouteStatus::Unavailable;
            route_result.diagnostic = error.what();
            ++unavailable_count;
        }
        result.routes.push_back(std::move(route_result));
    }

    if (unavailable_count == 0)
        result.status = MdbxFederatedReadStatus::Complete;
    else if (unavailable_count == result.routes.size())
        result.status = MdbxFederatedReadStatus::Unavailable;
    else
        result.status = MdbxFederatedReadStatus::Partial;
    return result;
}

} // namespace agent_memory
#endif
