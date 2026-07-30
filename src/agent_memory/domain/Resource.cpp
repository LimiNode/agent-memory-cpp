#include "Resource.hpp"

#include <array>
#include <cctype>
#include <string>

namespace agent_memory {

    namespace {

        struct DerivedRecordKindName final {
            DerivedRecordKind kind = DerivedRecordKind::Chunk;
            std::string_view name;
        };

        constexpr std::array<DerivedRecordKindName, 8> DERIVED_RECORD_KIND_NAMES{{
            {DerivedRecordKind::Document, "document"},
            {DerivedRecordKind::Chunk, "chunk"},
            {DerivedRecordKind::Embedding, "embedding"},
            {DerivedRecordKind::VectorRecord, "vector_record"},
            {DerivedRecordKind::BinaryBucketPosting, "binary_bucket_posting"},
            {DerivedRecordKind::LexicalPosting, "lexical_posting"},
            {DerivedRecordKind::GraphRecord, "graph_record"},
            {DerivedRecordKind::Custom, "custom"}
        }};

        std::string lowercase_ascii(std::string_view text) {
            std::string result;
            result.reserve(text.size());
            for(const unsigned char c : text) {
                result.push_back(static_cast<char>(std::tolower(c)));
            }
            return result;
        }

    } // namespace

    std::string_view to_string(DerivedRecordKind kind) noexcept {
        for(const auto& item : DERIVED_RECORD_KIND_NAMES) {
            if(item.kind == kind) {
                return item.name;
            }
        }
        return "custom";
    }

    bool parse_derived_record_kind(std::string_view text, DerivedRecordKind& kind) {
        const auto normalized = lowercase_ascii(text);
        for(const auto& item : DERIVED_RECORD_KIND_NAMES) {
            if(normalized == item.name) {
                kind = item.kind;
                return true;
            }
        }
        return false;
    }

    bool derived_record_kind_uses_chunk_id(DerivedRecordKind kind) noexcept {
        switch(kind) {
        case DerivedRecordKind::Chunk:
        case DerivedRecordKind::Embedding:
        case DerivedRecordKind::VectorRecord:
            return true;
        case DerivedRecordKind::Document:
        case DerivedRecordKind::BinaryBucketPosting:
        case DerivedRecordKind::LexicalPosting:
        case DerivedRecordKind::GraphRecord:
        case DerivedRecordKind::Custom:
            return false;
        }
        return false;
    }

    bool derived_record_kind_uses_key(DerivedRecordKind kind) noexcept {
        switch(kind) {
        case DerivedRecordKind::Document:
        case DerivedRecordKind::BinaryBucketPosting:
        case DerivedRecordKind::LexicalPosting:
        case DerivedRecordKind::GraphRecord:
        case DerivedRecordKind::Custom:
            return true;
        case DerivedRecordKind::Chunk:
        case DerivedRecordKind::Embedding:
        case DerivedRecordKind::VectorRecord:
            return false;
        }
        return false;
    }

    bool has_required_reference(const DerivedRecordRef& ref) noexcept {
        if(derived_record_kind_uses_chunk_id(ref.kind)) {
            return !ref.chunk_id.empty();
        }

        if(derived_record_kind_uses_key(ref.kind)) {
            return !ref.key.empty();
        }

        return false;
    }

    bool is_valid_derived_record_ref(const DerivedRecordRef& ref) noexcept {
        if(derived_record_kind_uses_chunk_id(ref.kind)) {
            return !ref.chunk_id.empty() && ref.key.empty();
        }

        if(derived_record_kind_uses_key(ref.kind)) {
            return ref.chunk_id.empty() && !ref.key.empty();
        }

        return false;
    }

    bool has_same_derived_record_identity(
        const DerivedRecordRef& left,
        const DerivedRecordRef& right
    ) noexcept {
        if(left.kind != right.kind) {
            return false;
        }

        if(derived_record_kind_uses_chunk_id(left.kind)) {
            return left.chunk_id == right.chunk_id;
        }

        if(derived_record_kind_uses_key(left.kind)) {
            return left.key == right.key;
        }

        return false;
    }

    bool is_valid_resource_body_digest(const ResourceBodyDigest& digest) noexcept {
        switch(digest.algorithm) {
        case ResourceBodyDigestAlgorithm::Sha256:
            return true;
        }
        return false;
    }

    bool is_valid_resource_manifest(const ResourceManifest& manifest) noexcept {
        if(manifest.revision.resource_id.empty()) {
            return false;
        }

        if(
            manifest.state != ResourceManifestState::Active
            && manifest.state != ResourceManifestState::ErasePending
        ) {
            return false;
        }

        if(
            manifest.revision.body_digest &&
            !is_valid_resource_body_digest(*manifest.revision.body_digest)
        ) {
            return false;
        }

        if(
            (manifest.schema.schema_id.empty() && manifest.schema.schema_version != 0) ||
            (!manifest.schema.schema_id.empty() && manifest.schema.schema_version == 0)
        ) {
            return false;
        }

        for(const auto& record : manifest.records) {
            if(!is_valid_derived_record_ref(record)) {
                return false;
            }
        }

        for(const auto& record : manifest.pending_reclaim_records) {
            if(!is_valid_derived_record_ref(record)) {
                return false;
            }
        }

        const auto contains_duplicate_identity = [](const std::vector<DerivedRecordRef>& records) {
            for(std::size_t left = 0; left < records.size(); ++left) {
                for(std::size_t right = left + 1; right < records.size(); ++right) {
                    if(has_same_derived_record_identity(records[left], records[right])) {
                        return true;
                    }
                }
            }
            return false;
        };

        if(
            contains_duplicate_identity(manifest.records) ||
            contains_duplicate_identity(manifest.pending_reclaim_records)
        ) {
            return false;
        }

        for(const auto& active_record : manifest.records) {
            for(const auto& reclaim_record : manifest.pending_reclaim_records) {
                if(has_same_derived_record_identity(active_record, reclaim_record)) {
                    return false;
                }
            }
        }

        return true;
    }

    bool is_active_resource_manifest(const ResourceManifest& manifest) noexcept {
        return manifest.state == ResourceManifestState::Active;
    }

    bool matches_revision_hashes(
        const ResourceRevision& revision,
        std::uint64_t content_hash,
        std::uint64_t pipeline_config_hash
    ) noexcept {
        return revision.content_hash == content_hash &&
            revision.pipeline_config_hash == pipeline_config_hash;
    }

} // namespace agent_memory
