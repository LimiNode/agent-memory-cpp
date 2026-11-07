#include "FramedZstdCodec.hpp"

#include <agent_memory/domain/Sha256.hpp>

#if AGENT_MEMORY_ENABLE_ZSTD
#include <zstd.h>
#endif

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace agent_memory {
namespace {

constexpr std::string_view kMagic = "AMZF";
constexpr std::uint8_t kRawFlag = 1U;

void append_u8(std::string& output, std::uint8_t value) { output.push_back(static_cast<char>(value)); }
void append_u32(std::string& output, std::uint32_t value) {
    for(int shift = 24; shift >= 0; shift -= 8) output.push_back(static_cast<char>((value >> shift) & 0xffU));
}
void append_u64(std::string& output, std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8) output.push_back(static_cast<char>((value >> shift) & 0xffU));
}
void append_digest(std::string& output, const BlobDigest& digest) {
    output.append(reinterpret_cast<const char*>(digest.value.data()), digest.value.size());
}
std::uint8_t read_u8(std::string_view input, std::size_t& position) {
    if(position >= input.size()) throw std::runtime_error("framed zstd header is truncated");
    return static_cast<std::uint8_t>(input[position++]);
}
std::uint32_t read_u32(std::string_view input, std::size_t& position) {
    if(position > input.size() || input.size() - position < 4U) throw std::runtime_error("framed zstd length is truncated");
    std::uint32_t value = 0;
    for(int i = 0; i != 4; ++i) value = (value << 8U) | static_cast<std::uint8_t>(input[position++]);
    return value;
}
std::uint64_t read_u64(std::string_view input, std::size_t& position) {
    if(position > input.size() || input.size() - position < 8U) throw std::runtime_error("framed zstd length is truncated");
    std::uint64_t value = 0;
    for(int i = 0; i != 8; ++i) value = (value << 8U) | static_cast<std::uint8_t>(input[position++]);
    return value;
}
BlobDigest read_digest(std::string_view input, std::size_t& position) {
    if(position > input.size() || input.size() - position < 32U)
        throw std::runtime_error("framed zstd digest is truncated");
    BlobDigest digest;
    std::memcpy(digest.value.data(), input.data() + position, digest.value.size());
    position += digest.value.size();
    return digest;
}
void validate_options(const FramedZstdOptions& options) {
    if(options.frame_bytes == 0 || options.max_frame_bytes == 0 || options.max_decoded_bytes == 0 ||
       options.frame_bytes > options.max_frame_bytes || options.compression_level < 1 || options.compression_level > 22)
        throw std::invalid_argument("invalid framed zstd options");
}

bool checked_add(std::uint64_t left, std::uint64_t right, std::uint64_t& result) noexcept {
    if(right > std::numeric_limits<std::uint64_t>::max() - left) return false;
    result = left + right;
    return true;
}

std::uint64_t expected_frame_count(std::uint64_t decoded_size,
                                   std::uint64_t frame_bytes) noexcept {
    if(decoded_size == 0) return 0;
    return 1U + ((decoded_size - 1U) / frame_bytes);
}

BlobDigest digest_for(std::string_view bytes) {
    BlobDigest digest;
    digest.value = sha256(bytes);
    return digest;
}

FramedZstdDescriptor descriptor_from(std::uint32_t frame_bytes, int level, bool raw) {
    FramedZstdDescriptor descriptor;
    descriptor.frame_bytes = frame_bytes;
    descriptor.compression_level = level;
    descriptor.raw_fallback = raw;
    return descriptor;
}

} // namespace

