#include <agent_memory.hpp>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr std::size_t kCodeBits = 256;
constexpr std::size_t kWordCount = kCodeBits / 64;

struct Band final {
    std::size_t offset = 0;
    std::size_t width = 0;
};

struct Entry final {
    std::uint32_t key = 0;
    std::uint32_t position = 0;
};

struct Directory final {
    std::vector<std::uint32_t> keys;
    std::vector<std::uint32_t> offsets;
    std::vector<std::uint32_t> postings;
};

[[nodiscard]] std::uint32_t extract_key(const std::uint64_t* code, const Band& band) {
    if(band.width == 0 || band.width > 32 || band.offset + band.width > kCodeBits) {
        throw std::invalid_argument("native sparse MIH band is invalid");
    }
    const auto word = band.offset / 64;
    const auto shift = band.offset % 64;
    std::uint64_t value = code[word] >> shift;
    if(shift + band.width > 64) {
        value |= code[word + 1] << (64 - shift);
    }
    const auto mask = band.width == 32 ? std::numeric_limits<std::uint32_t>::max() : (std::uint32_t{1} << band.width) - 1U;
    return static_cast<std::uint32_t>(value) & mask;
}

class SparseIndex final {
public:
    SparseIndex(const std::vector<std::uint64_t>& codes, std::size_t document_count, std::vector<Band> bands)
        : m_bands(std::move(bands)), m_directories(m_bands.size()) {
        if(document_count == 0 || codes.size() != document_count * kWordCount || m_bands.empty()) {
            throw std::invalid_argument("native sparse MIH input is invalid");
        }
        std::size_t expected_offset = 0;
        for(const auto& band : m_bands) {
            if(band.offset != expected_offset || band.width == 0 || band.width > 32) {
                throw std::invalid_argument("native sparse MIH band partition is invalid");
            }
            expected_offset += band.width;
        }
        if(expected_offset != kCodeBits) throw std::invalid_argument("native sparse MIH band coverage differs");
        for(std::size_t band_index = 0; band_index < m_bands.size(); ++band_index) {
            std::vector<Entry> entries;
            entries.reserve(document_count);
            for(std::size_t position = 0; position < document_count; ++position) {
                entries.push_back({extract_key(codes.data() + position * kWordCount, m_bands[band_index]), static_cast<std::uint32_t>(position)});
            }
            std::sort(entries.begin(), entries.end(), [](const Entry& left, const Entry& right) { return left.key == right.key ? left.position < right.position : left.key < right.key; });
            auto& directory = m_directories[band_index];
            for(const auto& entry : entries) {
                if(directory.keys.empty() || directory.keys.back() != entry.key) {
                    directory.keys.push_back(entry.key);
                    directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size()));
                }
                directory.postings.push_back(entry.position);
            }
            directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size()));
        }
    }

    [[nodiscard]] const std::vector<std::uint32_t>* find(std::size_t band_index, std::uint32_t key, std::size_t& first, std::size_t& last) const {
        const auto& directory = m_directories.at(band_index);
        const auto found = std::lower_bound(directory.keys.begin(), directory.keys.end(), key);
        if(found == directory.keys.end() || *found != key) return nullptr;
        const auto index = static_cast<std::size_t>(found - directory.keys.begin());
        first = directory.offsets[index];
        last = directory.offsets[index + 1];
        return &directory.postings;
    }

    [[nodiscard]] std::size_t logical_bytes() const noexcept {
        std::size_t result = 0;
        for(const auto& directory : m_directories) result += directory.keys.size() * sizeof(std::uint32_t) + directory.offsets.size() * sizeof(std::uint32_t) + directory.postings.size() * sizeof(std::uint32_t);
        return result;
    }

private:
    std::vector<Band> m_bands;
    std::vector<Directory> m_directories;
};

class GenerationDeduplicator final {
public:
    explicit GenerationDeduplicator(std::size_t document_count) : m_generation(document_count, 0) {}

    void next_query() {
        if(m_current == std::numeric_limits<std::uint32_t>::max()) {
            std::fill(m_generation.begin(), m_generation.end(), 0);
            m_current = 1;
        } else {
            ++m_current;
        }
    }

    [[nodiscard]] bool visit(std::uint32_t position) noexcept {
        if(m_generation[position] == m_current) return false;
        m_generation[position] = m_current;
        return true;
    }

private:
    std::vector<std::uint32_t> m_generation;
    std::uint32_t m_current = 0;
};

[[nodiscard]] int self_test() {
    try {
        std::vector<std::uint64_t> codes(3 * kWordCount, 0);
        codes[kWordCount] = 1U << 17;
        codes[2 * kWordCount] = (std::uint64_t{1} << 63) | 1U;
        const std::vector<Band> bands{{0, 18}, {18, 17}, {35, 17}, {52, 17}, {69, 17}, {86, 17}, {103, 17}, {120, 17}, {137, 17}, {154, 17}, {171, 17}, {188, 17}, {205, 17}, {222, 17}, {239, 17}};
        const SparseIndex index(codes, 3, bands);
        std::size_t first = 0, last = 0;
        const auto* postings = index.find(0, 0, first, last);
        if(postings == nullptr || last - first != 2 || (*postings)[first] != 0 || (*postings)[first + 1] != 1 || index.logical_bytes() == 0) throw std::runtime_error("sparse directory lookup differs");
        GenerationDeduplicator dedup(3); dedup.next_query();
        if(!dedup.visit(1) || dedup.visit(1)) throw std::runtime_error("generation deduplication differs");
    } catch(const std::exception& error) {
        std::cerr << "native sparse arbitrary-m MIH self-test failed: " << error.what() << '\n';
        return 1;
    }
    std::cout << "native sparse arbitrary-m MIH self-test passed\n";
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    if(argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    std::cerr << "usage: agent-memory-mih-native-sparse-arbitrary-m --self-test\n";
    return 2;
}
