#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/storage/DerivedPublicationAdmission.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <functional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void make_removable(const std::filesystem::path& path) noexcept {
#ifdef _WIN32
    if(std::filesystem::exists(path))
        SetFileAttributesW(path.wstring().c_str(), FILE_ATTRIBUTE_NORMAL);
#else
    static_cast<void>(path);
#endif
}

class TestDatabase final {
public:
    TestDatabase() {
        const auto temporary = std::filesystem::temp_directory_path();
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        for(std::uint64_t attempt = 0; attempt < 64; ++attempt) {
            const auto candidate = temporary /
                ("agent_memory_derived_publication_admission_" +
                 std::to_string(nonce) + "_" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                m_directory = candidate;
                m_path = candidate / "admission.mdbx";
                m_lock_path = std::filesystem::path{m_path.string() + "-lck"};
                return;
            }
            if(error) throw std::runtime_error("admission test directory creation failed");
        }
        throw std::runtime_error("admission test directory reservation failed");
    }

    ~TestDatabase() {
        std::error_code error;
        if(!m_path.empty()) std::filesystem::remove(m_path, error);
        error.clear();
        if(!m_lock_path.empty()) {
            make_removable(m_lock_path);
            std::filesystem::remove(m_lock_path, error);
        }
        error.clear();
        if(!m_directory.empty()) std::filesystem::remove(m_directory, error);
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return m_path; }

    void remove_or_fail() {
        std::error_code error;
        const auto removed_file = std::filesystem::remove(m_path, error);
        expect(!error && removed_file && !std::filesystem::exists(m_path),
               "admission test database could not be removed");
        error.clear();
        make_removable(m_lock_path);
        const auto removed_lock = std::filesystem::remove(m_lock_path, error);
        expect(!error && removed_lock && !std::filesystem::exists(m_lock_path),
               "admission test lock file could not be removed");
        error.clear();
        const auto removed_directory = std::filesystem::remove(m_directory, error);
        expect(!error && removed_directory && !std::filesystem::exists(m_directory),
               "admission test directory could not be removed");
        m_path.clear();
        m_lock_path.clear();
        m_directory.clear();
    }

private:
    std::filesystem::path m_directory;
    std::filesystem::path m_path;
    std::filesystem::path m_lock_path;
};

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

agent_memory::ResourceManifest make_manifest(
    const agent_memory::ResourceId& resource_id,
    std::uint64_t generation
) {
    using namespace agent_memory;
    return ResourceManifest{
        ResourceRevision{resource_id, generation, 0xAABBCCDDU, 0x11223344U},
        {DerivedRecordRef{DerivedRecordKind::Chunk, ChunkId{"chunk:resource:0"}, {}, 0}}
    };
}

agent_memory::DerivedPublicationRequest make_publication(
    const agent_memory::ScopeId& scope_id,
    std::string publication_id,
    const agent_memory::DerivedPublicationUnitSnapshot& snapshot
) {
    using namespace agent_memory;
    DerivedPublicationRequest publication;
    publication.scope_id = scope_id;
    publication.publication_id = std::move(publication_id);
    publication.kind = ProjectionKind::QACombined;
    publication.output_revision = 1;
    publication.generation = 1;
    publication.body = "locally admitted answer";
    publication.inputs = snapshot.inputs;
    publication.dependency_frontier = snapshot.dependency_frontier;
    return publication;
}

class UnavailableSnapshotProvider final
    : public agent_memory::IDerivedPublicationUnitSnapshotProvider {
public:
    [[nodiscard]] agent_memory::DerivedPublicationUnitSnapshotResult snapshot(
        const agent_memory::ScopeId&,
        const std::vector<agent_memory::KnowledgeUnitId>&
    ) const override {
        return {agent_memory::DerivedPublicationUnitSnapshotStatus::Unavailable, std::nullopt};
    }
};

class EmptyAvailableSnapshotProvider final
    : public agent_memory::IDerivedPublicationUnitSnapshotProvider {
public:
    [[nodiscard]] agent_memory::DerivedPublicationUnitSnapshotResult snapshot(
        const agent_memory::ScopeId&,
        const std::vector<agent_memory::KnowledgeUnitId>&
    ) const override {
        return {agent_memory::DerivedPublicationUnitSnapshotStatus::Available, std::nullopt};
    }
};

class MutationAfterSnapshotProvider final
    : public agent_memory::IDerivedPublicationUnitSnapshotProvider {
public:
    MutationAfterSnapshotProvider(
        const agent_memory::IDerivedPublicationUnitSnapshotProvider& delegate,
        std::function<void()> mutation
    )
        : m_delegate(&delegate), m_mutation(std::move(mutation)) {}

    [[nodiscard]] agent_memory::DerivedPublicationUnitSnapshotResult snapshot(
        const agent_memory::ScopeId& scope_id,
        const std::vector<agent_memory::KnowledgeUnitId>& unit_ids
    ) const override {
        auto result = m_delegate->snapshot(scope_id, unit_ids);
        if(!m_mutated) {
            m_mutated = true;
            m_mutation();
        }
        return result;
    }

private:
    const agent_memory::IDerivedPublicationUnitSnapshotProvider* m_delegate;
    std::function<void()> m_mutation;
    mutable bool m_mutated = false;
};

