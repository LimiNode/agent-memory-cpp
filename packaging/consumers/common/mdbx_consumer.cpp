#include <agent_memory/core/LibraryInfo.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalCandidateAdapter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <array>
#include <filesystem>
#include <fstream>
#include <random>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>

namespace {

class TemporaryDirectory final {
public:
    TemporaryDirectory() {
        const auto root = std::filesystem::temp_directory_path();
        std::random_device random;
        for(std::size_t attempt = 0; attempt != 32; ++attempt) {
            const auto candidate = root / ("agent-memory-mdbx-consumer-"
                + std::to_string(random()) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
        }
        throw std::runtime_error("unable to create a reserved temporary directory");
    }

    ~TemporaryDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }

    TemporaryDirectory(const TemporaryDirectory&) = delete;
    TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

private:
    std::filesystem::path path_;
};

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path cas_path_for(
    const std::filesystem::path& root,
    const agent_memory::ArtifactId& artifact_id) {
    const auto value = artifact_id.value();
    require(value.rfind("sha256:", 0) == 0, "catalog returned a non-SHA-256 artifact identity");
    const auto digest = value.substr(7);
    return root / "sha256" / digest.substr(0, 2) / (digest + ".raw");
}

void verify_bm25_reopen(const std::filesystem::path& database_path) {
    const agent_memory::ScopeId scope{"installed-lexical"};
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 1;
    agent_memory::MdbxPersistentLexicalIndexOptions options;
    options.path = database_path.string();
    options.table_prefix = "installed_lexical";
    options.descriptor = {
        {scope, agent_memory::ProjectionKind::Original,
         agent_memory::LexicalStatisticsEpoch{epoch}},
        "original", 1, "standard-v1", "lowercase-v1", {1}};
    {
        agent_memory::MdbxPersistentLexicalIndex index{options};
        index.upsert({scope, agent_memory::KnowledgeUnitId{1},
            agent_memory::ProjectionKind::Original, agent_memory::ResourceId{"short"}, 1, 1,
            {1, 1, 1, "m0.original.primary_text.v1"}, {"alpha", "alpha"}});
        index.upsert({scope, agent_memory::KnowledgeUnitId{2},
            agent_memory::ProjectionKind::Original, agent_memory::ResourceId{"long"}, 1, 1,
            {1, 1, 1, "m0.original.primary_text.v1"},
            {"alpha", "alpha", "alpha", "filler", "filler", "filler"}});
    }
    {
        agent_memory::MdbxPersistentLexicalIndex reopened{options};
        agent_memory::StandardTokenizer tokenizer;
        agent_memory::PersistentLexicalCandidateOptions ranking;
        ranking.scoring = agent_memory::PersistentLexicalScoring::Bm25;
        agent_memory::MdbxPersistentLexicalCandidateAdapter adapter{reopened, tokenizer, ranking};
        agent_memory::RetrievalQuery query;
        query.text = "alpha";
        query.limit = 1;
        const auto window = adapter.generate_window(query);
        require(window.completeness ==
                    agent_memory::PersistentLexicalCandidateCompleteness::Complete &&
                    window.candidates.size() == 2 &&
                    window.candidates[0].identity.knowledge_unit_id == agent_memory::KnowledgeUnitId{1} &&
                    window.candidates[0].identity.score > window.candidates[1].identity.score,
                "installed BM25 reopen or full candidate window failed");
        // Candidates are not product results: canonical admission is still required.
    }
}

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary_directory;
        const auto database_path = temporary_directory.path() / "database.mdbx";
        verify_bm25_reopen(temporary_directory.path() / "lexical.mdbx");
        agent_memory::MdbxArtifactCatalogOptions options;
        options.path = database_path.string();
        options.table_prefix = "installed_consumer";
        options.raw_cas_root = temporary_directory.path() / "cas";
        options.max_full_body_bytes = 1024;
        options.raw_cas_options.max_read_range_bytes = 1024;

        const agent_memory::ArtifactCatalogIngestRequest request{
            agent_memory::SourceRevisionId{"consumer@1"},
            agent_memory::ArtifactBindingId{"consumer-binding"},
            "source_original",
            agent_memory::ArtifactRetentionClass::SourceOriginal,
            std::string{"text/plain"},
            std::string{"consumer.txt"},
            1,
            "installed consumer body"
        };

