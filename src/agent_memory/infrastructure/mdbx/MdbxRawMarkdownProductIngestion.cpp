#include "MdbxRawMarkdownProductIngestion.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxArtifactCatalog.hpp"
#include "MdbxResourceArtifactBindingStore.hpp"
#include "MdbxResourceImporter.hpp"

#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <cctype>
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

} // namespace

class MdbxRawMarkdownProductIngestion::Impl final {
public:
    explicit Impl(RawMarkdownProductIngestionOptions options)
        : options_(std::move(options)),
          importer_(make_importer_options(options_)),
          catalog_(make_catalog_options(options_)),
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
