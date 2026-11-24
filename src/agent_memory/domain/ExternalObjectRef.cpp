#include "ExternalObjectRef.hpp"
#include "Sha256.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view MAGIC = "AMXR";
constexpr std::uint16_t VERSION = 1;

bool valid_utf8(std::string_view text) noexcept {
    for(std::size_t i = 0; i < text.size();) {
        const auto lead = static_cast<unsigned char>(text[i]);
        std::size_t width = 0;
        if(lead <= 0x7fU) width = 1;
        else if(lead >= 0xc2U && lead <= 0xdfU) width = 2;
        else if(lead >= 0xe0U && lead <= 0xefU) width = 3;
        else if(lead >= 0xf0U && lead <= 0xf4U) width = 4;
        else return false;
        if(width > text.size() - i) return false;
        for(std::size_t j = 1; j < width; ++j) {
            if((static_cast<unsigned char>(text[i + j]) & 0xc0U) != 0x80U) return false;
        }
        if(width == 3 && lead == 0xe0U && static_cast<unsigned char>(text[i + 1]) < 0xa0U) return false;
        if(width == 3 && lead == 0xedU && static_cast<unsigned char>(text[i + 1]) >= 0xa0U) return false;
        if(width == 4 && lead == 0xf0U && static_cast<unsigned char>(text[i + 1]) < 0x90U) return false;
        if(width == 4 && lead == 0xf4U && static_cast<unsigned char>(text[i + 1]) >= 0x90U) return false;
        i += width;
    }
    return true;
}