        agent_memory::ArtifactCatalogRecord published_record;
        agent_memory::ArtifactCatalogRecord image_record;
        {
            agent_memory::MdbxArtifactCatalog catalog(options);
            const auto published = catalog.ingest(request);
            require(published.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        published.record.has_value(),
                    "installed consumer could not publish the artifact");
            published_record = *published.record;

            const auto full_body = catalog.read_body(request.binding_id);
            require(full_body.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        full_body.bytes == request.body,
                    "full-body read under a sufficient budget failed");

            auto second_binding = request;
            second_binding.binding_id = agent_memory::ArtifactBindingId{"consumer-binding-2"};
            second_binding.role = "extracted_text";
            second_binding.body = "second binding body";
            require(catalog.ingest(second_binding).status == agent_memory::ArtifactCatalogStatus::Ok,
                    "second binding for one source revision failed");
            const auto bindings = catalog.read_source_revision_bindings(request.source_revision_id);
            require(bindings.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        bindings.records.size() == 2,
                    "one-to-many source revision bindings were not retained");
            require(catalog.read_source_revision(request.source_revision_id).status ==
                        agent_memory::ArtifactCatalogStatus::Conflict,
                    "ambiguous source revision lookup selected one binding");

            auto second_revision = request;
            second_revision.source_revision_id = agent_memory::SourceRevisionId{"consumer@2"};
            second_revision.binding_id = agent_memory::ArtifactBindingId{"consumer-binding-3"};
            const auto deduplicated = catalog.ingest(second_revision);
            require(deduplicated.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        deduplicated.record.has_value() &&
                        deduplicated.record->artifact.id == published_record.artifact.id,
                    "identical bodies across source revisions were not CAS-deduplicated");

            require(catalog.ingest(request).status == agent_memory::ArtifactCatalogStatus::Ok,
                    "idempotent duplicate ingest failed");

            auto conflict = request;
            conflict.body = "different body";
            require(catalog.ingest(conflict).status == agent_memory::ArtifactCatalogStatus::Conflict,
                    "conflicting duplicate ingest was accepted");
        }

        {
            auto bounded_options = options;
            bounded_options.max_full_body_bytes = 4;
            bounded_options.raw_cas_options.max_read_range_bytes = 4;
            agent_memory::MdbxArtifactCatalog bounded(std::move(bounded_options));
            require(bounded.read_body(request.binding_id).status ==
                        agent_memory::ArtifactCatalogStatus::InvalidArgument,
                    "full-body budget overflow was not rejected");
            const auto range = bounded.read_body_range(request.binding_id, 0, 4);
            require(range.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        range.bytes == request.body.substr(0, 4),
                    "bounded range read failed after reopen");
        }

        // This is a deterministic valid 1x1 PNG payload. The consumer treats
        // it as opaque bytes; it does not claim to decode or render images.
        const std::string image_body{
            "\x89\x50\x4e\x47\x0d\x0a\x1a\x0a\x00\x00\x00\x0d"
            "\x49\x48\x44\x52\x00\x00\x00\x01\x00\x00\x00\x01"
            "\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0b"
            "\x49\x44\x41\x54\x78\xda\x63\x64\xf8\x0f\x00\x01\x05\x01"
            "\x01\x27\x18\xe3\x66\x00\x00\x00\x00\x49\x45\x4e\x44\xae\x42\x60\x82"};
        auto image_request = request;
        image_request.source_revision_id = agent_memory::SourceRevisionId{"image@1"};
        image_request.binding_id = agent_memory::ArtifactBindingId{"image-binding"};
        image_request.role = "source_image";
        image_request.media_type = std::string{"image/png"};
        image_request.original_name = std::string{"diagram.png"};
        image_request.body = image_body;
        {
            agent_memory::MdbxArtifactCatalog catalog(options);
            const auto image = catalog.ingest(image_request);
            require(image.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        image.record.has_value(),
                    "binary image artifact publication failed");
            image_record = *image.record;
            const auto body = catalog.read_body(image_request.binding_id);
            require(body.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        body.bytes == image_body,
                    "binary image body did not round-trip");
        }

        {
            agent_memory::MdbxArtifactCatalog reopened(options);
            const auto body = reopened.read_body(image_request.binding_id);
            require(body.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        body.bytes == image_body,
                    "reopened catalog did not verify the image body");
            const auto cas_path = cas_path_for(reopened.raw_cas().root(), image_record.artifact.id);
            std::ofstream tampered(cas_path, std::ios::binary | std::ios::trunc);
            require(tampered.good(), "could not prepare image CAS integrity control");
            tampered << "tampered";
            tampered.close();
            const auto corrupt = reopened.read_body(image_request.binding_id);
            require(corrupt.status == agent_memory::ArtifactCatalogStatus::Corrupt &&
                        corrupt.bytes.empty(),
                    "tampered image body returned materialized bytes");
        }

        return agent_memory::library_name() == "Agent Memory C++" ? 0 : 1;
    } catch(...) {
        return 1;
    }
}
