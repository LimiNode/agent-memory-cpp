#include <agent_memory/domain/Artifact.hpp>

#include <cstddef>
#include <cstdint>
#include <iostream>
#include <stdexcept>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) {
        throw std::runtime_error(message);
    }
}

agent_memory::BlobDigest digest(std::uint8_t seed) {
    agent_memory::BlobDigest result;
    for(std::size_t index = 0; index < result.value.size(); ++index) {
        result.value[index] = static_cast<std::uint8_t>(seed + index);
    }
    return result;
}

} // namespace

int main() {
    using namespace agent_memory;

    const auto artifact_digest = digest(1);
    const Artifact artifact{artifact_id_for_digest(artifact_digest), artifact_digest, 42};
    expect(is_valid_artifact(artifact), "valid artifact was rejected");
    expect(artifact.id.value().rfind("sha256:", 0) == 0,
           "artifact id did not use the canonical digest form");
    expect(!is_valid_artifact(Artifact{ArtifactId("artifact-a"), artifact_digest, 42}),
           "non-derived artifact id was accepted");
    expect(!is_valid_artifact(Artifact{artifact.id, BlobDigest{}, 42}),
           "zero digest was accepted");

    const ArtifactBinding binding{
        ArtifactBindingId("binding-a"), SourceRevisionId("source@1"),
        artifact.id, "primary_source_original", ArtifactRetentionClass::SourceOriginal,
        std::string("text/markdown"), std::nullopt, 10};
    expect(is_valid_artifact_binding(binding), "valid binding was rejected");
    expect(is_valid_initial_body_state(
               ArtifactRetentionClass::ReferenceOnly,
               BindingBodyState::NeverRetained),
           "reference-only binding did not require NeverRetained");
    expect(!is_valid_initial_body_state(
               ArtifactRetentionClass::ReferenceOnly,
               BindingBodyState::Materialized),
           "reference-only binding accepted materialized state");
    expect(is_valid_initial_body_state(
               ArtifactRetentionClass::SourceOriginal,
               BindingBodyState::Materialized),
           "source-original binding rejected materialized state");
    expect(is_valid_body_state_transition(
               ArtifactRetentionClass::SourceOriginal,
               BindingBodyState::Materialized,
               BindingBodyState::RetentionRemoved),
           "source-original removal transition was rejected");
    expect(!is_valid_body_state_transition(
               ArtifactRetentionClass::SourceOriginal,
               BindingBodyState::RetentionRemoved,
               BindingBodyState::Materialized),
           "terminal removal transition was reopened");
    expect(is_valid_body_state_transition(
               ArtifactRetentionClass::RebuildableCache,
               BindingBodyState::Materialized,
               BindingBodyState::Evicted),
           "rebuildable cache was not evictable");
    expect(is_valid_body_state_transition(
               ArtifactRetentionClass::RebuildableCache,
               BindingBodyState::Evicted,
               BindingBodyState::Materialized),
           "rebuildable cache was not re-materializable");
    expect(is_valid_body_state_transition(
               ArtifactRetentionClass::RebuildableCache,
               BindingBodyState::Evicted,
               BindingBodyState::RetentionRemoved),
           "rebuildable cache removal transition was rejected");
    expect(!is_valid_body_state_transition(
               ArtifactRetentionClass::ReferenceOnly,
               BindingBodyState::NeverRetained,
               BindingBodyState::Materialized),
           "reference-only state acquired an impossible body");
    expect(!is_valid_body_state_transition(
               static_cast<ArtifactRetentionClass>(255),
               BindingBodyState::Materialized,
               BindingBodyState::Materialized),
           "unknown retention enum was accepted");

    std::cout << "artifact identity contract: PASS\n";
    return 0;
}
