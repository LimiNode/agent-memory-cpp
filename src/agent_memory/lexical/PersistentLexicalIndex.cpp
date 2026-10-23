#include "PersistentLexicalIndex.hpp"

#include <algorithm>

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
    return !snapshot.descriptor_ref.scope_id.empty()
        && is_valid_projection_kind(snapshot.descriptor_ref.projection_kind)
        && !snapshot.descriptor_ref.statistics_epoch.empty()
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
    if(descriptor.ref != snapshot.descriptor_ref) {
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

bool is_valid_lexical_unit_revision_binding(
    const LexicalUnitRevisionBinding& binding
) noexcept {
    return !binding.unit_id.empty()
        && !binding.resource_id.empty()
        && binding.resource_generation != 0
        && is_valid_projection_version(binding.projection_version)
        && binding.manifest_state == ResourceManifestState::Active;
}

bool is_valid_lexical_read_frontier(
    const LexicalReadFrontier& frontier
) noexcept {
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
        || frontier.index_descriptor.ref != frontier.active_snapshot.descriptor_ref
        || !supports_layout(
            frontier.index_descriptor.supported_posting_layout_versions,
            frontier.active_snapshot.posting_layout_version
        )) {
        return false;
    }

    for(std::size_t index = 0; index < frontier.observed_unit_bindings.size(); ++index) {
        const auto& binding = frontier.observed_unit_bindings[index];
        if(!is_valid_lexical_unit_revision_binding(binding)) {
            return false;
        }
        for(std::size_t previous = 0; previous < index; ++previous) {
            if(frontier.observed_unit_bindings[previous].unit_id == binding.unit_id) {
                return false;
            }
        }
    }
    return true;
}

bool supports_exact_lexical_read(
    const LexicalReadFrontier& frontier,
    const LexicalIndexReaderProfile& reader_profile
) noexcept {
    return is_valid_lexical_read_frontier(frontier)
        && frontier.consistency == LexicalReadConsistency::PinnedSnapshot
        && check_lexical_index_admission(
            frontier.index_descriptor,
            frontier.active_snapshot,
            reader_profile
        ) == LexicalIndexAdmission::Compatible;
}

} // namespace agent_memory
