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
    const auto range = decode_framed_zstd_range(first.bytes, 3, 11, options);
    expect(range.bytes == body.substr(3, 11), "framed range decode returned wrong bytes");
    expect(range.artifact_id == first.artifact_id, "range decode lost logical identity");

    const auto empty = encode_framed_zstd("", false, options);
    expect(decode_framed_zstd(empty.bytes, options).bytes.empty(), "empty payload did not round-trip");
    FramedZstdOptions tiny_budget = options;
    tiny_budget.max_decoded_bytes = 1;
    bool budget_rejected = false;
    try { static_cast<void>(decode_framed_zstd(first.bytes, tiny_budget)); }
    catch(const std::runtime_error&) { budget_rejected = true; }
    expect(budget_rejected, "decoded output budget was not enforced");

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
    auto oversized_descriptor = first.bytes;
    // The declared logical body becomes one byte while the first frame still
    // claims its original decoded size; the decoder must reject before resize.
    oversized_descriptor[21] = 1;
    rejected = false;
    try { static_cast<void>(decode_framed_zstd(oversized_descriptor, options)); }
    catch(const std::runtime_error&) { rejected = true; }
    expect(rejected, "oversized frame descriptor was accepted");
    std::cout << "framed zstd codec: PASS\n";
    return 0;
}