FramedZstdEncoded encode_framed_zstd(std::string_view decoded_bytes,
                                     bool already_compressed,
                                     FramedZstdOptions options) {
    validate_options(options);
    if(decoded_bytes.size() > options.max_decoded_bytes)
        throw std::invalid_argument("decoded payload exceeds framed zstd budget");
#if !AGENT_MEMORY_ENABLE_ZSTD
    static_cast<void>(decoded_bytes); static_cast<void>(already_compressed);
    throw std::runtime_error("framed zstd support is disabled");
#else
    const auto digest = digest_for(decoded_bytes);
    FramedZstdEncoded result;
    result.decoded_digest = digest;
    result.artifact_id = artifact_id_for_digest(digest);
    result.descriptor = descriptor_from(options.frame_bytes, options.compression_level, already_compressed);
    result.bytes.assign(kMagic.data(), kMagic.size());
    append_u8(result.bytes, static_cast<std::uint8_t>(result.descriptor.format_version));
    append_u8(result.bytes, already_compressed ? kRawFlag : 0U);
    append_u32(result.bytes, options.frame_bytes);
    append_u8(result.bytes, static_cast<std::uint8_t>(options.compression_level));
    append_u8(result.bytes, 0U); append_u8(result.bytes, 0U); append_u8(result.bytes, 0U);
    append_u64(result.bytes, decoded_bytes.size());
    const auto frame_count = decoded_bytes.empty() ? 0U :
        (decoded_bytes.size() + options.frame_bytes - 1U) / options.frame_bytes;
    append_u64(result.bytes, frame_count);
    append_digest(result.bytes, digest);
    for(std::uint64_t frame = 0; frame < frame_count; ++frame) {
        const auto offset = static_cast<std::size_t>(frame * options.frame_bytes);
        const auto length = std::min<std::size_t>(options.frame_bytes, decoded_bytes.size() - offset);
        const auto input = decoded_bytes.substr(offset, length);
        std::string encoded;
        if(already_compressed) encoded.assign(input.data(), input.size());
        else {
            const auto bound = ZSTD_compressBound(length);
            encoded.resize(bound);
            const auto written = ZSTD_compress(encoded.data(), bound, input.data(), length, options.compression_level);
            if(ZSTD_isError(written)) throw std::runtime_error("framed zstd compression failed");
            encoded.resize(written);
        }
        if(encoded.size() > options.max_frame_bytes || encoded.size() > std::numeric_limits<std::uint32_t>::max())
            throw std::invalid_argument("encoded frame exceeds its budget");
        append_u32(result.bytes, static_cast<std::uint32_t>(length));
        append_u32(result.bytes, static_cast<std::uint32_t>(encoded.size()));
        result.bytes.append(encoded);
    }
    return result;
#endif
}

