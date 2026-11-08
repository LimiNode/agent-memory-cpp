#include "MdbxAuthoritativeArticleHydration.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxArtifactCatalog.hpp"
#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxProjectionStore.hpp"
#include "MdbxResourceArtifactBindingStore.hpp"
#include "MdbxResourceManifestStorage.hpp"
#include "MdbxStorageErrors.hpp"
#include "MdbxVerifiedArtifactHydration.hpp"

#include <mdbx_containers/common.hpp>

#include <algorithm>
#include <string_view>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

constexpr const char* kSourceOriginalRole = "source-original";
constexpr const char* kMarkdownMediaType = "text/markdown";
constexpr const char* kMarkdownMediaTypeAlt = "text/x-markdown";

bool is_valid_utf8(std::string_view bytes) noexcept {
    std::size_t position = 0;
    while(position < bytes.size()) {
        const auto lead = static_cast<unsigned char>(bytes[position++]);
        std::size_t continuation = 0;
        std::uint32_t code_point = 0;
        if(lead <= 0x7fU) {
            continue;
        } else if(lead >= 0xc2U && lead <= 0xdfU) {
            continuation = 1;
            code_point = lead & 0x1fU;
        } else if(lead >= 0xe0U && lead <= 0xefU) {
            continuation = 2;
            code_point = lead & 0x0fU;
        } else if(lead >= 0xf0U && lead <= 0xf4U) {
            continuation = 3;
            code_point = lead & 0x07U;
        } else {
            return false;
        }
        if(continuation > bytes.size() - position) return false;
        for(std::size_t index = 0; index < continuation; ++index) {
            const auto byte = static_cast<unsigned char>(bytes[position++]);
            if((byte & 0xc0U) != 0x80U) return false;
            code_point = (code_point << 6U) | (byte & 0x3fU);
        }
        if((continuation == 1 && code_point < 0x80U) ||
           (continuation == 2 && code_point < 0x800U) ||
           (continuation == 3 && code_point < 0x10000U) ||
           code_point > 0x10ffffU ||
           (code_point >= 0xd800U && code_point <= 0xdfffU)) {
            return false;
        }
    }
    return true;
}

bool is_supported_markdown_media_type(const std::optional<std::string>& value) {
    return value && (*value == kMarkdownMediaType || *value == kMarkdownMediaTypeAlt);
}

struct AuthoritativeMetadata final {
    KnowledgeUnitEnvelope unit;
    SearchProjection projection;
    ResourceManifest manifest;
    SourceRefSummary source;
    ResourceArtifactBinding join;
    ArtifactCatalogRecord catalog;
};

struct MetadataCheck final {
    AuthoritativeArticleHydrationStatus status =
        AuthoritativeArticleHydrationStatus::Unavailable;
    AuthoritativeArticleHydrationDiagnostic code =
        AuthoritativeArticleHydrationDiagnostic::IoFailure;
    std::string diagnostic;
    std::optional<AuthoritativeMetadata> metadata;
};

MetadataCheck failure(
    AuthoritativeArticleHydrationStatus status,
    AuthoritativeArticleHydrationDiagnostic code,
    std::string diagnostic) {
    return MetadataCheck{status, code, std::move(diagnostic), std::nullopt};
}

bool same_digest(
    const ResourceBodyDigest& left,
    const ResourceBodyDigest& right) noexcept {
    return left.algorithm == right.algorithm && left.bytes == right.bytes;
}

bool same_revision(
    const ResourceRevisionRef& left,
    const ResourceRevisionRef& right) noexcept {
    return left.resource_id == right.resource_id &&
           left.generation == right.generation &&
           same_digest(left.body_digest, right.body_digest);
}

bool same_projection(
    const SearchProjection& left,
    const SearchProjection& right) noexcept {
    return left.scope_id == right.scope_id &&
           left.unit_id == right.unit_id &&
           left.kind == right.kind &&
           left.version.unit_revision_at_build == right.version.unit_revision_at_build &&
           left.version.projection_revision == right.version.projection_revision &&
           left.version.derivation_generation == right.version.derivation_generation &&
           left.version.derivation_fingerprint == right.version.derivation_fingerprint &&
           left.title == right.title && left.heading == right.heading &&
           left.body == right.body && left.code == right.code &&
           left.tags == right.tags && left.symbols == right.symbols;
}