class ThrowingFindStore final : public agent_memory::IKnowledgeUnitStore {
public:
    explicit ThrowingFindStore(agent_memory::IKnowledgeUnitStore& delegate)
        : m_delegate(&delegate) {}

    void upsert(agent_memory::KnowledgeUnitEnvelope envelope) override {
        m_delegate->upsert(std::move(envelope));
    }

    [[nodiscard]] bool erase(const agent_memory::KnowledgeUnitId& id) override {
        return m_delegate->erase(id);
    }

    [[nodiscard]] std::vector<agent_memory::KnowledgeUnitId> scan_by_scope_and_kind(
        const agent_memory::ScopeId& scope_id,
        agent_memory::KnowledgeUnitKind kind
    ) const override {
        return m_delegate->scan_by_scope_and_kind(scope_id, kind);
    }

    [[nodiscard]] std::vector<agent_memory::KnowledgeUnitId> scan_by_scope(
        const agent_memory::ScopeId& scope_id
    ) const override {
        return m_delegate->scan_by_scope(scope_id);
    }

    [[nodiscard]] agent_memory::KnowledgeUnitEnvelope create_or_get_unit(
        agent_memory::KnowledgeUnitCreateRequest request
    ) override {
        return m_delegate->create_or_get_unit(std::move(request));
    }

    [[nodiscard]] std::optional<agent_memory::KnowledgeUnitEnvelope> find(
        const agent_memory::KnowledgeUnitId&
    ) const override {
        throw std::runtime_error("injected KnowledgeUnit read failure");
    }

    [[nodiscard]] std::optional<agent_memory::KnowledgeUnitId> find_by_content_key(
        const agent_memory::KnowledgeUnitKey& key
    ) const override {
        return m_delegate->find_by_content_key(key);
    }

    [[nodiscard]] agent_memory::KnowledgeUnitEnvelope update_mutable_fields(
        const agent_memory::KnowledgeUnitId& id,
        agent_memory::KnowledgeUnitMutablePatch patch
    ) override {
        return m_delegate->update_mutable_fields(id, std::move(patch));
    }

private:
    agent_memory::IKnowledgeUnitStore* m_delegate;
};

} // namespace

