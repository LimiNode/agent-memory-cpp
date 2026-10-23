#include "PersistentLexicalIndex.hpp"

#include <algorithm>
#include <unordered_set>

namespace agent_memory {
namespace {

    bool has_valid_layout_versions(const std::vector<std::uint32_t>& versions) noexcept {
        if(versions.empty()) {
            return false;
        }

        for(std::size_t index = 0; index < versions.size(); ++index) {
            if(versions[index] == 0) {
                return false;
            }
            if(std::find(versions.begin(), versions.begin() + index, versions[index])
                != versions.begin() + index) {
                return false;
            }
        }
        return true;
    }

    bool supports_layout(
        const std::vector<std::uint32_t>& versions,
        const std::uint32_t layout
    ) noexcept {
        return std::find(versions.begin(), versions.end(), layout) != versions.end();
    }

} // namespace

bool LexicalStatisticsEpoch::empty() const noexcept {
    return std::all_of(m_bytes.begin(), m_bytes.end(), [](const std::uint8_t value) {
        return value == 0;
    });
}

bool operator==(
    const LexicalStatisticsEpoch& lhs,
    const LexicalStatisticsEpoch& rhs
) noexcept {
    return lhs.bytes() == rhs.bytes();
}

bool operator!=(
    const LexicalStatisticsEpoch& lhs,
    const LexicalStatisticsEpoch& rhs
) noexcept {
    return !(lhs == rhs);
}

bool operator==(
    const LexicalIndexDescriptorRef& lhs,
    const LexicalIndexDescriptorRef& rhs
) noexcept {
    return lhs.scope_id == rhs.scope_id
        && lhs.projection_kind == rhs.projection_kind
        && lhs.statistics_epoch == rhs.statistics_epoch;
}

bool operator!=(
    const LexicalIndexDescriptorRef& lhs,
    const LexicalIndexDescriptorRef& rhs
) noexcept {
    return !(lhs == rhs);
}

bool is_valid_lexical_index_descriptor(
    const LexicalIndexDescriptor& descriptor
) noexcept {
    return !descriptor.ref.scope_id.empty()
        && is_valid_projection_kind(descriptor.ref.projection_kind)
        && !descriptor.ref.statistics_epoch.empty()
        && !descriptor.projection_schema_id.empty()
        && descriptor.projection_schema_version != 0
        && !descriptor.tokenizer_fingerprint.empty()
        && !descriptor.normalization_fingerprint.empty()
        && has_valid_layout_versions(descriptor.supported_posting_layout_versions);
}

bool is_valid_lexical_active_snapshot(
    const LexicalActiveSnapshot& snapshot
) noexcept {
    return !snapshot.statistics_epoch.empty()
        && snapshot.statistics_generation != 0
        && snapshot.posting_layout_version != 0;
}

bool is_valid_lexical_index_reader_profile(
    const LexicalIndexReaderProfile& profile
) noexcept {
    return !profile.projection_schema_id.empty()
        && profile.projection_schema_version != 0
        && !profile.tokenizer_fingerprint.empty()
        && !profile.normalization_fingerprint.empty()
        && has_valid_layout_versions(profile.supported_posting_layout_versions);
}

LexicalIndexAdmission check_lexical_index_admission(
    const LexicalIndexDescriptor& descriptor,
    const LexicalActiveSnapshot& snapshot,
    const LexicalIndexReaderProfile& reader_profile
) noexcept {
    if(!is_valid_lexical_index_descriptor(descriptor)) {
        return LexicalIndexAdmission::InvalidDescriptor;
    }
    if(!is_valid_lexical_index_reader_profile(reader_profile)) {
        return LexicalIndexAdmission::InvalidReaderProfile;
    }
    if(!is_valid_lexical_active_snapshot(snapshot)) {
        return LexicalIndexAdmission::InvalidActiveSnapshot;
    }
    if(descriptor.ref.statistics_epoch != snapshot.statistics_epoch) {
        return LexicalIndexAdmission::DescriptorIdentityMismatch;
    }
    if(descriptor.projection_schema_id != reader_profile.projection_schema_id
        || descriptor.projection_schema_version != reader_profile.projection_schema_version) {
        return LexicalIndexAdmission::ProjectionSchemaMismatch;
    }
    if(descriptor.tokenizer_fingerprint != reader_profile.tokenizer_fingerprint) {
        return LexicalIndexAdmission::TokenizerFingerprintMismatch;
    }
    if(descriptor.normalization_fingerprint != reader_profile.normalization_fingerprint) {
        return LexicalIndexAdmission::NormalizationFingerprintMismatch;
    }
    if(!supports_layout(
        descriptor.supported_posting_layout_versions,
        snapshot.posting_layout_version
    ) || !supports_layout(
        reader_profile.supported_posting_layout_versions,
        snapshot.posting_layout_version
    )) {
        return LexicalIndexAdmission::UnsupportedPostingLayout;
    }
    return LexicalIndexAdmission::Compatible;
}

bool is_valid_lexical_unit_revision_observation(
    const LexicalUnitRevisionObservation& observation
) noexcept {
    return !observation.scope_id.empty()
        && !observation.unit_id.empty()
        && observation.unit_revision != 0
        && !observation.resource_id.empty()
        && observation.resource_generation != 0
        && is_valid_projection_version(observation.projection_version)
        && observation.manifest_state == ResourceManifestState::Active
        && observation.has_active_unit_binding
        && observation.has_active_projection_binding;
}

bool is_valid_lexical_read_frontier(
    const LexicalReadFrontier& frontier
) {
    switch(frontier.consistency) {
        case LexicalReadConsistency::NonLinearizable:
        case LexicalReadConsistency::PinnedSnapshot:
            break;
        default:
            return false;
    }

    if(frontier.publication_sequence.empty()
        || !is_valid_lexical_index_descriptor(frontier.index_descriptor)
        || !is_valid_lexical_active_snapshot(frontier.active_snapshot)
        || frontier.index_descriptor.ref.statistics_epoch
            != frontier.active_snapshot.statistics_epoch
        || !supports_layout(
            frontier.index_descriptor.supported_posting_layout_versions,
            frontier.active_snapshot.posting_layout_version
        )) {
        return false;
    }

    std::unordered_set<std::uint64_t> observed_unit_ids;
    observed_unit_ids.reserve(frontier.observed_unit_revisions.size());
    for(const auto& observation : frontier.observed_unit_revisions) {
        if(!is_valid_lexical_unit_revision_observation(observation)
            || observation.scope_id != frontier.index_descriptor.ref.scope_id
            || !observed_unit_ids.insert(observation.unit_id.value()).second) {
            return false;
        }
    }
    return true;
}

bool supports_exact_lexical_read(
    const LexicalReadFrontier& frontier,
    const LexicalIndexReaderProfile& reader_profile
) {
    return is_valid_lexical_read_frontier(frontier)
        && frontier.consistency == LexicalReadConsistency::PinnedSnapshot
        && check_lexical_index_admission(
            frontier.index_descriptor,
            frontier.active_snapshot,
            reader_profile
        ) == LexicalIndexAdmission::Compatible;
}

} // namespace agent_memory