bool same_manifest(
    const ResourceManifest& left,
    const ResourceManifest& right) noexcept {
    const auto same_revision_value = [](const ResourceRevision& a,
                                        const ResourceRevision& b) noexcept {
        if(a.resource_id != b.resource_id || a.generation != b.generation ||
           a.content_hash != b.content_hash ||
           a.pipeline_config_hash != b.pipeline_config_hash ||
           a.text_origin != b.text_origin ||
           a.body_digest.has_value() != b.body_digest.has_value()) {
            return false;
        }
        if(a.body_digest && !same_digest(*a.body_digest, *b.body_digest)) {
            return false;
        }
        if(a.derived_text_provenance.has_value() !=
           b.derived_text_provenance.has_value()) {
            return false;
        }
        if(a.derived_text_provenance &&
           (a.derived_text_provenance->source_media_type !=
                b.derived_text_provenance->source_media_type ||
            a.derived_text_provenance->extractor_id !=
                b.derived_text_provenance->extractor_id ||
            a.derived_text_provenance->extractor_version !=
                b.derived_text_provenance->extractor_version)) {
            return false;
        }
        return true;
    };
    const auto same_records = [](const auto& a, const auto& b) noexcept {
        if(a.size() != b.size()) return false;
        for(std::size_t index = 0; index < a.size(); ++index) {
            if(a[index].kind != b[index].kind ||
               a[index].chunk_id != b[index].chunk_id ||
               a[index].key != b[index].key ||
               a[index].ordinal != b[index].ordinal) {
                return false;
            }
        }
        return true;
    };
    if(left.state != right.state || left.payload_version != right.payload_version ||
       left.schema.schema_id != right.schema.schema_id ||
       left.schema.schema_version != right.schema.schema_version ||
       !same_revision_value(left.revision, right.revision) ||
       !same_records(left.records, right.records) ||
       !same_records(left.pending_reclaim_records,
                     right.pending_reclaim_records)) {
        return false;
    }
    return true;
}

bool same_source(
    const SourceRefSummary& left,
    const SourceRefSummary& right) noexcept {
    return left.resource_id == right.resource_id &&
           left.reference_mode == right.reference_mode &&
           left.resource_revision.has_value() == right.resource_revision.has_value() &&
           (!left.resource_revision ||
            same_revision(*left.resource_revision, *right.resource_revision)) &&
           left.uri == right.uri && left.excerpt.offset == right.excerpt.offset &&
           left.excerpt.length == right.excerpt.length &&
           left.quote_hash == right.quote_hash && left.preview == right.preview;
}

bool same_catalog(
    const ArtifactCatalogRecord& left,
    const ArtifactCatalogRecord& right) noexcept {
    return left.artifact.id == right.artifact.id &&
           left.artifact.digest.algorithm == right.artifact.digest.algorithm &&
           left.artifact.digest.value == right.artifact.digest.value &&
           left.artifact.size_bytes == right.artifact.size_bytes &&
           left.binding.id == right.binding.id &&
           left.binding.source_revision_id == right.binding.source_revision_id &&
           left.binding.artifact_id == right.binding.artifact_id &&
           left.binding.role == right.binding.role &&
           left.binding.retention == right.binding.retention &&
           left.binding.media_type == right.binding.media_type &&
           left.binding.original_name == right.binding.original_name &&
           left.binding.bound_at_ms == right.binding.bound_at_ms &&
           left.body_state == right.body_state &&
           left.receipt_finalized == right.receipt_finalized;
}

