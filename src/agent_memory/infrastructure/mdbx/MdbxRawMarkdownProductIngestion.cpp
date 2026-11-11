#include "MdbxRawMarkdownProductIngestion.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxArtifactCatalog.hpp"
#include "MdbxResourceManifestStorage.hpp"
#include "MdbxResourceArtifactBindingStore.hpp"
#include "MdbxResourceImporter.hpp"

#include <mdbx_containers/common.hpp>

#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <cctype>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

bool is_markdown_uri(std::string_view uri) {
    const auto slash = uri.find_last_of("/\\");
    const auto dot = uri.find_last_of('.');
    if(dot == std::string_view::npos || (slash != std::string_view::npos && dot < slash)) {
        return false;
    }
    std::string suffix{uri.substr(dot)};
    std::transform(suffix.begin(), suffix.end(), suffix.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return suffix == ".md" || suffix == ".markdown";
}

template <typename Array>
std::string hex_bytes(const Array& bytes) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(bytes.size() * 2U);
    for(const auto byte : bytes) {
        result.push_back(digits[(byte >> 4U) & 0x0fU]);
        result.push_back(digits[byte & 0x0fU]);
    }
    return result;
}

SourceRevisionId source_revision_id_for(
    const ResourceRevision& revision
) {
    if(!revision.body_digest) {
        throw std::invalid_argument("Raw Markdown import has no body digest");
    }
    std::string material = revision.resource_id.value();
    material.push_back('\0');
    material += std::to_string(revision.generation);
    material.push_back('\0');
    material.append(
        reinterpret_cast<const char*>(revision.body_digest->bytes.data()),
        revision.body_digest->bytes.size());
    const auto fingerprint = hex_bytes(sha256(material));
    // Keep the opaque catalog key bounded. The complete SHA-256 remains in
    // ResourceRevisionRef and Artifact/BlobDigest; this ID is only a stable
    // source-domain join label over the same identity material.
    return SourceRevisionId(std::string{"rm:"} + fingerprint.substr(0, 32));
}

ArtifactBindingId artifact_binding_id_for(const SourceRevisionId& source) {
    return ArtifactBindingId("rmb:" + source.value().substr(3));
}

std::string publication_schema_id(std::string prefix) {
    if(prefix.empty()) prefix = "agent_memory";
    for(auto& character : prefix) {
        if(!std::isalnum(static_cast<unsigned char>(character))) character = '_';
    }
    return "agent_memory.m0_resource_importer.raw_chunks_original@" + prefix;
}

BlobDigest blob_digest_for(const ResourceBodyDigest& digest) {
    BlobDigest result;
    result.algorithm = BlobDigestAlgorithm::Sha256;
    result.value = digest.bytes;
    return result;
}

bool same_digest(const ResourceBodyDigest& left, const ResourceBodyDigest& right) {
    return left.algorithm == right.algorithm && left.bytes == right.bytes;
}

bool same_revision(const ResourceRevision& left, const ResourceRevision& right) {
    return left.resource_id == right.resource_id &&
        left.generation == right.generation &&
        left.pipeline_config_hash == right.pipeline_config_hash &&
        left.text_origin == right.text_origin &&
        left.body_digest.has_value() == right.body_digest.has_value() &&
        (!left.body_digest || same_digest(*left.body_digest, *right.body_digest));
}

} // namespace

class MdbxRawMarkdownProductIngestion::Impl final {
public:
    explicit Impl(RawMarkdownProductIngestionOptions options)
        : options_(std::move(options)),
          importer_(make_importer_options(options_)),
          catalog_(make_catalog_options(options_)),
          manifests_(make_manifest_options(options_)),
          joins_(make_join_options(options_)),
          tokenizer_(),
          hydrator_(make_hydration_options(options_)) {
        if(!options_.context) {
            throw std::invalid_argument("Raw Markdown ingestion requires a shared MDBX context");
        }
        if(options_.raw_cas_root.empty()) {
            throw std::invalid_argument("Raw Markdown ingestion requires a File CAS root");
        }
    }

