#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

using namespace agent_memory;

const ScopeId scope{"preview"};
constexpr const char* prefix = "preview";
constexpr const char* profile =
    "agent_memory.m0_resource_importer.raw_chunks_original@preview";

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

class TemporaryDirectory final {
public:
    TemporaryDirectory() {
        const auto root = std::filesystem::temp_directory_path();
        std::random_device random;
        for(std::size_t attempt = 0; attempt != 32; ++attempt) {
            const auto candidate = root / ("agent-memory-raw-markdown-consumer-" +
                std::to_string(random()) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
        }
        throw std::runtime_error("unable to create a temporary directory");
    }
    ~TemporaryDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }
    const std::filesystem::path& path() const noexcept { return path_; }
private:
    std::filesystem::path path_;
};

RawMarkdownProductIngestionOptions options(
    const std::shared_ptr<MdbxStorageContext>& context,
    const std::filesystem::path& cas
) {
    RawMarkdownProductIngestionOptions result;
    result.context = context;
    result.table_prefix = prefix;
    result.raw_cas_root = cas;
    return result;
}

RawMarkdownProductImportResult publish(
    MdbxRawMarkdownProductIngestion& product, const char* id,
    const std::string& body, std::uint64_t generation
) {
    const auto result = product.import(ObservedResource{
        scope, ResourceId{id}, std::string{id} + ".md", body,
        SourceTextOrigin::OriginalText, std::nullopt, 1
    }, generation);
    expect(result.status == RawMarkdownProductImportStatus::Published &&
           result.resource && result.resource->raw_units.size() == 1 &&
           result.source_revision_id && result.artifact_binding_id &&
           result.resource_artifact_binding && result.artifact,
           "installed import did not publish complete provenance");
    const auto& revision = result.resource->revision;
    expect(revision.resource_id == ResourceId{id} &&
           revision.generation == generation && revision.body_digest &&
           revision.body_digest->algorithm == ResourceBodyDigestAlgorithm::Sha256 &&
           revision.body_digest->bytes == sha256(body),
           "published resource identity/generation/SHA-256 mismatch");
    return result;
}

RawMarkdownProductHit read_one(
    MdbxRawMarkdownProductIngestion& product, const char* query
) {
    const auto hits = product.retrieve_and_hydrate(
        scope, RetrievalQuery{query, std::nullopt, 8, {}}, 4096);
    expect(hits.size() == 1, "expected exactly one installed-consumer candidate");
    return hits.front();
}

void verify_bytes(
    const RawMarkdownProductHit& hit,
    const RawMarkdownProductImportResult& imported, const std::string& body,
    const std::filesystem::path& cas
) {
    const auto& candidate = hit.candidate;
    const auto& hydrated = hit.hydration;
    expect(candidate.freshness == RevisionBoundFreshness::Current &&
           candidate.projection_kind == ProjectionKind::Original &&
           candidate.knowledge_unit_id == imported.resource->raw_units.front() &&
           candidate.scope_id == scope && candidate.unit_revision != 0 &&
           candidate.root_resource_id == imported.resource->resource_id &&
           candidate.root_generation == imported.resource->revision.generation &&
           candidate.root_digest && candidate.root_digest->bytes == sha256(body) &&
           candidate.source && candidate.source->resource_revision &&
           candidate.source->resource_revision->resource_id == candidate.root_resource_id &&
           candidate.source->resource_revision->generation == candidate.root_generation &&
           candidate.source->resource_revision->body_digest.bytes == sha256(body),
           "current candidate identity/evidence mismatch");
    expect(hydrated.status == AuthoritativeArticleHydrationStatus::Hydrated &&
           hydrated.knowledge_unit_id == candidate.knowledge_unit_id &&
           hydrated.scope_id == scope && hydrated.unit_revision == candidate.unit_revision &&
           hydrated.source_range.offset == 0 && hydrated.source_range.length == body.size() &&
           hydrated.bytes == body && hydrated.resource_revision &&
           hydrated.resource_revision->resource_id == candidate.root_resource_id &&
           hydrated.resource_revision->generation == candidate.root_generation &&
           hydrated.resource_revision->body_digest.bytes == sha256(body) &&
           hydrated.source_revision_id == imported.source_revision_id &&
           hydrated.artifact_binding_id == imported.artifact_binding_id &&
           hydrated.artifact_id && hydrated.artifact_digest &&
           *hydrated.artifact_id == imported.artifact->artifact.id &&
           hydrated.artifact_digest->algorithm == BlobDigestAlgorithm::Sha256 &&
           hydrated.artifact_digest->value == sha256(body),
           "hydration lost exact bytes, revision or artifact binding");
    const FileRawBlobStore blobs{cas};
    const auto probe = blobs.probe(*hydrated.artifact_id);
    const auto full = blobs.read_range_bounded(*hydrated.artifact_id, 0, body.size(), 4096);
    expect(probe.status == RawBlobStatus::Ok && probe.metadata &&
           probe.metadata->digest.algorithm == BlobDigestAlgorithm::Sha256 &&
           probe.metadata->digest.value == sha256(body) &&
           probe.metadata->size_bytes == body.size() &&
           full.status == RawBlobStatus::Ok && full.bytes == body,
           "full restored CAS object digest/bytes mismatch");
}