bool same_metadata(
    const AuthoritativeMetadata& left,
    const AuthoritativeMetadata& right) noexcept {
    const auto same_optional_id = [](const auto& a, const auto& b) noexcept {
        return a.has_value() == b.has_value() &&
               (!a || a->value() == b->value());
    };
    return left.unit.id == right.unit.id &&
           left.unit.kind == right.unit.kind &&
           left.unit.scope_id == right.unit.scope_id &&
           left.unit.primary_text == right.unit.primary_text &&
           left.unit.display_text == right.unit.display_text &&
           left.unit.lifecycle_state == right.unit.lifecycle_state &&
           left.unit.created_at_ms == right.unit.created_at_ms &&
           left.unit.updated_at_ms == right.unit.updated_at_ms &&
           left.unit.observed_at_ms == right.unit.observed_at_ms &&
           left.unit.revision == right.unit.revision &&
           left.unit.content_hash.bytes == right.unit.content_hash.bytes &&
           left.unit.content_hash_recipe_version ==
               right.unit.content_hash_recipe_version &&
           left.unit.priority_weight == right.unit.priority_weight &&
           left.unit.supersedes == right.unit.supersedes &&
           same_optional_id(left.unit.superseded_by, right.unit.superseded_by) &&
           same_optional_id(left.unit.derived_from, right.unit.derived_from) &&
           left.unit.sources.size() == right.unit.sources.size() &&
           left.unit.sources.size() == 1U &&
           same_source(left.unit.sources.front(), right.unit.sources.front()) &&
           same_projection(left.projection, right.projection) &&
           same_manifest(left.manifest, right.manifest) &&
           same_source(left.source, right.source) &&
           left.join == right.join && same_catalog(left.catalog, right.catalog);
}

MdbxAuthoritativeArticleHydrationResult result_from_request(
    const MdbxAuthoritativeArticleHydrationRequest& request,
    AuthoritativeArticleHydrationStatus status,
    AuthoritativeArticleHydrationDiagnostic code,
    std::string diagnostic) {
    MdbxAuthoritativeArticleHydrationResult result;
    result.status = status;
    result.diagnostic_code = code;
    result.diagnostic = std::move(diagnostic);
    result.knowledge_unit_id = request.candidate.knowledge_unit_id;
    result.scope_id = request.scope_id;
    result.unit_revision = request.candidate.unit_revision;
    result.source_range = request.source_range;
    return result;
}