FramedZstdDecoded decode_framed_zstd(std::string_view encoded_bytes,
                                     FramedZstdOptions options) {
    validate_options(options);
#if !AGENT_MEMORY_ENABLE_ZSTD
    static_cast<void>(encoded_bytes);
    throw std::runtime_error("framed zstd support is disabled");
#else
    std::size_t position = 0;
    if(encoded_bytes.size() < kMagic.size() || encoded_bytes.substr(0, kMagic.size()) != kMagic)
        throw std::runtime_error("framed zstd magic is invalid");
    position += kMagic.size();
    const auto version = read_u8(encoded_bytes, position);
    const auto flags = read_u8(encoded_bytes, position);
    if(version != 1U || (flags & ~kRawFlag) != 0U) throw std::runtime_error("framed zstd descriptor is unsupported");
    const auto frame_bytes = read_u32(encoded_bytes, position);
    const auto level = static_cast<int>(read_u8(encoded_bytes, position));
    if(encoded_bytes.size() - position < 3U) throw std::runtime_error("framed zstd descriptor is truncated");
    const auto reserved0 = read_u8(encoded_bytes, position);
    const auto reserved1 = read_u8(encoded_bytes, position);
    const auto reserved2 = read_u8(encoded_bytes, position);
    if(reserved0 != 0U || reserved1 != 0U || reserved2 != 0U)
        throw std::runtime_error("framed zstd reserved descriptor bytes are non-zero");
    const auto decoded_size = read_u64(encoded_bytes, position);
    const auto frame_count = read_u64(encoded_bytes, position);
    const auto declared_digest = read_digest(encoded_bytes, position);
    if(frame_bytes == 0 || frame_bytes > options.max_frame_bytes || decoded_size > options.max_decoded_bytes ||
       decoded_size > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()) ||
       level < 1 || level > 22)
        throw std::runtime_error("framed zstd descriptor exceeds budget");
    const auto expected_count = expected_frame_count(decoded_size, frame_bytes);
    if(frame_count != expected_count)
        throw std::runtime_error("framed zstd frame count is invalid");
    FramedZstdDecoded result;
    result.descriptor = descriptor_from(frame_bytes, level, (flags & kRawFlag) != 0U);
    result.bytes.reserve(static_cast<std::size_t>(decoded_size));
    std::uint64_t decoded_cursor = 0;
    for(std::uint64_t frame = 0; frame < frame_count; ++frame) {
        const auto decoded_frame_size = read_u32(encoded_bytes, position);
        const auto encoded_frame_size = read_u32(encoded_bytes, position);
        const auto expected_size = static_cast<std::uint64_t>(
            std::min<std::uint64_t>(frame_bytes, decoded_size - decoded_cursor));
        if(decoded_frame_size == 0 || decoded_frame_size != expected_size || encoded_frame_size > options.max_frame_bytes ||
           position > encoded_bytes.size() || encoded_frame_size > encoded_bytes.size() - position ||
           decoded_frame_size > decoded_size - decoded_cursor)
            throw std::runtime_error("framed zstd frame is invalid");
        const auto payload = encoded_bytes.substr(position, encoded_frame_size);
        position += encoded_frame_size;
        const auto old_size = result.bytes.size();
        if(decoded_frame_size > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()) - old_size ||
           decoded_frame_size > decoded_size - decoded_cursor ||
           old_size > static_cast<std::size_t>(decoded_size))
            throw std::runtime_error("framed zstd output budget is exhausted");
        const auto new_size = old_size + static_cast<std::size_t>(decoded_frame_size);
        if(static_cast<std::uint64_t>(new_size) > decoded_size ||
           static_cast<std::uint64_t>(new_size) > options.max_decoded_bytes)
            throw std::runtime_error("framed zstd output budget is exhausted");
        result.bytes.resize(new_size);
        if(result.descriptor.raw_fallback) {
            if(encoded_frame_size != decoded_frame_size) throw std::runtime_error("raw fallback frame size mismatch");
            std::memcpy(result.bytes.data() + old_size, payload.data(), payload.size());
        } else {
            const auto written = ZSTD_decompress(result.bytes.data() + old_size, decoded_frame_size,
                                                 payload.data(), payload.size());
            if(ZSTD_isError(written) || written != decoded_frame_size)
                throw std::runtime_error("framed zstd decompression failed");
        }
        decoded_cursor += decoded_frame_size;
    }
    if(position != encoded_bytes.size() || decoded_cursor != decoded_size || result.bytes.size() != decoded_size)
        throw std::runtime_error("framed zstd payload has trailing or missing bytes");
    result.decoded_digest = digest_for(result.bytes);
    if(result.decoded_digest.value != declared_digest.value)
        throw std::runtime_error("framed zstd decoded digest mismatch");
    result.artifact_id = artifact_id_for_digest(result.decoded_digest);
    return result;
#endif
}