    RawMarkdownProductImportResult import(ObservedResource resource, std::uint64_t bound_at_ms) {
        RawMarkdownProductImportResult result;
        if(!is_markdown_uri(resource.source_uri)) {
            result.status = RawMarkdownProductImportStatus::InvalidArgument;
            result.diagnostic = "only .md and .markdown source URIs are supported";
            return result;
        }
        if(resource.text_origin != SourceTextOrigin::OriginalText ||
           resource.derived_text_provenance) {
            result.status = RawMarkdownProductImportStatus::InvalidArgument;
            result.diagnostic = "Raw Markdown product ingestion admits original text only";
            return result;
        }

        try {
            ResourceImportResult imported;
            try {
                imported = importer_.import(resource);
            } catch(const std::invalid_argument& error) {
                result.status = RawMarkdownProductImportStatus::InvalidArgument;
                result.diagnostic = std::string{"resource importer: "} + error.what();
                return result;
            } catch(const std::exception& error) {
                result.status = RawMarkdownProductImportStatus::Incomplete;
                result.diagnostic = std::string{"resource importer: "} + error.what();
                return result;
            }
            if(!imported.revision.body_digest) {
                result.status = RawMarkdownProductImportStatus::Incomplete;
                result.diagnostic = "importer did not expose a body digest";
                result.resource = imported;
                return result;
            }
            const auto source_revision = source_revision_id_for(imported.revision);
            const auto binding = artifact_binding_id_for(source_revision);
            result.resource = imported;
            result.source_revision_id = source_revision;
            result.artifact_binding_id = binding;

            ArtifactCatalogResult retained;
            try {
                retained = catalog_.ingest(ArtifactCatalogIngestRequest{
                    source_revision,
                    binding,
                    "source-original",
                    ArtifactRetentionClass::SourceOriginal,
                    std::string{"text/markdown"},
                    std::string{"raw-markdown.md"},
                    bound_at_ms,
                    resource.utf8_body
                });
            } catch(const std::invalid_argument& error) {
                result.status = RawMarkdownProductImportStatus::InvalidArgument;
                result.diagnostic = std::string{"artifact catalog: "} + error.what();
                return result;
            } catch(const std::exception& error) {
                result.status = RawMarkdownProductImportStatus::Incomplete;
                result.diagnostic = std::string{"artifact catalog: "} + error.what();
                return result;
            }
            if(retained.status != ArtifactCatalogStatus::Ok || !retained.record) {
                result.status = retained.status == ArtifactCatalogStatus::Conflict
                    ? RawMarkdownProductImportStatus::Conflict
                    : retained.status == ArtifactCatalogStatus::InvalidArgument
                        ? RawMarkdownProductImportStatus::InvalidArgument
                        : RawMarkdownProductImportStatus::Incomplete;
                result.diagnostic = retained.diagnostic.empty()
                    ? "artifact catalog publication is incomplete"
                    : retained.diagnostic;
                return result;
            }
            const auto expected_digest = blob_digest_for(*imported.revision.body_digest);
            if(retained.record->artifact.digest.value != expected_digest.value ||
               retained.record->artifact.digest.algorithm != expected_digest.algorithm) {
                result.status = RawMarkdownProductImportStatus::Conflict;
                result.diagnostic = "resource and artifact authorities disagree on digest";
                return result;
            }
            result.artifact = retained.record;
            const ResourceArtifactBinding typed_join{
                ResourceRevisionRef{
                    imported.resource_id,
                    imported.revision.generation,
                    *imported.revision.body_digest
                },
                source_revision,
                binding
            };
            if(options_.before_join_publication_hook_for_testing) {
                options_.before_join_publication_hook_for_testing();
            }
            try {
                joins_.put(typed_join);
            } catch(const std::invalid_argument& error) {
                result.status = RawMarkdownProductImportStatus::InvalidArgument;
                result.diagnostic = std::string{"resource/artifact join: "} + error.what();
                return result;
            } catch(const std::exception& error) {
                result.status = RawMarkdownProductImportStatus::Conflict;
                result.diagnostic = std::string{"resource/artifact join: "} + error.what();
                return result;
            }
            result.resource_artifact_binding = typed_join;
            if(options_.before_final_observation_hook_for_testing) {
                options_.before_final_observation_hook_for_testing();
            }
            std::string observation_diagnostic;
            if(!observe_current_publication(
                imported, source_revision, binding, typed_join, *retained.record,
                observation_diagnostic)) {
                result.status = RawMarkdownProductImportStatus::Conflict;
                result.diagnostic = std::move(observation_diagnostic);
                return result;
            }
            result.status = RawMarkdownProductImportStatus::Published;
            return result;
        } catch(const std::invalid_argument& error) {
            result.status = RawMarkdownProductImportStatus::InvalidArgument;
            result.diagnostic = error.what();
            return result;
        } catch(const std::runtime_error& error) {
            result.status = RawMarkdownProductImportStatus::Incomplete;
            result.diagnostic = error.what();
            return result;
        }
    }