MetadataCheck read_metadata(
    const MdbxAuthoritativeArticleHydrationRequest& request,
    const std::uint64_t max_range_bytes,
    MdbxKnowledgeUnitStore& units,
    MdbxProjectionStore& projections,
    MdbxResourceManifestStorage& manifests,
    MdbxResourceArtifactBindingStore& joins,
    MdbxArtifactCatalog& catalog,
    const mdbxc::Transaction& transaction,
    const ProjectionAwareLexicalRetrieverOptions& admission_options) {
    const auto unit = units.find_in_transaction(
        request.candidate.knowledge_unit_id, transaction);
    if(!unit) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::UnitMissing,
                       "knowledge unit is no longer present");
    }
    if(unit->scope_id != request.scope_id) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::ScopeMismatch,
                       "knowledge unit belongs to another scope");
    }
    if(unit->lifecycle_state != KnowledgeUnitLifecycleState::Active) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::UnitInactive,
                       "knowledge unit is not active");
    }
    if(unit->revision != request.candidate.unit_revision) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::UnitRevisionMismatch,
                       "candidate unit revision is no longer current");
    }
    if(unit->kind != KnowledgeUnitKind::Chunk) {
        return failure(AuthoritativeArticleHydrationStatus::Unsupported,
                       AuthoritativeArticleHydrationDiagnostic::ArtifactProfileUnsupported,
                       "only Chunk knowledge units are supported by the Raw Markdown profile");
    }

    const OriginalProjectionListReader read_projections =
        [&projections, &transaction](
            const ScopeId& scope,
            const KnowledgeUnitId& id,
            ProjectionKind kind) {
            return projections.list_in_transaction(scope, id, transaction, kind);
        };
    const OriginalManifestReader read_manifest =
        [&manifests, &transaction](const ResourceId& id) {
            return manifests.find_manifest_in_transaction(id, transaction);
        };
    if(!has_current_original_projection_with_readers(
            *unit, read_projections, read_manifest, admission_options)) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::ProjectionMissing,
                       "current Original projection is not authoritatively published");
    }
    const auto projections_in_view = projections.list_in_transaction(
        unit->scope_id, unit->id, transaction, ProjectionKind::Original);
    std::optional<SearchProjection> projection;
    for(const auto& value : projections_in_view) {
        if(value.version.unit_revision_at_build == unit->revision &&
           value.version.projection_revision == unit->revision &&
           value.version.derivation_generation == 1 &&
           value.body == unit->primary_text) {
            if(projection) {
                return failure(AuthoritativeArticleHydrationStatus::Corrupt,
                               AuthoritativeArticleHydrationDiagnostic::ProjectionMissing,
                               "multiple current Original projections are published");
            }
            projection = value;
        }
    }
    if(!projection) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::ProjectionMissing,
                       "current Original projection body was not found");
    }

    if(unit->sources.empty()) {
        return failure(AuthoritativeArticleHydrationStatus::SummaryOnly,
                       AuthoritativeArticleHydrationDiagnostic::LocatorUnsupported,
                       "the current Original projection has no source locator");
    }
    if(unit->sources.size() != 1U) {
        return failure(AuthoritativeArticleHydrationStatus::Unsupported,
                       AuthoritativeArticleHydrationDiagnostic::LocatorUnsupported,
                       "only one revision-bound Raw Markdown source is supported");
    }
    const auto source = unit->sources.front();
    if(!is_valid_source_ref_summary(source)) {
        return failure(AuthoritativeArticleHydrationStatus::Corrupt,
                       AuthoritativeArticleHydrationDiagnostic::LocatorUnsupported,
                       "the source locator is malformed");
    }
    if(source.reference_mode != SourceReferenceMode::RevisionBoundQuote ||
       !source.resource_revision) {
        return failure(AuthoritativeArticleHydrationStatus::SummaryOnly,
                       AuthoritativeArticleHydrationDiagnostic::LocatorUnsupported,
                       "only a summary is available; no revision-bound source locator exists");
    }
    if(source.text_origin != SourceTextOrigin::OriginalText) {
        return failure(AuthoritativeArticleHydrationStatus::Unsupported,
                       AuthoritativeArticleHydrationDiagnostic::LocatorUnsupported,
                       "derived source text is outside the Raw Markdown profile");
    }
    const auto revision = *source.resource_revision;
    if(request.source_range.offset != source.excerpt.offset ||
       request.source_range.length != source.excerpt.length) {
        return failure(AuthoritativeArticleHydrationStatus::InvalidArgument,
                       AuthoritativeArticleHydrationDiagnostic::LocatorMismatch,
                       "requested source range does not match authoritative evidence");
    }
    if(request.source_range.length == 0 ||
       request.source_range.length > max_range_bytes ||
       request.source_range.offset >
           std::numeric_limits<std::uint64_t>::max() - request.source_range.length) {
        return failure(AuthoritativeArticleHydrationStatus::InvalidArgument,
                       request.source_range.length > max_range_bytes
                           ? AuthoritativeArticleHydrationDiagnostic::BudgetExceeded
                           : AuthoritativeArticleHydrationDiagnostic::RangeOutOfBounds,
                       "requested source range exceeds the hydration budget or overflows");
    }

    const auto manifest = manifests.find_manifest_in_transaction(
        source.resource_id, transaction);
    if(!manifest) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::ManifestMissing,
                       "source resource manifest is missing");
    }
    if(manifest->state != ResourceManifestState::Active ||
       manifest->revision.text_origin != SourceTextOrigin::OriginalText ||
       manifest->revision.resource_id != revision.resource_id ||
       manifest->revision.generation != revision.generation ||
       !manifest->revision.body_digest ||
       !same_digest(*manifest->revision.body_digest, revision.body_digest)) {
        return failure(AuthoritativeArticleHydrationStatus::Stale,
                       AuthoritativeArticleHydrationDiagnostic::ManifestStale,
                       "source resource manifest is not current for the evidence revision");
    }

    const auto join = joins.find_in_transaction(revision, transaction);
    if(!join) {
        return failure(AuthoritativeArticleHydrationStatus::Missing,
                       AuthoritativeArticleHydrationDiagnostic::BindingMissing,
                       "resource revision has no artifact binding");
    }
    const auto catalog_result = catalog.read_binding_in_transaction(
        join->artifact_binding_id, transaction);
    if(catalog_result.status != ArtifactCatalogStatus::Ok ||
       !catalog_result.record) {
        switch(catalog_result.status) {
        case ArtifactCatalogStatus::NotFound:
            return failure(AuthoritativeArticleHydrationStatus::Missing,
                           AuthoritativeArticleHydrationDiagnostic::CatalogMissing,
                           catalog_result.diagnostic);
        case ArtifactCatalogStatus::InvalidArgument:
            return failure(AuthoritativeArticleHydrationStatus::InvalidArgument,
                           AuthoritativeArticleHydrationDiagnostic::InvalidRequest,
                           catalog_result.diagnostic);
        case ArtifactCatalogStatus::IoError:
            return failure(AuthoritativeArticleHydrationStatus::Unavailable,
                           AuthoritativeArticleHydrationDiagnostic::IoFailure,
                           catalog_result.diagnostic);
        case ArtifactCatalogStatus::Corrupt:
        case ArtifactCatalogStatus::Conflict:
        case ArtifactCatalogStatus::Ok:
            return failure(AuthoritativeArticleHydrationStatus::Corrupt,
                           AuthoritativeArticleHydrationDiagnostic::CatalogCorrupt,
                           catalog_result.diagnostic);
        }
    }
    const auto& catalog_record = *catalog_result.record;
    if(catalog_record.binding.source_revision_id != join->source_revision_id ||
       catalog_record.binding.artifact_id != catalog_record.artifact.id ||
       catalog_record.body_state != BindingBodyState::Materialized) {
        return failure(AuthoritativeArticleHydrationStatus::Corrupt,
                       AuthoritativeArticleHydrationDiagnostic::BindingMismatch,
                       "artifact catalog binding is inconsistent with the typed join");
    }
    if(!catalog_record.receipt_finalized) {
        return failure(AuthoritativeArticleHydrationStatus::Unavailable,
                       AuthoritativeArticleHydrationDiagnostic::CatalogReceiptIncomplete,
                       "artifact catalog publication receipt is not finalized");
    }
    if(catalog_record.binding.role != kSourceOriginalRole ||
       !is_supported_markdown_media_type(catalog_record.binding.media_type)) {
        return failure(AuthoritativeArticleHydrationStatus::Unsupported,
                       AuthoritativeArticleHydrationDiagnostic::ArtifactProfileUnsupported,
                       "artifact binding is outside the supported Raw Markdown profile");
    }
    if(revision.body_digest.algorithm != ResourceBodyDigestAlgorithm::Sha256 ||
       catalog_record.artifact.digest.algorithm != BlobDigestAlgorithm::Sha256 ||
       revision.body_digest.bytes != catalog_record.artifact.digest.value) {
        return failure(AuthoritativeArticleHydrationStatus::Corrupt,
                       AuthoritativeArticleHydrationDiagnostic::BindingMismatch,
                       "resource and artifact digests disagree");
    }
    if(request.source_range.offset > catalog_record.artifact.size_bytes ||
       request.source_range.length >
           catalog_record.artifact.size_bytes - request.source_range.offset) {
        return failure(AuthoritativeArticleHydrationStatus::InvalidArgument,
                       AuthoritativeArticleHydrationDiagnostic::RangeOutOfBounds,
                       "source range is outside the retained Raw body");
    }
    if(request.claimed_resource_revision &&
       !same_revision(*request.claimed_resource_revision, revision)) {
        return failure(AuthoritativeArticleHydrationStatus::InvalidArgument,
                       AuthoritativeArticleHydrationDiagnostic::InvalidRequest,
                       "caller resource revision claim does not match authority");
    }
    if(request.claimed_artifact_digest &&
       (request.claimed_artifact_digest->algorithm != catalog_record.artifact.digest.algorithm ||
        request.claimed_artifact_digest->value != catalog_record.artifact.digest.value)) {
        return failure(AuthoritativeArticleHydrationStatus::InvalidArgument,
                       AuthoritativeArticleHydrationDiagnostic::InvalidRequest,
                       "caller artifact digest claim does not match authority");
    }

    return MetadataCheck{
        AuthoritativeArticleHydrationStatus::Hydrated,
        AuthoritativeArticleHydrationDiagnostic::None,
        {},
        AuthoritativeMetadata{
            *unit, *projection, *manifest, source, *join, catalog_record
        }
    };
}

} // namespace

