#include "Artifact.hpp"

#include <algorithm>
#include <iomanip>
#include <sstream>
#include <utility>

namespace agent_memory {

namespace {

bool non_zero(const std::array<std::uint8_t, 32>& bytes) noexcept {
    return std::any_of(bytes.begin(), bytes.end(), [](std::uint8_t byte) {
        return byte != 0;
    });
}

bool known_retention(ArtifactRetentionClass retention) noexcept {
    switch(retention) {
    case ArtifactRetentionClass::ReferenceOnly:
    case ArtifactRetentionClass::SourceOriginal:
    case ArtifactRetentionClass::DerivedDurable:
    case ArtifactRetentionClass::RebuildableCache:
        return true;
    }
    return false;
}

bool known_body_state(BindingBodyState state) noexcept {
    switch(state) {
    case BindingBodyState::Materialized:
    case BindingBodyState::NeverRetained:
    case BindingBodyState::RetentionRemoved:
    case BindingBodyState::Evicted:
        return true;
    }
    return false;
}

} // namespace

SourceRevisionId::SourceRevisionId(std::string value) : m_value(std::move(value)) {}
const std::string& SourceRevisionId::value() const noexcept { return m_value; }
bool SourceRevisionId::empty() const noexcept { return m_value.empty(); }

ArtifactId::ArtifactId(std::string value) : m_value(std::move(value)) {}
const std::string& ArtifactId::value() const noexcept { return m_value; }
bool ArtifactId::empty() const noexcept { return m_value.empty(); }

ArtifactBindingId::ArtifactBindingId(std::string value) : m_value(std::move(value)) {}
const std::string& ArtifactBindingId::value() const noexcept { return m_value; }
bool ArtifactBindingId::empty() const noexcept { return m_value.empty(); }

bool operator==(const SourceRevisionId& left, const SourceRevisionId& right) noexcept {
    return left.value() == right.value();
}
bool operator!=(const SourceRevisionId& left, const SourceRevisionId& right) noexcept {
    return !(left == right);
}
bool operator<(const SourceRevisionId& left, const SourceRevisionId& right) noexcept {
    return left.value() < right.value();
}
bool operator==(const ArtifactId& left, const ArtifactId& right) noexcept {
    return left.value() == right.value();
}
bool operator!=(const ArtifactId& left, const ArtifactId& right) noexcept {
    return !(left == right);
}
bool operator<(const ArtifactId& left, const ArtifactId& right) noexcept {
    return left.value() < right.value();
}
bool operator==(const ArtifactBindingId& left, const ArtifactBindingId& right) noexcept {
    return left.value() == right.value();
}
bool operator!=(const ArtifactBindingId& left, const ArtifactBindingId& right) noexcept {
    return !(left == right);
}
bool operator<(const ArtifactBindingId& left, const ArtifactBindingId& right) noexcept {
    return left.value() < right.value();
}

bool is_valid_blob_digest(const BlobDigest& digest) noexcept {
    return digest.algorithm == BlobDigestAlgorithm::Sha256 && non_zero(digest.value);
}

ArtifactId artifact_id_for_digest(const BlobDigest& digest) {
    if(!is_valid_blob_digest(digest)) {
        return ArtifactId();
    }
    std::ostringstream stream;
    stream << "sha256:" << std::hex << std::setfill('0');
    for(const auto byte : digest.value) {
        stream << std::setw(2) << static_cast<unsigned int>(byte);
    }
    return ArtifactId(stream.str());
}

bool is_valid_artifact(const Artifact& artifact) {
    return is_valid_blob_digest(artifact.digest) &&
        artifact.id == artifact_id_for_digest(artifact.digest);
}

bool is_valid_artifact_binding(const ArtifactBinding& binding) noexcept {
    return !binding.id.empty() && !binding.source_revision_id.empty() &&
        !binding.artifact_id.empty() && !binding.role.empty();
}

bool is_valid_initial_body_state(
    ArtifactRetentionClass retention,
    BindingBodyState state
) noexcept {
    if(!known_retention(retention) || !known_body_state(state)) {
        return false;
    }
    switch(retention) {
    case ArtifactRetentionClass::ReferenceOnly:
        return state == BindingBodyState::NeverRetained;
    case ArtifactRetentionClass::SourceOriginal:
    case ArtifactRetentionClass::DerivedDurable:
        return state == BindingBodyState::Materialized;
    case ArtifactRetentionClass::RebuildableCache:
        return state == BindingBodyState::Materialized ||
            state == BindingBodyState::Evicted;
    }
    return false;
}

bool is_valid_body_state_transition(
    ArtifactRetentionClass retention,
    BindingBodyState current,
    BindingBodyState next
) noexcept {
    if(!known_retention(retention) || !known_body_state(current) ||
       !known_body_state(next)) {
        return false;
    }
    if(current == next) {
        return true;
    }
    if(retention == ArtifactRetentionClass::ReferenceOnly) {
        return false;
    }
    if(current == BindingBodyState::Materialized &&
       next == BindingBodyState::RetentionRemoved) {
        return retention == ArtifactRetentionClass::SourceOriginal ||
            retention == ArtifactRetentionClass::DerivedDurable ||
            retention == ArtifactRetentionClass::RebuildableCache;
    }
    if(retention == ArtifactRetentionClass::RebuildableCache &&
       current == BindingBodyState::Materialized &&
       next == BindingBodyState::Evicted) {
        return true;
    }
    if(retention == ArtifactRetentionClass::RebuildableCache &&
       current == BindingBodyState::Evicted &&
       next == BindingBodyState::Materialized) {
        return true;
    }
    if(retention == ArtifactRetentionClass::RebuildableCache &&
       current == BindingBodyState::Evicted &&
       next == BindingBodyState::RetentionRemoved) {
        return true;
    }
    return false;
}

} // namespace agent_memory
