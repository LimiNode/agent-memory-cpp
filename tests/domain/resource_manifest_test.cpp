#include <agent_memory.hpp>

#include <iostream>
#include <string_view>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

} // namespace

int main() {
    const agent_memory::ResourceId resource_id{"resource:readme"};
    const agent_memory::ResourceId same_resource_id{"resource:readme"};
    const agent_memory::ResourceId other_resource_id{"resource:notes"};

    if(resource_id.empty()) {
        return fail("resource id must not be empty");
    }

    if(resource_id != same_resource_id) {
        return fail("matching resource ids must compare equal");
    }

    if(!(other_resource_id < resource_id)) {
        return fail("resource ids must provide deterministic ordering");
    }

    const agent_memory::ResourceRevision revision{
        resource_id,
        7,
        0xAABBCCDDU,
        0x11223344U
    };

    if(!agent_memory::matches_revision_hashes(revision, 0xAABBCCDDU, 0x11223344U)) {
        return fail("revision must match content and pipeline hashes together");
    }

    if(agent_memory::matches_revision_hashes(revision, 0xAABBCCDDU, 0x55667788U)) {
        return fail("revision must reject changed pipeline hash");
    }

    agent_memory::DerivedRecordKind parsed_kind = agent_memory::DerivedRecordKind::Custom;
    if(!agent_memory::parse_derived_record_kind("Binary_Bucket_Posting", parsed_kind)) {
        return fail("derived record kind parser must accept mixed-case input");
    }

    if(parsed_kind != agent_memory::DerivedRecordKind::BinaryBucketPosting) {
        return fail("derived record kind parser returned unexpected value");
    }

    if(agent_memory::to_string(parsed_kind) != "binary_bucket_posting") {
        return fail("derived record kind names must be stable lowercase strings");
    }

    if(agent_memory::parse_derived_record_kind("unknown_kind", parsed_kind)) {
        return fail("unknown derived record kind must not parse");
    }

    if(!agent_memory::derived_record_kind_uses_chunk_id(
        agent_memory::DerivedRecordKind::VectorRecord
    )) {
        return fail("vector records must use chunk id references");
    }

    if(!agent_memory::derived_record_kind_uses_key(
        agent_memory::DerivedRecordKind::GraphRecord
    )) {
        return fail("graph records must use key references");
    }

    const agent_memory::DerivedRecordRef chunk_ref{
        agent_memory::DerivedRecordKind::Chunk,
        agent_memory::ChunkId{"chunk:readme:0"},
        {},
        0
    };

    if(!agent_memory::has_required_reference(chunk_ref)) {
        return fail("chunk derived record ref must be valid with chunk id");
    }

    if(!agent_memory::is_valid_derived_record_ref(chunk_ref)) {
        return fail("chunk derived record ref must pass strict validation");
    }

    const agent_memory::DerivedRecordRef invalid_chunk_ref{
        agent_memory::DerivedRecordKind::Chunk,
        {},
        "ignored",
        0
    };

    if(agent_memory::has_required_reference(invalid_chunk_ref)) {
        return fail("chunk derived record ref must require a chunk id");
    }

    const agent_memory::DerivedRecordRef chunk_ref_with_key{
        agent_memory::DerivedRecordKind::Chunk,
        agent_memory::ChunkId{"chunk:readme:extra"},
        "unexpected",
        0
    };

    if(!agent_memory::has_required_reference(chunk_ref_with_key)) {
        return fail("chunk ref with extra key still has the required chunk id");
    }

    if(agent_memory::is_valid_derived_record_ref(chunk_ref_with_key)) {
        return fail("strict validation must reject unused ref fields");
    }

    const agent_memory::DerivedRecordRef bucket_ref{
        agent_memory::DerivedRecordKind::BinaryBucketPosting,
        {},
        "bucket:24:1024",
        3
    };

    if(!agent_memory::has_required_reference(bucket_ref)) {
        return fail("bucket posting ref must be valid with key and ordinal");
    }

    if(!agent_memory::is_valid_derived_record_ref(bucket_ref)) {
        return fail("bucket posting ref must pass strict validation");
    }

    const agent_memory::ResourceManifest manifest{
        revision,
        {chunk_ref, bucket_ref}
    };

    if(!agent_memory::is_valid_resource_manifest(manifest)) {
        return fail("resource manifest must pass strict validation");
    }

    if(manifest.records.size() != 2) {
        return fail("resource manifest must store derived record refs");
    }

    if(manifest.revision.resource_id != resource_id) {
        return fail("resource manifest must retain resource revision");
    }

    auto duplicate_active = manifest;
    duplicate_active.records.push_back(chunk_ref);
    if(agent_memory::is_valid_resource_manifest(duplicate_active)) {
        return fail("resource manifest must reject duplicate active record identities");
    }

    auto duplicate_reclaim = manifest;
    duplicate_reclaim.pending_reclaim_records.push_back(bucket_ref);
    duplicate_reclaim.pending_reclaim_records.push_back(bucket_ref);
    if(agent_memory::is_valid_resource_manifest(duplicate_reclaim)) {
        return fail("resource manifest must reject duplicate reclaim record identities");
    }

    auto overlapping_reclaim = manifest;
    overlapping_reclaim.pending_reclaim_records.push_back(chunk_ref);
    if(agent_memory::is_valid_resource_manifest(overlapping_reclaim)) {
        return fail("resource manifest must reject active and reclaim identity overlap");
    }

    return 0;
}
