#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_ARTIFACT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_ARTIFACT_HPP_INCLUDED

/// \file Artifact.hpp
/// \brief Backend-neutral identity and retention contracts for source bytes.

#include <array>
#include <cstdint>
#include <optional>
#include <string>

namespace agent_memory {

/// \brief Opaque source-revision identity used by artifact bindings.
class SourceRevisionId final {
public:
    SourceRevisionId() = default;
    explicit SourceRevisionId(std::string value);
    [[nodiscard]] const std::string& value() const noexcept;
    [[nodiscard]] bool empty() const noexcept;

private:
    std::string m_value;
};

/// \brief Globally deduplicated immutable-artifact identity.
class ArtifactId final {
public:
    ArtifactId() = default;
    explicit ArtifactId(std::string value);
    [[nodiscard]] const std::string& value() const noexcept;
    [[nodiscard]] bool empty() const noexcept;

private:
    std::string m_value;
};

/// \brief Immutable relationship identity between a source revision and an artifact.
class ArtifactBindingId final {
public:
    ArtifactBindingId() = default;
    explicit ArtifactBindingId(std::string value);
    [[nodiscard]] const std::string& value() const noexcept;
    [[nodiscard]] bool empty() const noexcept;

private:
    std::string m_value;
};

[[nodiscard]] bool operator==(const SourceRevisionId&, const SourceRevisionId&) noexcept;
[[nodiscard]] bool operator!=(const SourceRevisionId&, const SourceRevisionId&) noexcept;
[[nodiscard]] bool operator<(const SourceRevisionId&, const SourceRevisionId&) noexcept;
[[nodiscard]] bool operator==(const ArtifactId&, const ArtifactId&) noexcept;
[[nodiscard]] bool operator!=(const ArtifactId&, const ArtifactId&) noexcept;
[[nodiscard]] bool operator<(const ArtifactId&, const ArtifactId&) noexcept;
[[nodiscard]] bool operator==(const ArtifactBindingId&, const ArtifactBindingId&) noexcept;
[[nodiscard]] bool operator!=(const ArtifactBindingId&, const ArtifactBindingId&) noexcept;
[[nodiscard]] bool operator<(const ArtifactBindingId&, const ArtifactBindingId&) noexcept;

/// \brief Algorithm-tagged digest of decoded immutable artifact bytes.
enum class BlobDigestAlgorithm : std::uint8_t {
    Sha256 = 1
};

struct BlobDigest final {
    BlobDigestAlgorithm algorithm = BlobDigestAlgorithm::Sha256;
    std::array<std::uint8_t, 32> value{};
};

[[nodiscard]] bool is_valid_blob_digest(const BlobDigest& digest) noexcept;

/// \brief Computes the canonical opaque id for a valid decoded-byte digest.
///
/// The textual form is deliberately independent of storage layout and codec:
/// `sha256:` followed by lowercase hexadecimal decoded bytes.
[[nodiscard]] ArtifactId artifact_id_for_digest(const BlobDigest& digest);

/// \brief Retention policy for one immutable artifact binding.
enum class ArtifactRetentionClass : std::uint8_t {
    ReferenceOnly,
    SourceOriginal,
    DerivedDurable,
    RebuildableCache
};

/// \brief Durable body state of one artifact binding.
enum class BindingBodyState : std::uint8_t {
    Materialized,
    NeverRetained,
    RetentionRemoved,
    Evicted
};

/// \brief Immutable byte identity. Role and retention belong to a binding.
struct Artifact final {
    ArtifactId id;
    BlobDigest digest;
    std::uint64_t size_bytes = 0;
};

/// \brief Immutable source-revision-to-artifact relationship.
struct ArtifactBinding final {
    ArtifactBindingId id;
    SourceRevisionId source_revision_id;
    ArtifactId artifact_id;
    std::string role;
    ArtifactRetentionClass retention = ArtifactRetentionClass::ReferenceOnly;
    std::optional<std::string> media_type;
    std::optional<std::string> original_name;
    std::uint64_t bound_at_ms = 0;
};

/// \brief Append-only body-state receipt for one artifact binding.
///
/// Catalog implementations add policy/authority evidence and validate the
/// receipt chain. This value slice only defines the identity and state fields;
/// it does not publish a durable catalog or body store.
struct ArtifactBodyStateReceipt final {
    std::string receipt_id;
    ArtifactBindingId binding_id;
    BindingBodyState state = BindingBodyState::NeverRetained;
    std::optional<std::string> previous_receipt_id;
    std::uint64_t recorded_at_ms = 0;
};

[[nodiscard]] bool is_valid_artifact(const Artifact& artifact);
[[nodiscard]] bool is_valid_artifact_binding(const ArtifactBinding& binding) noexcept;

/// \brief Checks whether a receipt is valid for the binding's retention policy.
[[nodiscard]] bool is_valid_initial_body_state(
    ArtifactRetentionClass retention,
    BindingBodyState state
) noexcept;

/// \brief Checks an append-only state transition without consulting a backend.
[[nodiscard]] bool is_valid_body_state_transition(
    ArtifactRetentionClass retention,
    BindingBodyState current,
    BindingBodyState next
) noexcept;

} // namespace agent_memory

#endif // AGENT_MEMORY_HEADER_DOMAIN_ARTIFACT_HPP_INCLUDED