void verify_same_evidence(const RawMarkdownProductHit& before,
                          const RawMarkdownProductHit& after) {
    const auto& a = before.candidate;
    const auto& b = after.candidate;
    expect(a.knowledge_unit_id == b.knowledge_unit_id &&
           a.unit_revision == b.unit_revision && a.projection_kind == b.projection_kind &&
           a.root_resource_id == b.root_resource_id && a.root_generation == b.root_generation &&
           a.root_digest->bytes == b.root_digest->bytes &&
           a.source->uri == b.source->uri && a.source->quote_hash == b.source->quote_hash &&
           a.source->excerpt.offset == b.source->excerpt.offset &&
           a.source->excerpt.length == b.source->excerpt.length &&
           a.source->preview == b.source->preview &&
           before.hydration.source_revision_id == after.hydration.source_revision_id &&
           before.hydration.artifact_binding_id == after.hydration.artifact_binding_id &&
           before.hydration.artifact_id == after.hydration.artifact_id &&
           before.hydration.artifact_digest->value == after.hydration.artifact_digest->value &&
           before.hydration.bytes == after.hydration.bytes,
           "reopen/restore changed canonical identity or evidence");
}

void verify_stale(const std::shared_ptr<MdbxStorageContext>& context,
                  const std::filesystem::path& cas, const RawMarkdownProductHit& old) {
    MdbxAuthoritativeArticleHydrationOptions hydration_options;
    hydration_options.context = context;
    hydration_options.table_prefix = prefix;
    hydration_options.raw_cas_root = cas;
    hydration_options.expected_manifest_schema_id = profile;
    MdbxAuthoritativeArticleHydrator hydrator{std::move(hydration_options)};
    MdbxAuthoritativeArticleHydrationRequest request;
    request.candidate = {old.candidate.knowledge_unit_id, old.candidate.unit_revision,
                         old.candidate.projection_kind, old.candidate.score};
    request.scope_id = scope;
    request.source_range = old.hydration.source_range;
    request.claimed_resource_revision = old.hydration.resource_revision;
    request.claimed_artifact_digest = old.hydration.artifact_digest;
    request.max_bytes = 4096;
    const auto rejected = hydrator.hydrate(request);
    expect(rejected.status == AuthoritativeArticleHydrationStatus::Stale &&
           rejected.bytes.empty(), "retained A@1 hydrated after A@2/restore");
    StandardTokenizer tokenizer;
    ProjectionAwareLexicalRetrieverOptions admission_options;
    admission_options.expected_manifest_schema_id = profile;
    MdbxSnapshotRevisionBoundRetriever admission{
        context, scope, tokenizer, admission_options, prefix};
    const auto admitted = admission.admit_candidate(request.candidate);
    expect(admitted.freshness == RevisionBoundFreshness::Stale && admitted.text.empty(),
           "retained A@1 admission was not stale after A@2/restore");
}

