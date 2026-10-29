#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/storage/DerivedPublicationAdmission.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <stdexcept>
#include <string>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        ("agent_memory_derived_publication_admission_" + std::to_string(nonce) + ".mdbx");
}

agent_memory::SourceRefSummary make_source(
    std::string resource,
    std::uint64_t generation,
    std::uint8_t seed
) {
    using namespace agent_memory;
    SourceRefSummary source;
    source.resource_id = ResourceId{std::move(resource)};
    ResourceBodyDigest digest;
    for(std::size_t index = 0; index < digest.bytes.size(); ++index)
        digest.bytes[index] = static_cast<std::uint8_t>(seed + index);
    source.resource_revision = ResourceRevisionRef{
        source.resource_id, generation, digest};
    source.uri = "https://example.test/source";
    source.excerpt = TextRange{0, 8};
    source.preview = "canonical evidence";
    source.quote_hash = compute_quote_hash(source.preview);
    source.confidence = 1.0;
    return source;
}

agent_memory::KnowledgeUnitCreateRequest make_request(
    std::string scope,
    std::string resource,
    std::uint8_t seed
) {
    using namespace agent_memory;
    KnowledgeUnitCreateRequest request;
    request.kind = KnowledgeUnitKind::Chunk;
    request.scope_id = ScopeId{std::move(scope)};
    request.primary_text = "canonical unit " + std::to_string(seed);
    request.display_text = request.primary_text;
    request.sources.push_back(make_source(std::move(resource), 1, seed));
    request.content_hash_recipe_version = 2;
    request.content_hash = compute_chunk_content_hash(
        request.scope_id, request.primary_text, request.sources, 2);
    return request;
}

} // namespace

int main() {
    using namespace agent_memory;
    const auto path = test_path();
    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxKnowledgeUnitStore units({"", "agent_memory", false,
                                      kDefaultMdbxMaxDbs, context});
        const auto first = units.create_or_get_unit(
            make_request("workspace-a", "resource:first", 3));
        const auto second = units.create_or_get_unit(
            make_request("workspace-a", "resource:second", 7));
        KnowledgeUnitDerivedPublicationFrontierProvider frontier_provider(units);
        MdbxDerivedPublicationStore publications({"", "agent_memory", false,
                                                  kDefaultMdbxMaxDbs, context});
        DerivedPublicationAdmission admission(publications, frontier_provider);

        const std::vector<KnowledgeUnitId> ids{first.id, second.id};
        const auto snapshot = frontier_provider.snapshot(first.scope_id, ids);
        expect(snapshot.status == DerivedPublicationFrontierStatus::Available && snapshot.snapshot,
               "authoritative frontier snapshot was unavailable");

        DerivedPublicationRequest publication;
        publication.scope_id = first.scope_id;
        publication.publication_id = "qa-authoritative-1";
        publication.kind = ProjectionKind::QACombined;
        publication.output_revision = 1;
        publication.generation = 1;
        publication.body = "authoritatively admitted answer";
        publication.inputs = snapshot.snapshot->inputs;
        publication.dependency_frontier = snapshot.snapshot->dependency_frontier;

        const auto published = admission.publish(publication);
        expect(published.status == DerivedPublicationAdmissionStatus::Published,
               "authoritative publication was not admitted");

        const auto read = admission.read(
            first.scope_id, publication.publication_id,
            publication.inputs, publication.dependency_frontier);
        expect(read.status == DerivedPublicationAdmissionStatus::StoredMatch && read.publication,
               "authoritative publication did not read as a stored match");

        auto old_inputs = publication.inputs;
        const auto old_frontier = publication.dependency_frontier;
        units.update_mutable_fields(first.id, KnowledgeUnitMutablePatch{
            std::string{"updated display"}, std::nullopt, std::nullopt,
            std::nullopt, std::nullopt});
        const auto changed = admission.read(
            first.scope_id, publication.publication_id, old_inputs, old_frontier);
        expect(changed.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "old caller metadata was treated as an authoritative current frontier");

        expect(admission.publish(publication).status == DerivedPublicationAdmissionStatus::Rejected,
               "publication against an old canonical revision was admitted");

        units.update_mutable_fields(first.id, KnowledgeUnitMutablePatch{
            std::nullopt, std::nullopt, KnowledgeUnitLifecycleState::Deprecated,
            std::nullopt, std::nullopt});
        const auto inactive = admission.read(
            first.scope_id, publication.publication_id, old_inputs, old_frontier);
        expect(inactive.status == DerivedPublicationAdmissionStatus::InactiveInput,
               "inactive canonical input was admitted");

        const auto missing = admission.read(
            first.scope_id, publication.publication_id,
            std::vector<DerivedPublicationInput>{{KnowledgeUnitId{999999}, 1, 1, "missing"}},
            "missing");
        expect(missing.status == DerivedPublicationAdmissionStatus::MissingInput,
               "missing canonical input was not rejected");

        const auto wrong_scope = admission.read(
            ScopeId{"workspace-b"}, publication.publication_id,
            old_inputs, old_frontier);
        expect(wrong_scope.status == DerivedPublicationAdmissionStatus::ScopeMismatch,
               "cross-scope canonical input was admitted");
    }
    std::error_code error;
    std::filesystem::remove(path, error);
    expect(!error && !std::filesystem::exists(path),
           "admission test database could not be removed");
    return 0;
}

#else
int main() { return 0; }
#endif