FramedZstdRange decode_framed_zstd_range(std::string_view encoded_bytes,
                                         std::uint64_t offset,
                                         std::uint64_t length,
                                         FramedZstdOptions options) {
    validate_options(options);
#if !AGENT_MEMORY_ENABLE_ZSTD
    static_cast<void>(encoded_bytes); static_cast<void>(offset); static_cast<void>(length);
    throw std::runtime_error("framed zstd support is disabled");
#else
    std::size_t position = 0;
    if(encoded_bytes.size() < kMagic.size() || encoded_bytes.substr(0, kMagic.size()) != kMagic)
        throw std::runtime_error("framed zstd magic is invalid");
    position += kMagic.size();
    const auto version = read_u8(encoded_bytes, position);
    const auto flags = read_u8(encoded_bytes, position);
    if(version != 1U || (flags & ~kRawFlag) != 0U) throw std::runtime_error("framed zstd descriptor is unsupported");
    const auto frame_bytes = read_u32(encoded_bytes, position);
    const auto level = static_cast<int>(read_u8(encoded_bytes, position));
    if(encoded_bytes.size() - position < 3U) throw std::runtime_error("framed zstd descriptor is truncated");
    const auto reserved0 = read_u8(encoded_bytes, position);
    const auto reserved1 = read_u8(encoded_bytes, position);
    const auto reserved2 = read_u8(encoded_bytes, position);
    if(reserved0 != 0U || reserved1 != 0U || reserved2 != 0U)
        throw std::runtime_error("framed zstd reserved descriptor bytes are non-zero");
    const auto decoded_size = read_u64(encoded_bytes, position);
    const auto frame_count = read_u64(encoded_bytes, position);
    const auto declared_digest = read_digest(encoded_bytes, position);
    if(frame_bytes == 0 || frame_bytes > options.max_frame_bytes || level < 1 || level > 22 ||
       decoded_size > options.max_decoded_bytes ||
       decoded_size > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
        throw std::runtime_error("framed zstd descriptor exceeds budget");
    const auto expected_count = expected_frame_count(decoded_size, frame_bytes);
    if(frame_count != expected_count || offset > decoded_size || length > decoded_size - offset)
        throw std::runtime_error("framed zstd range or frame count is invalid");
    FramedZstdRange result;
    result.declared_artifact_id = artifact_id_for_digest(declared_digest);
    result.verified_artifact_id.reset();
    result.descriptor = descriptor_from(frame_bytes, level, (flags & kRawFlag) != 0U);
    result.offset = offset;
    result.bytes.reserve(static_cast<std::size_t>(length));
    std::uint64_t requested_end = 0;
    if(!checked_add(offset, length, requested_end))
        throw std::runtime_error("framed zstd range overflows");
    std::uint64_t decoded_cursor = 0;
    for(std::uint64_t frame = 0; frame < frame_count; ++frame) {
        const auto decoded_frame_size = read_u32(encoded_bytes, position);
        const auto encoded_frame_size = read_u32(encoded_bytes, position);
        const auto expected_size = static_cast<std::uint64_t>(
            std::min<std::uint64_t>(frame_bytes, decoded_size - decoded_cursor));
        if(decoded_frame_size == 0 || decoded_frame_size != expected_size ||
           encoded_frame_size > options.max_frame_bytes || position > encoded_bytes.size() ||
           encoded_frame_size > encoded_bytes.size() - position)
            throw std::runtime_error("framed zstd frame is invalid");
        const auto payload = encoded_bytes.substr(position, encoded_frame_size);
        position += encoded_frame_size;
        const auto frame_end = decoded_cursor + decoded_frame_size;
        const auto overlap_begin = std::max(offset, decoded_cursor);
        const auto overlap_end = std::min(requested_end, frame_end);
        if(overlap_begin < overlap_end) {
            std::string frame_bytes_out;
            frame_bytes_out.resize(decoded_frame_size);
            if(result.descriptor.raw_fallback) {
                if(encoded_frame_size != decoded_frame_size)
                    throw std::runtime_error("raw fallback frame size mismatch");
                std::memcpy(frame_bytes_out.data(), payload.data(), payload.size());
            } else {
                const auto written = ZSTD_decompress(frame_bytes_out.data(), frame_bytes_out.size(),
                                                     payload.data(), payload.size());
                if(ZSTD_isError(written) || written != decoded_frame_size)
                    throw std::runtime_error("framed zstd decompression failed");
            }
            const auto local_offset = static_cast<std::size_t>(overlap_begin - decoded_cursor);
            const auto local_length = static_cast<std::size_t>(overlap_end - overlap_begin);
            result.bytes.append(frame_bytes_out.data() + local_offset, local_length);
        }
        decoded_cursor = frame_end;
    }
    if(position != encoded_bytes.size() || decoded_cursor != decoded_size || result.bytes.size() != length)
        throw std::runtime_error("framed zstd payload has trailing or missing bytes");
    return result;
#endif
}

} // namespace agent_memory