bool label(std::string_view text, std::size_t maximum, bool namespace_label) noexcept {
    if(text.empty() || text.size() > maximum || text.front() < 'a' || text.front() > 'z') return false;
    for(const auto c : text) {
        if((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_') continue;
        if(namespace_label && (c == '.' || c == '-')) continue;
        return false;
    }
    return true;
}

bool text_key(std::string_view text, std::size_t maximum) noexcept {
    return !text.empty() && text.size() <= maximum && valid_utf8(text);
}

bool add_size(std::size_t& total, std::size_t additional) noexcept {
    if(additional > kMaxExternalObjectRefBytes - total) return false;
    total += additional;
    return true;
}

template<typename Variant>
bool scalar_size(const Variant& value, std::size_t& size) noexcept {
    if(value.index() < 2) return add_size(size, 13);
    const auto* text = std::get_if<std::string>(&value);
    return text && text_key(*text, kMaxExternalIdentityKeyBytes) &&
        add_size(size, 5) && add_size(size, text->size());
}

bool key_size(const ExternalObjectKey& key, std::size_t& size) noexcept {
    const auto* parts = std::get_if<std::vector<ExternalIdentityComponent>>(&key.value);
    if(!parts) return scalar_size(key.value, size);
    if(parts->size() < 2 || parts->size() > kMaxExternalIdentityComponents || !add_size(size, 7)) return false;
    for(std::size_t i = 0; i < parts->size(); ++i) {
        const auto& part = (*parts)[i];
        if(!label(part.name, 64, false)) return false;
        for(std::size_t j = 0; j < i; ++j) if((*parts)[j].name == part.name) return false;
        if(!add_size(size, 2) || !add_size(size, part.name.size()) || !scalar_size(part.value, size)) return false;
    }
    return true;
}

bool reference_size(const ExternalObjectRef& ref, std::size_t& size) noexcept {
    size = 6;
    if(!label(ref.provider, 128, true) || !label(ref.object_kind, 64, true) ||
       !text_key(ref.service_instance, 1024) || !text_key(ref.identity_domain, 1024)) return false;
    for(const auto* text : {&ref.provider, &ref.service_instance, &ref.identity_domain, &ref.object_kind}) {
        if(!add_size(size, 4) || !add_size(size, text->size())) return false;
    }
    return add_size(size, 1) && (!ref.parent_key || key_size(*ref.parent_key, size)) && key_size(ref.object_key, size);
}

void put_uint(std::string& out, std::uint64_t value, unsigned width) {
    for(unsigned i = width; i > 0; --i) out.push_back(static_cast<char>((value >> ((i - 1) * 8)) & 0xffU));
}

void put_text(std::string& out, std::string_view text, unsigned width) {
    put_uint(out, text.size(), width);
    out.append(text.data(), text.size());
}

template<typename Variant>
void put_scalar(std::string& out, const Variant& value) {
    out.push_back(static_cast<char>(value.index() + 1));
    if(const auto* text = std::get_if<std::string>(&value)) put_text(out, *text, 4);
    else {
        put_uint(out, 8, 4);
        const auto bits = value.index() == 0 ? static_cast<std::uint64_t>(std::get<std::int64_t>(value)) :
            std::get<std::uint64_t>(value);
        put_uint(out, bits, 8);
    }
}

void put_key(std::string& out, const ExternalObjectKey& key) {
    const auto* parts = std::get_if<std::vector<ExternalIdentityComponent>>(&key.value);
    if(!parts) { put_scalar(out, key.value); return; }
    std::size_t size = 0;
    key_size(key, size); // The whole reference has already passed preflight.
    out.push_back('\x04');
    put_uint(out, size - 5, 4);
    put_uint(out, parts->size(), 2);
    for(const auto& part : *parts) { put_text(out, part.name, 2); put_scalar(out, part.value); }
}

[[noreturn]] void reject() { throw std::invalid_argument("invalid external object reference"); }

class Reader final {
public:
    explicit Reader(std::string_view input) : bytes(input) {}
    std::uint64_t integer(unsigned width) {
        const auto value = take(width);
        std::uint64_t result = 0;
        for(const auto c : value) result = (result << 8) | static_cast<unsigned char>(c);
        return result;
    }
    std::string_view take(std::size_t count) {
        if(count > bytes.size() - position) reject();
        const auto result = bytes.substr(position, count);
        position += count;
        return result;
    }
    std::string text(unsigned width, std::size_t maximum) {
        const auto count = integer(width);
        if(count == 0 || count > maximum || count > remaining()) reject();
        const auto value = take(static_cast<std::size_t>(count));
        if(!valid_utf8(value)) reject();
        return std::string(value);
    }
    std::size_t remaining() const noexcept { return bytes.size() - position; }
private:
    std::string_view bytes;
    std::size_t position = 0;
};

ExternalIdentityScalar read_scalar(unsigned tag, Reader& payload) {
    if(tag == 1 || tag == 2) {
        if(payload.remaining() != 8) reject();
        const auto bits = payload.integer(8);
        if(tag == 2) return bits;
        // Every conversion stays representable, including INT64_MIN.
        const auto maximum = static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
        return bits <= maximum ? static_cast<std::int64_t>(bits) : -static_cast<std::int64_t>(~bits) - 1;
    }
    if(tag != 3 || payload.remaining() == 0 || payload.remaining() > kMaxExternalIdentityKeyBytes) reject();
    const auto value = payload.take(payload.remaining());
    if(!valid_utf8(value)) reject();
    return std::string(value);
}

ExternalIdentityScalar read_scalar_frame(Reader& input) {
    const auto tag = static_cast<unsigned>(input.integer(1));
    const auto size = input.integer(4);
    if(tag < 1 || tag > 3 || size > input.remaining()) reject();
    Reader payload(input.take(static_cast<std::size_t>(size)));
    return read_scalar(tag, payload);
}

ExternalObjectKey read_key(Reader& input) {
    const auto tag = static_cast<unsigned>(input.integer(1));
    const auto size = input.integer(4);
    if(size > input.remaining()) reject();
    Reader payload(input.take(static_cast<std::size_t>(size)));
    if(tag != 4) {
        auto scalar = read_scalar(tag, payload);
        return std::visit([](auto&& value) { return ExternalObjectKey{std::forward<decltype(value)>(value)}; },
                          std::move(scalar));
    }
    const auto count = payload.integer(2);
    // Each component needs a nonempty name (2+1), scalar frame (5), and payload (>=1).
    if(count < 2 || count > kMaxExternalIdentityComponents || count > payload.remaining() / 9) reject();
    std::vector<ExternalIdentityComponent> parts;
    parts.reserve(static_cast<std::size_t>(count));
    for(std::uint64_t i = 0; i < count; ++i) {
        auto name = payload.text(2, 64);
        if(!label(name, 64, false)) reject();
        for(const auto& part : parts) if(part.name == name) reject();
        parts.push_back({std::move(name), read_scalar_frame(payload)});
    }
    if(payload.remaining() != 0) reject();
    return ExternalObjectKey{std::move(parts)};
}

} // namespace

ExternalIdentityScalar copy_external_identity_scalar(const ExternalIdentityScalar& value) {
    return std::visit([](const auto& scalar) -> ExternalIdentityScalar {
        using Scalar = std::decay_t<decltype(scalar)>;
        return ExternalIdentityScalar{std::in_place_type<Scalar>, scalar};
    }, value);
}

ExternalIdentityComponent copy_external_identity_component(const ExternalIdentityComponent& value) {
    return {value.name, copy_external_identity_scalar(value.value)};
}

ExternalObjectKey copy_external_object_key(const ExternalObjectKey& key) {
    using Value = decltype(key.value);
    return std::visit([](const auto& value) -> ExternalObjectKey {
        using Alternative = std::decay_t<decltype(value)>;
        if constexpr(std::is_same_v<Alternative, std::vector<ExternalIdentityComponent>>) {
            std::vector<ExternalIdentityComponent> parts;
            parts.reserve(value.size());
            for(const auto& part : value) parts.push_back(copy_external_identity_component(part));
            return {Value{std::in_place_type<Alternative>, std::move(parts)}};
        } else {
            return {Value{std::in_place_type<Alternative>, value}};
        }
    }, key.value);
}

ExternalObjectRef copy_external_object_ref(const ExternalObjectRef& value) {
    std::optional<ExternalObjectKey> parent;
    if(value.parent_key) parent = copy_external_object_key(*value.parent_key);
    return {value.provider, value.service_instance, value.identity_domain, value.object_kind,
        std::move(parent), copy_external_object_key(value.object_key)};
}

bool is_valid_external_object_ref(const ExternalObjectRef& value) noexcept {
    std::size_t size = 0;
    return reference_size(value, size);
}

bool operator==(const ExternalIdentityComponent& lhs, const ExternalIdentityComponent& rhs) {
    return lhs.name == rhs.name && lhs.value == rhs.value;
}
bool operator==(const ExternalObjectKey& lhs, const ExternalObjectKey& rhs) { return lhs.value == rhs.value; }
bool operator==(const ExternalObjectRef& lhs, const ExternalObjectRef& rhs) {
    return lhs.provider == rhs.provider && lhs.service_instance == rhs.service_instance &&
        lhs.identity_domain == rhs.identity_domain && lhs.object_kind == rhs.object_kind &&
        lhs.parent_key == rhs.parent_key && lhs.object_key == rhs.object_key;
}
bool operator!=(const ExternalObjectRef& lhs, const ExternalObjectRef& rhs) { return !(lhs == rhs); }
bool operator<(const ExternalObjectRef& lhs, const ExternalObjectRef& rhs) {
    const auto a = serialize_external_object_ref(lhs);
    const auto b = serialize_external_object_ref(rhs);
    return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end(),
        [](unsigned char x, unsigned char y) { return x < y; });
}