class MdbxAuthoritativeArticleHydrator::Impl final {
public:
    explicit Impl(MdbxAuthoritativeArticleHydrationOptions options)
        : context(require_context(std::move(options.context))),
          table_prefix(options.table_prefix.empty()
                           ? "agent_memory"
                           : std::move(options.table_prefix)),
          max_range_bytes(options.max_range_bytes),
          max_verified_cas_bytes(options.max_verified_cas_bytes),
          admission_options(make_admission_options(
              options.expected_manifest_schema_id,
              options.expected_manifest_schema_version)),
          units(make_unit_options(context, table_prefix, options.max_dbs)),
          projections(make_projection_options(context, table_prefix, options.max_dbs)),
          manifests(make_manifest_options(context, table_prefix, options.max_dbs)),
          joins(make_join_options(context, table_prefix, options.max_dbs)),
          catalog(make_catalog_options(
              context, table_prefix, options.max_dbs,
              require_cas_root(std::move(options.raw_cas_root)),
              options.raw_cas_options)) {
        if(max_range_bytes == 0 || max_verified_cas_bytes == 0) throw std::invalid_argument(
            "authoritative article hydrator requires positive read budgets");
    }

    static std::shared_ptr<MdbxStorageContext> require_context(
        std::shared_ptr<MdbxStorageContext> value) {
        if(!value) throw std::invalid_argument(
            "authoritative article hydrator requires a storage context");
        return value;
    }

