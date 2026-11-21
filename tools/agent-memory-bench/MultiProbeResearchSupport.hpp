#ifndef AGENT_MEMORY_HEADER_TOOLS_MULTI_PROBE_RESEARCH_SUPPORT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_TOOLS_MULTI_PROBE_RESEARCH_SUPPORT_HPP_INCLUDED

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace agent_memory::multiprobe_research {

// Query-local only: no record-position state survives an index mutation.
class CompactVisited final {
public:
    bool insert(std::size_t position) {
        if(position == empty()) throw std::invalid_argument("reserved visited position");
        if(slots_.empty()) grow();
        auto slot = locate(slots_, position);
        if(slots_[slot] == position) return false;
        if(size_ == slots_.size() / 2) {
            grow();
            slot = locate(slots_, position);
        }
        slots_[slot] = position;
        ++size_;
        return true;
    }
    std::size_t capacity() const noexcept { return slots_.capacity(); }
    std::size_t size() const noexcept { return size_; }

private:
    static constexpr std::size_t empty() noexcept { return std::numeric_limits<std::size_t>::max(); }
    static std::uint64_t hash(std::size_t value) noexcept {
        auto word = static_cast<std::uint64_t>(value) + UINT64_C(0x9e3779b97f4a7c15);
        word = (word ^ (word >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
        word = (word ^ (word >> 27)) * UINT64_C(0x94d049bb133111eb);
        return word ^ (word >> 31);
    }
    static std::size_t locate(const std::vector<std::size_t>& slots, std::size_t position) noexcept {
        auto index = static_cast<std::size_t>(hash(position)) & (slots.size() - 1);
        while(slots[index] != empty() && slots[index] != position) index = (index + 1) & (slots.size() - 1);
        return index;
    }
    void grow() {
        if(slots_.size() > slots_.max_size() / 2) throw std::length_error("visited capacity overflow");
        const auto next_size = slots_.empty() ? 16 : slots_.size() * 2;
        std::vector<std::size_t> next(next_size, empty());
        for(const auto position : slots_) if(position != empty()) next[locate(next, position)] = position;
        slots_.swap(next);
    }
    std::vector<std::size_t> slots_;
    std::size_t size_ = 0;
};

#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)
struct Trace final {
    std::vector<std::pair<std::size_t, std::uint64_t>> buckets;
    std::vector<std::size_t> postings;
    std::vector<std::size_t> first_seen;
    std::vector<std::size_t> accepted;
    std::vector<std::pair<std::size_t, std::size_t>> distances;
    std::vector<std::pair<std::size_t, std::size_t>> radii;
    std::size_t visited_capacity = 0;
    std::size_t visited_payload_lower_bound = 0;
};
inline thread_local Trace* active_trace = nullptr;
inline void trace_bucket(std::size_t table, std::uint64_t key) {
    if(active_trace) active_trace->buckets.emplace_back(table, key);
}
inline void trace_posting(std::size_t position) {
    if(active_trace) active_trace->postings.push_back(position);
}
inline void trace_first(std::size_t position) {
    if(active_trace) active_trace->first_seen.push_back(position);
}
inline void trace_accepted(std::size_t position) {
    if(active_trace) active_trace->accepted.push_back(position);
}
inline void trace_distance(std::size_t position, std::size_t distance) {
    if(active_trace) active_trace->distances.emplace_back(position, distance);
}
inline void trace_radius(std::size_t radius, std::size_t accepted) {
    if(active_trace) active_trace->radii.emplace_back(radius, accepted);
}
inline void trace_memory(std::size_t capacity, std::size_t lower_bound) {
    if(active_trace) {
        active_trace->visited_capacity = capacity;
        active_trace->visited_payload_lower_bound = lower_bound;
    }
}
#endif
} // namespace agent_memory::multiprobe_research

#endif