// Host-side preflight: open() can create a new database; absence is not recovery.
void copy_closed_storage(const std::filesystem::path& source,
                         const std::filesystem::path& destination) {
    expect(std::filesystem::is_regular_file(source / "memory.mdbx") &&
           std::filesystem::is_directory(source / "cas"),
           "incomplete cold backup: MDBX file and CAS directory are required");
    expect(!std::filesystem::exists(destination), "restore destination must be fresh");
    std::filesystem::create_directory(destination);
    std::filesystem::copy_file(source / "memory.mdbx", destination / "memory.mdbx");
    std::filesystem::copy(source / "cas", destination / "cas",
                          std::filesystem::copy_options::recursive);
}

void verify_unavailable(MdbxRawMarkdownProductIngestion& product,
                        const RawMarkdownProductHit& expected,
                        AuthoritativeArticleHydrationStatus status,
                        AuthoritativeArticleHydrationDiagnostic diagnostic) {
    const auto hit = read_one(product, "alphatwo");
    expect(hit.candidate.knowledge_unit_id == expected.candidate.knowledge_unit_id &&
           hit.candidate.freshness == RevisionBoundFreshness::Current &&
           hit.hydration.status == status && hit.hydration.diagnostic_code == diagnostic &&
           hit.hydration.bytes.empty(), "incomplete CAS exposed authoritative bytes");
}

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary;
        const auto source = temporary.path() / "source";
        std::filesystem::create_directory(source);
        const auto database = source / "memory.mdbx";
        const auto cas = source / "cas";
        const std::string a1 = "# alphaone\n\nExact original Markdown evidence.\n";
        const std::string a2 = "# alphatwo\n\nRevised Markdown evidence, retained exactly.\n";
        const std::string b1 = "# betaonly\n\nAn independent second resource.\n";
        RawMarkdownProductImportResult imported_a1, imported_a2, imported_b;
        RawMarkdownProductHit old, current_a, current_b;
        {
            auto context = MdbxStorageContext::open(database.string());
            MdbxRawMarkdownProductIngestion product{options(context, cas)};
            imported_a1 = publish(product, "a", a1, 1);
            imported_b = publish(product, "b", b1, 1);
            old = read_one(product, "alphaone");
            verify_bytes(old, imported_a1, a1, cas);
            current_b = read_one(product, "betaonly");
            verify_bytes(current_b, imported_b, b1, cas);
        }
        {
            auto context = MdbxStorageContext::open(database.string());
            MdbxRawMarkdownProductIngestion product{options(context, cas)};
            const auto reopened = read_one(product, "alphaone");
            verify_bytes(reopened, imported_a1, a1, cas);
            verify_same_evidence(old, reopened);
            imported_a2 = publish(product, "a", a2, 2);
            current_a = read_one(product, "alphatwo");
            verify_bytes(current_a, imported_a2, a2, cas);
            verify_stale(context, cas, old);
            auto bounded_options = options(context, cas);
            bounded_options.max_indexed_projection_bytes = 1;
            MdbxRawMarkdownProductIngestion bounded{std::move(bounded_options)};
            bool exceeded = false;
            try { (void)read_one(bounded, "alphatwo"); }
            catch(const ProjectionAwareLexicalRetrievalBudgetExceeded&) { exceeded = true; }
            expect(exceeded, "budget overflow did not throw the expected typed exception");
        }

        // All adapters, options and shared contexts above are destroyed before copying.
        const auto backup = temporary.path() / "backup";
        const auto restored = temporary.path() / "restored";
        copy_closed_storage(source, backup);
        copy_closed_storage(backup, restored);
        {
            auto context = MdbxStorageContext::open((restored / "memory.mdbx").string());
            MdbxRawMarkdownProductIngestion product{options(context, restored / "cas")};
            const auto restored_a = read_one(product, "alphatwo");
            const auto restored_b = read_one(product, "betaonly");
            verify_bytes(restored_a, imported_a2, a2, restored / "cas");
            verify_bytes(restored_b, imported_b, b1, restored / "cas");
            verify_same_evidence(current_a, restored_a);
            verify_same_evidence(current_b, restored_b);
            verify_stale(context, restored / "cas", old);

            MdbxRawMarkdownProductIngestion missing{
                options(context, restored / "missing-cas")};
            verify_unavailable(missing, current_a, AuthoritativeArticleHydrationStatus::Missing,
                               AuthoritativeArticleHydrationDiagnostic::CasMissing);

            auto wrong_options = options(context, restored / "cas");
            wrong_options.table_prefix = "wrong";
            MdbxRawMarkdownProductIngestion wrong_prefix{std::move(wrong_options)};
            expect(wrong_prefix.retrieve_and_hydrate(
                scope, RetrievalQuery{"alphatwo", std::nullopt, 8, {}}, 4096).empty(),
                "wrong table prefix exposed another namespace");

            MdbxAuthoritativeArticleHydrationOptions incompatible_options;
            incompatible_options.context = context;
            incompatible_options.table_prefix = prefix;
            incompatible_options.raw_cas_root = restored / "cas";
            incompatible_options.expected_manifest_schema_id = profile;
            incompatible_options.expected_manifest_schema_version = 2;
            MdbxAuthoritativeArticleHydrator incompatible{std::move(incompatible_options)};
            MdbxAuthoritativeArticleHydrationRequest request;
            request.candidate = {current_a.candidate.knowledge_unit_id,
                                 current_a.candidate.unit_revision, ProjectionKind::Original, 1.0F};
            request.scope_id = scope;
            request.source_range = current_a.hydration.source_range;
            request.claimed_resource_revision = current_a.hydration.resource_revision;
            request.max_bytes = 4096;
            const auto unsupported_profile = incompatible.hydrate(request);
            expect(unsupported_profile.status == AuthoritativeArticleHydrationStatus::Stale &&
                   unsupported_profile.diagnostic_code ==
                       AuthoritativeArticleHydrationDiagnostic::ProjectionMissing &&
                   unsupported_profile.bytes.empty(),
                   "incompatible manifest profile exposed source bytes");

            // Damage only the restored fixture, never the source or backup.
            const auto digest = current_a.hydration.artifact_id->value().substr(7);
            const auto object = restored / "cas" / "sha256" / digest.substr(0, 2) /
                (digest + ".raw");
            std::ofstream corrupt(object, std::ios::binary | std::ios::trunc);
            corrupt << "not the published object";
            corrupt.close();
            expect(static_cast<bool>(corrupt), "unable to corrupt restored CAS fixture");
            verify_unavailable(product, current_a, AuthoritativeArticleHydrationStatus::Corrupt,
                               AuthoritativeArticleHydrationDiagnostic::CasCorrupt);
        }
        const auto incomplete = temporary.path() / "incomplete";
        std::filesystem::create_directory(incomplete);
        for(const bool database_only : {true, false}) {
            if(database_only) {
                std::filesystem::copy_file(backup / "memory.mdbx", incomplete / "memory.mdbx");
            } else {
                std::filesystem::copy(backup / "cas", incomplete / "cas",
                                      std::filesystem::copy_options::recursive);
                // Remove only the known fixture file after no MDBX handle is open.
                expect(std::filesystem::remove(incomplete / "memory.mdbx"),
                       "incomplete backup fixture file was not removed");
            }
            bool rejected = false;
            try { copy_closed_storage(incomplete, temporary.path() / "must-not-restore"); }
            catch(const std::runtime_error&) { rejected = true; }
            expect(rejected && !std::filesystem::exists(temporary.path() / "must-not-restore"),
                   "host preflight accepted incomplete backup inputs");
        }
        std::cout << "installed Markdown: A@1/reopen/A@2/stale/budget PASS\n"
                     "offline backup/restore: two resources, identities/digests/bytes PASS\n"
                     "missing/corrupt CAS, wrong prefix/profile, incomplete backup preflight PASS\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
