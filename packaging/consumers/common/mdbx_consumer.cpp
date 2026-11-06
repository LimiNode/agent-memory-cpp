#include <agent_memory/core/LibraryInfo.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

#include <filesystem>
#include <fstream>
#include <random>
#include <stdexcept>
#include <string>
#include <system_error>

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

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary_directory;
        const auto database_path = temporary_directory.path() / "database.mdbx";
        agent_memory::MdbxArtifactCatalogOptions options;
        options.path = database_path.string();
        options.table_prefix = "installed_consumer";
        options.raw_cas_root = temporary_directory.path() / "cas";
        options.max_full_body_bytes = 4;
        options.raw_cas_options.max_read_range_bytes = 4;

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
        {
            agent_memory::MdbxArtifactCatalog catalog(options);
            const auto published = catalog.ingest(request);
            require(published.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        published.record.has_value(),
                    "installed consumer could not publish the artifact");
            published_record = *published.record;

            const auto bounded = catalog.read_body(request.binding_id);
            require(bounded.status == agent_memory::ArtifactCatalogStatus::InvalidArgument,
                    "full-body bound was not enforced");
            const auto range = catalog.read_body_range(request.binding_id, 0, 4);
            require(range.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        range.bytes == request.body.substr(0, 4),
                    "verified bounded range read failed");
            require(catalog.ingest(request).status == agent_memory::ArtifactCatalogStatus::Ok,
                    "idempotent duplicate ingest failed");

            auto conflict = request;
            conflict.body = "different body";
            require(catalog.ingest(conflict).status == agent_memory::ArtifactCatalogStatus::Conflict,
                    "conflicting duplicate ingest was accepted");
        }

        {
            agent_memory::MdbxArtifactCatalog reopened(options);
            const auto body = reopened.read_body_range(request.binding_id, 0, 4);
            require(body.status == agent_memory::ArtifactCatalogStatus::Ok &&
                        body.bytes == request.body.substr(0, 4),
                    "reopened catalog did not retain the binding body");

            const auto artifact_value = published_record.artifact.id.value();
            require(artifact_value.rfind("sha256:", 0) == 0,
                    "catalog returned a non-SHA-256 artifact identity");
            const auto digest = artifact_value.substr(7);
            const auto cas_path = reopened.raw_cas().root() / "sha256" /
                digest.substr(0, 2) / (digest + ".raw");
            std::ofstream tampered(cas_path, std::ios::binary | std::ios::trunc);
            require(tampered.good(), "could not prepare CAS integrity control");
            tampered << "tampered";
            tampered.close();
            require(reopened.read_body_range(request.binding_id, 0, 4).status ==
                        agent_memory::ArtifactCatalogStatus::Corrupt,
                    "tampered CAS body passed digest verification");
        }

        return agent_memory::library_name() == "Agent Memory C++" ? 0 : 1;
    } catch(...) {
        return 1;
    }
}