    std::vector<RawMarkdownProductHit> retrieve_and_hydrate(
        const ScopeId& scope_id,
        const RetrievalQuery& query,
        std::uint64_t max_bytes,
        std::size_t limit
    ) const {
        if(scope_id.empty()) {
            throw std::invalid_argument("Raw Markdown retrieval requires a non-empty scope");
        }
        MdbxSnapshotRevisionBoundRetriever retriever(
            options_.context,
            scope_id,
            tokenizer_,
            make_retriever_options(options_),
            options_.table_prefix);
        const auto hits = retriever.retrieve(query);
        const auto count = limit == 0 ? hits.size() : std::min(limit, hits.size());
        std::vector<RawMarkdownProductHit> result;
        result.reserve(count);
        for(std::size_t i = 0; i < count; ++i) {
            RawMarkdownProductHit item;
            item.candidate = hits[i];
            MdbxAuthoritativeArticleHydrationRequest request;
            request.candidate = ProjectionAwareLexicalCandidate{
                hits[i].knowledge_unit_id,
                hits[i].unit_revision,
                hits[i].projection_kind,
                hits[i].score
            };
            request.scope_id = hits[i].scope_id;
            if(hits[i].source) {
                request.source_range = hits[i].source->excerpt;
                request.claimed_resource_revision = hits[i].source->resource_revision;
            }
            request.max_bytes = max_bytes;
            item.hydration = hydrator_.hydrate(request);
            result.push_back(std::move(item));
        }
        return result;
    }

private:
    static void require_options(const RawMarkdownProductIngestionOptions& options) {
        if(!options.context) {
            throw std::invalid_argument("Raw Markdown ingestion requires a shared MDBX context");
        }
        if(options.raw_cas_root.empty()) {
            throw std::invalid_argument("Raw Markdown ingestion requires a File CAS root");
        }
    }

    static MdbxResourceImporterOptions make_importer_options(
        const RawMarkdownProductIngestionOptions& options
    ) {
        require_options(options);
        MdbxResourceImporterOptions result;
        result.context = options.context;
        result.table_prefix = options.table_prefix;
        result.max_dbs = options.max_dbs;
        result.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        result.max_raw_body_bytes = options.max_full_body_bytes;
        result.ownership_lease_ms = options.importer_ownership_lease_ms;
        result.clock_ms = options.importer_clock_ms;
        result.fault_injector = options.importer_fault_injector;
        return result;
    }

    static MdbxArtifactCatalogOptions make_catalog_options(
        const RawMarkdownProductIngestionOptions& options
    ) {
        require_options(options);
        MdbxArtifactCatalogOptions result;
        result.context = options.context;
        result.table_prefix = options.table_prefix;
        result.max_dbs = options.max_dbs;
        result.raw_cas_root = options.raw_cas_root;
        result.raw_cas_options.max_read_range_bytes = options.max_range_bytes;
        result.max_full_body_bytes = options.max_full_body_bytes;
        result.fault_injector = options.catalog_fault_injector;
        return result;
    }

    bool observe_current_publication(
        const ResourceImportResult& imported,
        const SourceRevisionId& source_revision,
        const ArtifactBindingId& binding,
        const ResourceArtifactBinding& expected_join,
        const ArtifactCatalogRecord& expected_catalog,
        std::string& diagnostic
    ) const {
        auto transaction = options_.context->connection()->transaction(
            mdbxc::TransactionMode::READ_ONLY);
        const auto manifest = manifests_.find_manifest_in_transaction(
            imported.resource_id, transaction);
        if(!manifest || manifest->state != ResourceManifestState::Active) {
            diagnostic = "resource manifest is not active at product publication linearization";
            return false;
        }
        if(!same_revision(manifest->revision, imported.revision) ||
           manifest->schema.schema_id != publication_schema_id(options_.table_prefix) ||
           manifest->schema.schema_version != 1U) {
            diagnostic = "resource revision was superseded or has an unexpected publication profile";
            return false;
        }
        std::set<std::pair<std::string, std::uint32_t>> bindings;
        for(const auto& record : manifest->records) {
            if(record.kind == DerivedRecordKind::Custom) {
                bindings.emplace(record.key, record.ordinal);
            }
        }
        for(std::size_t ordinal = 0; ordinal < imported.raw_units.size(); ++ordinal) {
            const auto id = std::to_string(imported.raw_units[ordinal].value());
            const auto index = static_cast<std::uint32_t>(ordinal);
            if(bindings.find({"knowledge_unit:" + id, index}) == bindings.end() ||
               bindings.find({"original_projection:" + id, index}) == bindings.end()) {
                diagnostic = "resource manifest is missing the expected Original publication records";
                return false;
            }
        }
        const auto stored_join = joins_.find_in_transaction(
            expected_join.resource_revision, transaction);
        if(!stored_join || !(*stored_join == expected_join)) {
            diagnostic = "typed resource/artifact join is missing or inconsistent";
            return false;
        }
        const auto catalog = catalog_.read_binding_in_transaction(binding, transaction);
        if(catalog.status != ArtifactCatalogStatus::Ok || !catalog.record) {
            diagnostic = "artifact catalog receipt is not finalized at product publication linearization";
            return false;
        }
        const auto& record = *catalog.record;
        const auto expected_digest = blob_digest_for(*imported.revision.body_digest);
        if(!record.receipt_finalized ||
           record.body_state != BindingBodyState::Materialized ||
           record.binding.id != binding ||
           record.binding.source_revision_id != source_revision ||
           record.binding.artifact_id != record.artifact.id ||
           record.artifact.id != expected_catalog.artifact.id ||
           record.artifact.size_bytes != expected_catalog.artifact.size_bytes ||
           record.binding.role != expected_catalog.binding.role ||
           record.binding.retention != expected_catalog.binding.retention ||
           record.binding.media_type != expected_catalog.binding.media_type ||
           record.binding.original_name != expected_catalog.binding.original_name ||
           record.binding.bound_at_ms != expected_catalog.binding.bound_at_ms ||
           record.artifact.digest.algorithm != expected_digest.algorithm ||
           record.artifact.digest.value != expected_digest.value) {
            diagnostic = "artifact catalog metadata is incomplete or inconsistent";
            return false;
        }
        transaction.commit();
        return true;
    }

