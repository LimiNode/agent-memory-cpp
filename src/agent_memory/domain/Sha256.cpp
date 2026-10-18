#include "Sha256.hpp"

#include <array>

namespace agent_memory {
namespace {
constexpr std::array<std::uint32_t, 64> K{{
    0x428A2F98U,0x71374491U,0xB5C0FBCFU,0xE9B5DBA5U,0x3956C25BU,0x59F111F1U,0x923F82A4U,0xAB1C5ED5U,
    0xD807AA98U,0x12835B01U,0x243185BEU,0x550C7DC3U,0x72BE5D74U,0x80DEB1FEU,0x9BDC06A7U,0xC19BF174U,
    0xE49B69C1U,0xEFBE4786U,0x0FC19DC6U,0x240CA1CCU,0x2DE92C6FU,0x4A7484AAU,0x5CB0A9DCU,0x76F988DAU,
    0x983E5152U,0xA831C66DU,0xB00327C8U,0xBF597FC7U,0xC6E00BF3U,0xD5A79147U,0x06CA6351U,0x14292967U,
    0x27B70A85U,0x2E1B2138U,0x4D2C6DFCU,0x53380D13U,0x650A7354U,0x766A0ABBU,0x81C2C92EU,0x92722C85U,
    0xA2BFE8A1U,0xA81A664BU,0xC24B8B70U,0xC76C51A3U,0xD192E819U,0xD6990624U,0xF40E3585U,0x106AA070U,
    0x19A4C116U,0x1E376C08U,0x2748774CU,0x34B0BCB5U,0x391C0CB3U,0x4ED8AA4AU,0x5B9CCA4FU,0x682E6FF3U,
    0x748F82EEU,0x78A5636FU,0x84C87814U,0x8CC70208U,0x90BEFFFAU,0xA4506CEBU,0xBEF9A3F7U,0xC67178F2U
}};
constexpr std::uint32_t rotr(std::uint32_t x, unsigned n) { return (x >> n) | (x << (32U - n)); }
}

std::array<std::uint8_t, 32> sha256(std::string_view input) noexcept {
    std::array<std::uint32_t, 8> h{{0x6A09E667U,0xBB67AE85U,0x3C6EF372U,0xA54FF53AU,0x510E527FU,0x9B05688CU,0x1F83D9ABU,0x5BE0CD19U}};
    const std::uint64_t bit_count = static_cast<std::uint64_t>(input.size()) * 8U;
    const std::size_t total = ((input.size() + 9U + 63U) / 64U) * 64U;
    const std::size_t pad_offset = input.size();
    for(std::size_t offset = 0; offset < total; offset += 64U) {
        std::array<std::uint8_t, 64> block{};
        for(std::size_t i = 0; i < 64U && offset + i < input.size(); ++i) block[i] = static_cast<std::uint8_t>(input[offset + i]);
        if(pad_offset >= offset && pad_offset < offset + 64U) {
            block[pad_offset - offset] = 0x80U;
        }
        if(offset == total - 64U) {
            for(unsigned i = 0; i < 8U; ++i) {
                block[56U + i] = static_cast<std::uint8_t>(bit_count >> (56U - i * 8U));
            }
        }
        std::array<std::uint32_t, 64> w{};
        for(unsigned i = 0; i < 16U; ++i) w[i] = (static_cast<std::uint32_t>(block[i*4])<<24U)|(static_cast<std::uint32_t>(block[i*4+1])<<16U)|(static_cast<std::uint32_t>(block[i*4+2])<<8U)|block[i*4+3];
        for(unsigned i = 16U; i < 64U; ++i) { const auto s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>3U); const auto s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>10U); w[i]=w[i-16]+s0+w[i-7]+s1; }
        auto a=h[0]; auto b=h[1]; auto c=h[2]; auto d=h[3]; auto e=h[4]; auto f=h[5]; auto g=h[6]; auto x=h[7];
        for(unsigned i=0;i<64U;++i){const auto s1=rotr(e,6)^rotr(e,11)^rotr(e,25);const auto ch=(e&f)^(~e&g);const auto t1=x+s1+ch+K[i]+w[i];const auto s0=rotr(a,2)^rotr(a,13)^rotr(a,22);const auto maj=(a&b)^(a&c)^(b&c);const auto t2=s0+maj;x=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;}
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=x;
    }
    std::array<std::uint8_t,32> out{}; for(unsigned i=0;i<8U;++i){out[i*4]=static_cast<std::uint8_t>(h[i]>>24U);out[i*4+1]=static_cast<std::uint8_t>(h[i]>>16U);out[i*4+2]=static_cast<std::uint8_t>(h[i]>>8U);out[i*4+3]=static_cast<std::uint8_t>(h[i]);} return out;
}
} // namespace agent_memory