int main() {
    using namespace agent_memory;
    TestDatabase database;
    KnowledgeUnitId first_id;
    ScopeId scope{"workspace-a"};
    DerivedPublicationRequest initial_publication;

    {
        auto context = MdbxStorageContext::open(database.path().string());
        MdbxResourceManifestStorage manifests({"", "agent_memory", false,
                                               kDefaultMdbxMaxDbs, context});
        const ResourceId first_resource{"resource:first;source:@:|"};
        manifests.upsert_manifest(make_manifest(first_resource, 1));

        MdbxKnowledgeUnitStore units({"", "agent_memory", false,
                                      kDefaultMdbxMaxDbs, context});
        const auto first = units.create_or_get_unit(
            make_request(scope.value(), first_resource.value(), 3));
        const auto second = units.create_or_get_unit(
            make_request(scope.value(), "resource:second", 7));
        const auto foreign = units.create_or_get_unit(
            make_request("workspace-b", "resource:foreign", 11));
        first_id = first.id;

        KnowledgeUnitDerivedPublicationSnapshotProvider unit_provider(units);
        MdbxDerivedPublicationStore publications({"", "agent_memory", false,
                                                  kDefaultMdbxMaxDbs, context});
        DerivedPublicationAdmission admission(publications, unit_provider);

        const std::vector<KnowledgeUnitId> ids{first.id, second.id};
        const auto snapshot = unit_provider.snapshot(scope, ids);
        expect(snapshot.status == DerivedPublicationUnitSnapshotStatus::Available &&
                   snapshot.snapshot,
               "KnowledgeUnit-local snapshot was unavailable");
        expect(snapshot.snapshot->dependency_frontier.rfind("ku-local-v1|", 0) == 0,
               "unit snapshot did not use the versioned length-prefixed encoding");
        expect(snapshot.snapshot->dependency_frontier.find(
                   "resource_id=25:resource:first;source:@:||") != std::string::npos,
               "special ResourceId characters were not length-prefixed");
        const auto repeated_snapshot = unit_provider.snapshot(scope, ids);
        expect(repeated_snapshot.snapshot &&
                   repeated_snapshot.snapshot->dependency_frontier ==
                       snapshot.snapshot->dependency_frontier,
               "unit snapshot encoding was not deterministic");

        const auto duplicate = unit_provider.snapshot(scope, {first.id, first.id});
        expect(duplicate.status == DerivedPublicationUnitSnapshotStatus::InvalidInput,
               "duplicate KnowledgeUnit ids were admitted");
        std::vector<KnowledgeUnitId> oversized(4097, first.id);
        const auto over_budget = unit_provider.snapshot(scope, oversized);
        expect(over_budget.status == DerivedPublicationUnitSnapshotStatus::InvalidInput,
               "over-budget unit snapshot request was admitted");

        initial_publication = make_publication(
            scope, "qa-unit-local-1", *snapshot.snapshot);
        const auto published = admission.publish(initial_publication);
        expect(published.status ==
                   DerivedPublicationAdmissionStatus::PublishedAfterUnitCheck,
               "KnowledgeUnit-local publication was not admitted");

        const auto read = admission.read(
            scope, initial_publication.publication_id,
            initial_publication.inputs, initial_publication.dependency_frontier);
        expect(read.status == DerivedPublicationAdmissionStatus::KnowledgeUnitLocalMatch &&
                   read.publication,
               "publication did not read as a KnowledgeUnit-local match");

        manifests.upsert_manifest(make_manifest(first_resource, 2));
        const auto after_resource_advance = admission.read(
            scope, initial_publication.publication_id,
            initial_publication.inputs, initial_publication.dependency_frontier);
        expect(after_resource_advance.status ==
                   DerivedPublicationAdmissionStatus::KnowledgeUnitLocalMatch,
               "unit-local result changed without a KnowledgeUnit revision");

        const auto reversed_snapshot = unit_provider.snapshot(scope, {second.id, first.id});
        expect(reversed_snapshot.snapshot.has_value(),
               "reversed unit snapshot was unavailable");
        const auto reversed = admission.read(
            scope, initial_publication.publication_id,
            reversed_snapshot.snapshot->inputs,
            reversed_snapshot.snapshot->dependency_frontier);
        expect(reversed.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "ordered lineage accepted a reordered input list");

        const auto duplicate_read = admission.read(
            scope, initial_publication.publication_id,
            {initial_publication.inputs.front(), initial_publication.inputs.front()},
            initial_publication.dependency_frontier);
        expect(duplicate_read.status == DerivedPublicationAdmissionStatus::InvalidInput,
               "duplicate caller lineage was not rejected");

        const auto first_only_snapshot = unit_provider.snapshot(scope, {first.id});
        expect(first_only_snapshot.snapshot.has_value(),
               "single-input snapshot was unavailable");
        const auto omitted = admission.read(
            scope, initial_publication.publication_id,
            first_only_snapshot.snapshot->inputs,
            first_only_snapshot.snapshot->dependency_frontier);
        expect(omitted.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "omitted causal input matched a two-input publication");

        auto mismatched_revision = initial_publication.inputs;
        ++mismatched_revision.front().unit_revision;
        const auto mismatched = admission.read(
            scope, initial_publication.publication_id,
            mismatched_revision, initial_publication.dependency_frontier);
        expect(mismatched.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "mismatched caller revision was admitted");

        const auto wrong_scope = admission.read(
            scope, initial_publication.publication_id,
            {{foreign.id, foreign.revision, 1, "foreign"}}, "foreign");
        expect(wrong_scope.status == DerivedPublicationAdmissionStatus::ScopeMismatch,
               "cross-scope canonical input was admitted");

        auto independent_publication = make_publication(
            scope, "qa-unit-local-independent", *first_only_snapshot.snapshot);
        expect(admission.publish(independent_publication).status ==
                   DerivedPublicationAdmissionStatus::PublishedAfterUnitCheck,
               "independent publication identity was not preserved");

        auto partial_inputs = initial_publication.inputs;
        partial_inputs.push_back({KnowledgeUnitId{999999}, 1, 1, "missing"});
        const auto partial = admission.read(
            scope, initial_publication.publication_id, partial_inputs, "missing");
        expect(partial.status == DerivedPublicationAdmissionStatus::MissingInput,
               "partially unavailable input set was not rejected");

        UnavailableSnapshotProvider unavailable_provider;
        DerivedPublicationAdmission unavailable_admission(publications, unavailable_provider);
        expect(unavailable_admission.read(
                   scope, initial_publication.publication_id,
                   initial_publication.inputs,
                   initial_publication.dependency_frontier).status ==
                   DerivedPublicationAdmissionStatus::Unavailable,
               "unavailable unit snapshot was masked as a storage result");

        EmptyAvailableSnapshotProvider empty_provider;
        DerivedPublicationAdmission empty_admission(publications, empty_provider);
        expect(empty_admission.read(
                   scope, initial_publication.publication_id,
                   initial_publication.inputs,
                   initial_publication.dependency_frontier).status ==
                   DerivedPublicationAdmissionStatus::Unavailable,
               "malformed available snapshot was masked as a mismatch");

        ThrowingFindStore throwing_units(units);
        KnowledgeUnitDerivedPublicationSnapshotProvider throwing_provider(throwing_units);
        DerivedPublicationAdmission throwing_admission(publications, throwing_provider);
        expect(throwing_admission.read(
                   scope, initial_publication.publication_id,
                   initial_publication.inputs,
                   initial_publication.dependency_frontier).status ==
                   DerivedPublicationAdmissionStatus::Unavailable,
               "KnowledgeUnit storage failure was masked as absence or mismatch");

        const auto before_read_race = unit_provider.snapshot(scope, ids);
        expect(before_read_race.snapshot.has_value(),
               "pre-read-race unit snapshot was unavailable");
        MutationAfterSnapshotProvider read_race_provider(
            unit_provider,
            [&units, second]() {
                const auto updated = units.update_mutable_fields(
                    second.id, KnowledgeUnitMutablePatch{
                        std::string{"changed during admitted read"}, std::nullopt,
                        std::nullopt, std::nullopt, std::nullopt});
                expect(updated.revision > second.revision,
                       "injected read-race mutation did not advance revision");
            });
        DerivedPublicationAdmission read_race_admission(publications, read_race_provider);
        const auto racing_read = read_race_admission.read(
            scope, initial_publication.publication_id,
            before_read_race.snapshot->inputs,
            before_read_race.snapshot->dependency_frontier);
        expect(racing_read.status ==
                   DerivedPublicationAdmissionStatus::KnowledgeUnitLocalMatch,
               "local read-race semantics changed unexpectedly");
        const auto post_read_race = admission.read(
            scope, initial_publication.publication_id,
            initial_publication.inputs, initial_publication.dependency_frontier);
        expect(post_read_race.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "post-read validation did not observe the unit update");

        const auto before_publish_race = unit_provider.snapshot(scope, ids);
        expect(before_publish_race.snapshot.has_value(),
               "pre-publication-race unit snapshot was unavailable");
        MutationAfterSnapshotProvider mutating_provider(
            unit_provider,
            [&units, first_id]() {
                const auto updated = units.update_mutable_fields(
                    first_id, KnowledgeUnitMutablePatch{
                    std::string{"changed after snapshot"}, std::nullopt, std::nullopt,
                    std::nullopt, std::nullopt});
                expect(updated.revision > 1, "injected unit mutation did not advance revision");
            });
        DerivedPublicationAdmission mutating_admission(publications, mutating_provider);
        auto racing_publication = make_publication(
            scope, "qa-unit-local-toctou", *before_publish_race.snapshot);
        expect(mutating_admission.publish(racing_publication).status ==
                   DerivedPublicationAdmissionStatus::PublishedAfterUnitCheck,
               "local TOCTOU semantics changed unexpectedly");
        const auto post_race = admission.read(
            scope, racing_publication.publication_id,
            racing_publication.inputs, racing_publication.dependency_frontier);
        expect(post_race.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "post-publication validation did not observe the unit update");
    }

    {
        auto context = MdbxStorageContext::open(database.path().string());
        MdbxKnowledgeUnitStore units({"", "agent_memory", false,
                                      kDefaultMdbxMaxDbs, context});
        KnowledgeUnitDerivedPublicationSnapshotProvider unit_provider(units);
        MdbxDerivedPublicationStore publications({"", "agent_memory", false,
                                                  kDefaultMdbxMaxDbs, context});
        DerivedPublicationAdmission admission(publications, unit_provider);

        const auto reopened = admission.read(
            scope, initial_publication.publication_id,
            initial_publication.inputs, initial_publication.dependency_frontier);
        expect(reopened.status == DerivedPublicationAdmissionStatus::StoredMismatch,
               "reopen lost the corrected KnowledgeUnit revision");

        const auto deprecated = units.update_mutable_fields(
            first_id, KnowledgeUnitMutablePatch{
            std::nullopt, std::nullopt, KnowledgeUnitLifecycleState::Deprecated,
            std::nullopt, std::nullopt});
        expect(deprecated.lifecycle_state == KnowledgeUnitLifecycleState::Deprecated,
               "unit deprecation did not persist");
        const auto inactive = admission.read(
            scope, initial_publication.publication_id,
            initial_publication.inputs, initial_publication.dependency_frontier);
        expect(inactive.status == DerivedPublicationAdmissionStatus::InactiveInput,
               "inactive canonical input was admitted after reopen");
    }

    database.remove_or_fail();
    return 0;
}

#else
int main() { return 0; }
#endif