    static MdbxResourceManifestStorageOptions make_manifest_options(
        const RawMarkdownProductIngestionOptions& options
    ) {
        require_options(options);
        MdbxResourceManifestStorageOptions result;
        result.context = options.context;
        result.table_prefix = options.table_prefix;
        result.max_dbs = options.max_dbs;
        return result;
    }

    static MdbxResourceArtifactBindingStoreOptions make_join_options(
        const RawMarkdownProductIngestionOptions& options
    ) {
        require_options(options);
        MdbxResourceArtifactBindingStoreOptions result;
        result.context = options.context;
        result.table_prefix = options.table_prefix;
        result.max_dbs = options.max_dbs;
        return result;
    }

    static ProjectionAwareLexicalRetrieverOptions make_retriever_options(
        const RawMarkdownProductIngestionOptions& options
    ) {
        require_options(options);
        ProjectionAwareLexicalRetrieverOptions result;
        result.max_candidates = options.max_candidates;
        result.max_scanned_units = options.max_scanned_units;
        result.max_indexed_projection_bytes = options.max_indexed_projection_bytes;
        result.expected_manifest_schema_id = publication_schema_id(options.table_prefix);
        return result;
    }

    static MdbxAuthoritativeArticleHydrationOptions make_hydration_options(
        const RawMarkdownProductIngestionOptions& options
    ) {
        require_options(options);
        MdbxAuthoritativeArticleHydrationOptions result;
        result.context = options.context;
        result.table_prefix = options.table_prefix;
        result.max_dbs = options.max_dbs;
        result.raw_cas_root = options.raw_cas_root;
        result.raw_cas_options.max_read_range_bytes = options.max_range_bytes;
        result.max_range_bytes = options.max_range_bytes;
        result.max_verified_cas_bytes = options.max_verified_cas_bytes;
        result.expected_manifest_schema_id = publication_schema_id(options.table_prefix);
        return result;
    }

    RawMarkdownProductIngestionOptions options_;
    MdbxResourceImporter importer_;
    MdbxArtifactCatalog catalog_;
    MdbxResourceManifestStorage manifests_;
    MdbxResourceArtifactBindingStore joins_;
    mutable StandardTokenizer tokenizer_;
    MdbxAuthoritativeArticleHydrator hydrator_;
};

MdbxRawMarkdownProductIngestion::MdbxRawMarkdownProductIngestion(
    RawMarkdownProductIngestionOptions options
) : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxRawMarkdownProductIngestion::~MdbxRawMarkdownProductIngestion() = default;

RawMarkdownProductImportResult MdbxRawMarkdownProductIngestion::import(
    ObservedResource resource,
    std::uint64_t bound_at_ms
) {
    return m_impl->import(std::move(resource), bound_at_ms);
}

std::vector<RawMarkdownProductHit>
MdbxRawMarkdownProductIngestion::retrieve_and_hydrate(
    const ScopeId& scope_id,
    const RetrievalQuery& query,
    std::uint64_t max_bytes,
    std::size_t limit
) const {
    return m_impl->retrieve_and_hydrate(scope_id, query, max_bytes, limit);
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