    static std::filesystem::path require_cas_root(
        std::filesystem::path value) {
        if(value.empty()) throw std::invalid_argument(
            "authoritative article hydrator requires a Raw CAS root");
        return value;
    }

    static ProjectionAwareLexicalRetrieverOptions make_admission_options(
        std::string schema_id, std::uint32_t schema_version) {
        ProjectionAwareLexicalRetrieverOptions value;
        value.expected_manifest_schema_id = std::move(schema_id);
        value.expected_manifest_schema_version = schema_version;
        if(value.expected_manifest_schema_id.empty() || schema_version == 0) {
            throw std::invalid_argument(
                "authoritative article hydrator requires a manifest schema");
        }
        return value;
    }

    static MdbxKnowledgeUnitStoreOptions make_unit_options(
        const std::shared_ptr<MdbxStorageContext>& context,
        const std::string& prefix,
        std::int64_t max_dbs) {
        MdbxKnowledgeUnitStoreOptions value;
        value.table_prefix = prefix;
        value.max_dbs = max_dbs;
        value.context = context;
        return value;
    }
    static MdbxProjectionStoreOptions make_projection_options(
        const std::shared_ptr<MdbxStorageContext>& context,
        const std::string& prefix,
        std::int64_t max_dbs) {
        MdbxProjectionStoreOptions value;
        value.table_prefix = prefix;
        value.max_dbs = max_dbs;
        value.context = context;
        return value;
    }
    static MdbxResourceManifestStorageOptions make_manifest_options(
        const std::shared_ptr<MdbxStorageContext>& context,
        const std::string& prefix,
        std::int64_t max_dbs) {
        MdbxResourceManifestStorageOptions value;
        value.table_prefix = prefix;
        value.max_dbs = max_dbs;
        value.context = context;
        return value;
    }
    static MdbxResourceArtifactBindingStoreOptions make_join_options(
        const std::shared_ptr<MdbxStorageContext>& context,
        const std::string& prefix,
        std::int64_t max_dbs) {
        MdbxResourceArtifactBindingStoreOptions value;
        value.table_prefix = prefix;
        value.max_dbs = max_dbs;
        value.context = context;
        return value;
    }
    static MdbxArtifactCatalogOptions make_catalog_options(
        const std::shared_ptr<MdbxStorageContext>& context,
        const std::string& prefix,
        std::int64_t max_dbs,
        std::filesystem::path cas_root,
        FileRawBlobStoreOptions cas_options) {
        MdbxArtifactCatalogOptions value;
        value.table_prefix = prefix;
        value.max_dbs = max_dbs;
        value.context = context;
        value.raw_cas_root = std::move(cas_root);
        value.raw_cas_options = cas_options;
        return value;
    }

    std::shared_ptr<MdbxStorageContext> context;
    std::string table_prefix;
    std::uint64_t max_range_bytes;
    std::uint64_t max_verified_cas_bytes;
    ProjectionAwareLexicalRetrieverOptions admission_options;
    MdbxKnowledgeUnitStore units;
    MdbxProjectionStore projections;
    MdbxResourceManifestStorage manifests;
    MdbxResourceArtifactBindingStore joins;
    MdbxArtifactCatalog catalog;
    mutable std::mutex hook_mutex;
    mutable std::function<void()> before_final_hook;
    mutable std::function<void()> after_final_hook;
};

