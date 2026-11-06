#include <agent_memory/storage/FramedZstdCodec.hpp>

#include <algorithm>
#include <iostream>
#include <stdexcept>

namespace {
void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
}

int main() {
    using namespace agent_memory;
    const std::string body = "repeated source bytes repeated source bytes\n";
    FramedZstdOptions options;
    options.frame_bytes = 8;
    options.max_decoded_bytes = 1024;
    options.max_frame_bytes = 1024;
    const auto first = encode_framed_zstd(body, false, options);
    const auto second = encode_framed_zstd(body, false, options);
    expect(first.bytes == second.bytes, "framed encoding was not deterministic");
    expect(first.artifact_id == second.artifact_id, "logical artifact identity changed on re-encode");
    const auto decoded = decode_framed_zstd(first.bytes, options);
    expect(decoded.bytes == body, "framed zstd round-trip changed decoded bytes");
    expect(decoded.artifact_id == first.artifact_id, "decoded artifact identity changed");
    const auto raw = encode_framed_zstd(body, true, options);
    expect(raw.artifact_id == first.artifact_id, "raw fallback changed logical artifact identity");
    expect(raw.descriptor.raw_fallback, "raw fallback descriptor was not marked");
    expect(decode_framed_zstd(raw.bytes, options).bytes == body, "raw fallback did not round-trip");

    auto truncated = first.bytes;
    truncated.pop_back();
    bool rejected = false;
    try { static_cast<void>(decode_framed_zstd(truncated, options)); }
    catch(const std::runtime_error&) { rejected = true; }
    expect(rejected, "truncated frame was accepted");
    auto corrupted = first.bytes;
    corrupted[corrupted.size() - 1U] ^= 0x01;
    rejected = false;
    try { static_cast<void>(decode_framed_zstd(corrupted, options)); }
    catch(const std::runtime_error&) { rejected = true; }
    expect(rejected, "corrupted frame was accepted");
    std::cout << "framed zstd codec: PASS\n";
    return 0;
}
