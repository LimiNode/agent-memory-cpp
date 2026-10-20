#include <agent_memory/domain/SearchProjection.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#include <iostream>
#include <stdexcept>

namespace {
void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
}

int main() {
    using namespace agent_memory;
    KnowledgeUnitEnvelope envelope;
    envelope.id = KnowledgeUnitId{7};
    envelope.kind = KnowledgeUnitKind::Chunk;
    envelope.scope_id = ScopeId{"tenant-a"};
    envelope.primary_text = "original bounded text";
    envelope.revision = 4;
    const auto projection = make_original_projection(envelope, 3, 9, "recipe.test.v1");
    expect(projection.kind == ProjectionKind::Original,
           "Original builder emitted the wrong projection kind");
    expect(projection.body == envelope.primary_text,
           "Original builder did not preserve the envelope seed");
    expect(projection.version.unit_revision_at_build == 4 &&
               projection.version.projection_revision == 3 &&
               projection.version.derivation_generation == 9 &&
               projection.version.derivation_fingerprint == "recipe.test.v1",
           "Original projection freshness identity is incomplete");
    expect(is_valid_search_projection(projection),
           "Original builder emitted an invalid projection");

    bool rejected = false;
    envelope.lifecycle_state = KnowledgeUnitLifecycleState::Erased;
    try {
        (void)make_original_projection(envelope, 1);
    } catch(const std::invalid_argument&) { rejected = true; }
    expect(rejected, "Original builder accepted an erased envelope");
    std::cout << "search projection tests passed\n";
    return 0;
}
