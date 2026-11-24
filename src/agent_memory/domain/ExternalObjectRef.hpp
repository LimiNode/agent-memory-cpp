#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_EXTERNAL_OBJECT_REF_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_EXTERNAL_OBJECT_REF_HPP_INCLUDED

/// \file ExternalObjectRef.hpp
/// \brief Namespace-qualified external object identity, independent of local storage keys.

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace agent_memory {

/// Signed, unsigned and opaque UTF-8 keys are distinct; strings are never parsed as numbers.
using ExternalIdentityScalar = std::variant<std::int64_t, std::uint64_t, std::string>;

/// One position of an ordered composite key; names must be unique within that key.
struct ExternalIdentityComponent final {
    std::string name;
    ExternalIdentityScalar value = std::string{};
};

/// A scalar key or an ordered tuple of 2..16 named scalar components.
/// Composite nesting is deliberately unavailable in version 1.
struct ExternalObjectKey final {
    std::variant<std::int64_t, std::uint64_t, std::string,
                 std::vector<ExternalIdentityComponent>> value = std::string{};
};

/// \brief Portable reference in an adapter-declared provider uniqueness domain.
///
/// The host owns namespace issuance and validation against the provider contract.
/// Observer accounts, authorization and selected revisions are separate provenance.
/// A parent has the same provider/instance/domain as its object. The parent key's
/// role is fixed by the adapter's object-kind recipe, never inferred from its value.
/// No constructor allocates an occurrence, authenticates a scope or converts a legacy ID.
/// Owned mutable values must be validated again at every import/encoding boundary.
/// See guides/identity-policy.md for namespace issuance and the complete AMXR v1 grammar.
struct ExternalObjectRef final {
    std::string provider;
    std::string service_instance;
    std::string identity_domain;
    std::string object_kind;
    std::optional<ExternalObjectKey> parent_key;
    ExternalObjectKey object_key;
};

/// \brief Copies an active scalar alternative directly, without a variant copy constructor.
/// \return An independent value with the same alternative and exact bytes.
/// \throws std::bad_alloc Allocation failures propagate; the input is unchanged.
/// \throws std::bad_variant_access If the input is valueless.
/// \note Use the named copy operations on affected standard libraries. Implicit copies
/// remain ordinary STL operations; aggregate initialization, layout and AMXR bytes are unchanged.
[[nodiscard]] ExternalIdentityScalar copy_external_identity_scalar(const ExternalIdentityScalar&);
/// \brief Copies a named component through the same exception-safe scalar operation.
[[nodiscard]] ExternalIdentityComponent copy_external_identity_component(const ExternalIdentityComponent&);
/// \brief Copies scalar/composite keys, including each nested component, without variant copying.
[[nodiscard]] ExternalObjectKey copy_external_object_key(const ExternalObjectKey&);
/// \brief Copies both optional parent and object keys through the shared key operation.
[[nodiscard]] ExternalObjectRef copy_external_object_ref(const ExternalObjectRef&);

inline constexpr std::size_t kMaxExternalObjectRefBytes = 65536;
inline constexpr std::size_t kMaxExternalIdentityKeyBytes = 4096;
inline constexpr std::size_t kMaxExternalIdentityComponents = 16;

/// Checks the full version-1 profile, including UTF-8, names, duplicates and total size.
/// Default values and present empty text keys are invalid; absent parent is valid.
/// No trimming, Unicode normalization, case folding or numeric-string coercion occurs.
[[nodiscard]] bool is_valid_external_object_ref(const ExternalObjectRef& value) noexcept;

/// Type-strict structural equality. Comparing invalid values does not admit them as identities.
[[nodiscard]] bool operator==(const ExternalIdentityComponent&, const ExternalIdentityComponent&);
[[nodiscard]] bool operator==(const ExternalObjectKey&, const ExternalObjectKey&);
[[nodiscard]] bool operator==(const ExternalObjectRef&, const ExternalObjectRef&);
[[nodiscard]] bool operator!=(const ExternalObjectRef&, const ExternalObjectRef&);

/// Canonical unsigned-byte encoding order, not numeric order. Allocates bounded encodings.
/// \throws std::invalid_argument if either reference violates the version-1 profile.
[[nodiscard]] bool operator<(const ExternalObjectRef&, const ExternalObjectRef&);

/// Encodes the standalone AMXR version-1 contract; does not write any storage schema.
/// Preflights the exact size before allocating its output buffer.
/// \throws std::invalid_argument for an invalid or oversized value.
[[nodiscard]] std::string serialize_external_object_ref(const ExternalObjectRef& value);

/// Decodes one complete bounded AMXR reference. Unknown formats and trailing bytes reject.
/// \throws std::invalid_argument for malformed, noncanonical or oversized input.
[[nodiscard]] ExternalObjectRef deserialize_external_object_ref(std::string_view bytes);

/// SHA-256 of the complete canonical AMXR bytes, not std::hash or an ArtifactId.
/// Digest equality does not replace full reference equality or authenticate its issuer.
[[nodiscard]] std::array<std::uint8_t, 32> external_object_ref_digest(const ExternalObjectRef& value);

} // namespace agent_memory

#endif