MdbxAuthoritativeArticleHydrator::MdbxAuthoritativeArticleHydrator(
    MdbxAuthoritativeArticleHydrationOptions options
) : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxAuthoritativeArticleHydrator::~MdbxAuthoritativeArticleHydrator() = default;

void MdbxAuthoritativeArticleHydrator::set_before_final_revalidation_hook_for_testing(
    std::function<void()> hook) {
    std::lock_guard<std::mutex> lock(m_impl->hook_mutex);
    m_impl->before_final_hook = std::move(hook);
}

void MdbxAuthoritativeArticleHydrator::set_after_final_revalidation_hook_for_testing(
    std::function<void()> hook) {
    std::lock_guard<std::mutex> lock(m_impl->hook_mutex);
    m_impl->after_final_hook = std::move(hook);
}

MdbxAuthoritativeArticleHydrationResult
MdbxAuthoritativeArticleHydrator::hydrate(
    const MdbxAuthoritativeArticleHydrationRequest& request) const {
    if(request.scope_id.empty() || request.candidate.knowledge_unit_id.empty() ||
       request.candidate.unit_revision == 0 ||
       request.candidate.projection_kind != ProjectionKind::Original) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::InvalidArgument,
            AuthoritativeArticleHydrationDiagnostic::InvalidRequest,
            "article hydration request identity is incomplete or unsupported");
    }
    const auto budget = request.max_bytes == 0
        ? m_impl->max_range_bytes
        : request.max_bytes;
    if(request.max_bytes > m_impl->max_range_bytes) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::InvalidArgument,
            AuthoritativeArticleHydrationDiagnostic::BudgetExceeded,
            "request budget exceeds the configured hydrator budget");
    }
    if(request.source_range.length == 0 || request.source_range.length > budget ||
       request.source_range.offset >
           std::numeric_limits<std::uint64_t>::max() - request.source_range.length) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::InvalidArgument,
            request.source_range.length > budget
                ? AuthoritativeArticleHydrationDiagnostic::BudgetExceeded
                : AuthoritativeArticleHydrationDiagnostic::RangeOutOfBounds,
            "article hydration range is empty, oversized or overflowing");
    }

    MetadataCheck admitted;
    try {
        auto transaction = m_impl->context->connection()->transaction(
            mdbxc::TransactionMode::READ_ONLY);
        admitted = read_metadata(
            request, m_impl->max_range_bytes, m_impl->units, m_impl->projections,
            m_impl->manifests, m_impl->joins, m_impl->catalog, transaction,
            m_impl->admission_options);
        transaction.commit();
    } catch(const MdbxPersistedMetadataError& error) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Corrupt,
            AuthoritativeArticleHydrationDiagnostic::PersistedMetadataCorrupt,
            error.what());
    } catch(const std::exception& error) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Unavailable,
            AuthoritativeArticleHydrationDiagnostic::IoFailure,
            error.what());
    }
    if(!admitted.metadata) {
        return result_from_request(
            request, admitted.status, admitted.code, admitted.diagnostic);
    }

    const auto& metadata = *admitted.metadata;
    if(metadata.catalog.artifact.size_bytes > m_impl->max_verified_cas_bytes) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::InvalidArgument,
            AuthoritativeArticleHydrationDiagnostic::VerificationBudgetExceeded,
            "complete Raw CAS object exceeds the verification budget");
    }
    const auto materialized = MdbxArtifactMaterializer(m_impl->catalog).materialize(
        VerifiedArtifactMaterializationRequest{
            metadata.join.source_revision_id,
            metadata.join.artifact_binding_id,
            metadata.catalog.artifact.id,
            metadata.catalog.artifact.digest,
            request.source_range.offset,
            request.source_range.length
        });
    if(materialized.status != VerifiedArtifactMaterializationStatus::Materialized) {
        AuthoritativeArticleHydrationStatus status =
            AuthoritativeArticleHydrationStatus::Unavailable;
        AuthoritativeArticleHydrationDiagnostic code =
            AuthoritativeArticleHydrationDiagnostic::IoFailure;
        switch(materialized.status) {
        case VerifiedArtifactMaterializationStatus::Missing:
            status = AuthoritativeArticleHydrationStatus::Missing;
            code = AuthoritativeArticleHydrationDiagnostic::CasMissing;
            break;
        case VerifiedArtifactMaterializationStatus::Corrupt:
        case VerifiedArtifactMaterializationStatus::BindingRevisionMismatch:
            status = AuthoritativeArticleHydrationStatus::Corrupt;
            code = materialized.status == VerifiedArtifactMaterializationStatus::Corrupt
                ? AuthoritativeArticleHydrationDiagnostic::CasCorrupt
                : AuthoritativeArticleHydrationDiagnostic::BindingMismatch;
            break;
        case VerifiedArtifactMaterializationStatus::Unsupported:
            status = AuthoritativeArticleHydrationStatus::Unsupported;
            code = AuthoritativeArticleHydrationDiagnostic::LocatorUnsupported;
            break;
        case VerifiedArtifactMaterializationStatus::InvalidArgument:
            status = AuthoritativeArticleHydrationStatus::InvalidArgument;
            code = AuthoritativeArticleHydrationDiagnostic::InvalidRequest;
            break;
        case VerifiedArtifactMaterializationStatus::Unavailable:
            status = AuthoritativeArticleHydrationStatus::Unavailable;
            code = AuthoritativeArticleHydrationDiagnostic::IoFailure;
            break;
        case VerifiedArtifactMaterializationStatus::Materialized:
            break;
        }
        return result_from_request(request, status, code, materialized.diagnostic);
    }

    const auto& source = metadata.source;
    if(source.preview.size() > materialized.bytes.size() ||
       materialized.bytes.compare(0, source.preview.size(), source.preview) != 0 ||
       source.quote_hash != compute_quote_hash(
           std::string_view(materialized.bytes).substr(0, source.preview.size()))) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Corrupt,
            AuthoritativeArticleHydrationDiagnostic::SourceQuoteMismatch,
            "hydrated source span does not match the persisted citation preview");
    }
    if(!is_valid_utf8(materialized.bytes)) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Corrupt,
            AuthoritativeArticleHydrationDiagnostic::InvalidUtf8,
            "hydrated Raw Markdown span is not valid UTF-8");
    }

    {
        std::lock_guard<std::mutex> lock(m_impl->hook_mutex);
        if(m_impl->before_final_hook) m_impl->before_final_hook();
    }

    MetadataCheck final;
    try {
        auto transaction = m_impl->context->connection()->transaction(
            mdbxc::TransactionMode::READ_ONLY);
        final = read_metadata(
            request, m_impl->max_range_bytes, m_impl->units, m_impl->projections,
            m_impl->manifests, m_impl->joins, m_impl->catalog, transaction,
            m_impl->admission_options);
        transaction.commit();
    } catch(const MdbxPersistedMetadataError& error) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Corrupt,
            AuthoritativeArticleHydrationDiagnostic::PersistedMetadataCorrupt,
            error.what());
    } catch(const std::exception& error) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Unavailable,
            AuthoritativeArticleHydrationDiagnostic::IoFailure,
            error.what());
    }
    if(!final.metadata) {
        return result_from_request(
            request, final.status, final.code, final.diagnostic);
    }
    if(!same_metadata(metadata, *final.metadata)) {
        return result_from_request(
            request,
            AuthoritativeArticleHydrationStatus::Stale,
            AuthoritativeArticleHydrationDiagnostic::MetadataChanged,
            "authoritative metadata changed before hydration linearization");
    }

    MdbxAuthoritativeArticleHydrationResult result = result_from_request(
        request,
        AuthoritativeArticleHydrationStatus::Hydrated,
        AuthoritativeArticleHydrationDiagnostic::None,
        {});
    result.resource_revision = metadata.source.resource_revision;
    result.source_revision_id = metadata.join.source_revision_id;
    result.artifact_binding_id = metadata.join.artifact_binding_id;
    result.artifact_id = metadata.catalog.artifact.id;
    result.artifact_digest = metadata.catalog.artifact.digest;
    result.bytes = materialized.bytes;
    {
        std::lock_guard<std::mutex> lock(m_impl->hook_mutex);
        if(m_impl->after_final_hook) m_impl->after_final_hook();
    }
    return result;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