std::string serialize_external_object_ref(const ExternalObjectRef& value) {
    std::size_t size = 0;
    if(!reference_size(value, size)) reject();
    std::string out;
    out.reserve(size);
    out.append(MAGIC.data(), MAGIC.size());
    put_uint(out, VERSION, 2);
    for(const auto* text : {&value.provider, &value.service_instance, &value.identity_domain, &value.object_kind})
        put_text(out, *text, 4);
    out.push_back(value.parent_key ? '\x01' : '\x00');
    if(value.parent_key) put_key(out, *value.parent_key);
    put_key(out, value.object_key);
    if(out.size() != size) throw std::logic_error("external identity size preflight mismatch");
    return out;
}

ExternalObjectRef deserialize_external_object_ref(std::string_view bytes) {
    if(bytes.size() > kMaxExternalObjectRefBytes) reject();
    Reader reader(bytes);
    if(reader.take(4) != MAGIC || reader.integer(2) != VERSION) reject();
    ExternalObjectRef result;
    result.provider = reader.text(4, 128);
    result.service_instance = reader.text(4, 1024);
    result.identity_domain = reader.text(4, 1024);
    result.object_kind = reader.text(4, 64);
    const auto presence = reader.integer(1);
    if(presence > 1) reject();
    if(presence == 1) result.parent_key = read_key(reader);
    result.object_key = read_key(reader);
    if(reader.remaining() != 0 || !is_valid_external_object_ref(result)) reject();
    return result;
}

std::array<std::uint8_t, 32> external_object_ref_digest(const ExternalObjectRef& value) {
    return sha256(serialize_external_object_ref(value));
}

} // namespace agent_memory
