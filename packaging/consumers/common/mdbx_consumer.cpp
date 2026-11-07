#include <agent_memory/core/LibraryInfo.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

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

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary_directory;
        const auto database_path = temporary_directory.path() / "database.mdbx";
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

        std::string image_body{"\x89PNG\r\n\x1a\n"};
        image_body.push_back('\0');
        image_body.push_back('\x01');
        image_body.push_back('\x02');
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
