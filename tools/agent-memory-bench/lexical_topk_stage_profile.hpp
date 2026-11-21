#pragma once
#ifndef AGENT_MEMORY_HEADER_TOOLS_LEXICAL_TOPK_STAGE_PROFILE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_TOOLS_LEXICAL_TOPK_STAGE_PROFILE_HPP_INCLUDED

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>

namespace agent_memory::lexical_topk_research {

    using ProfileClock = std::chrono::steady_clock;

    enum class Stage : std::size_t {
        CandidateGeneration, MetadataFiltering, Bm25Scoring, CandidateConstruction,
        TopKSelection, ResultMaterialization, FullSearch, Count
    };

    struct StageTimings final {
        std::array<std::uint64_t, static_cast<std::size_t>(Stage::Count)> elapsed_ns{};
        std::array<std::size_t, static_cast<std::size_t>(Stage::Count)> calls{};
    };

    // Private benchmark state: every thread owns its own sink. It is never
    // installed in the production class or shared with another query thread.
    inline thread_local StageTimings* g_timings = nullptr;

    class ProfileBinding final {
    public:
        explicit ProfileBinding(StageTimings& timings) noexcept : m_previous(g_timings) { g_timings = &timings; }
        ~ProfileBinding() noexcept { g_timings = m_previous; }
        ProfileBinding(const ProfileBinding&) = delete;
        ProfileBinding& operator=(const ProfileBinding&) = delete;
    private:
        StageTimings* m_previous;
    };

    inline void record_elapsed(const Stage stage, const ProfileClock::time_point started) noexcept {
        if(g_timings != nullptr) {
            const auto slot = static_cast<std::size_t>(stage);
            g_timings->elapsed_ns[slot] += static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::nanoseconds>(ProfileClock::now() - started).count()
            );
            ++g_timings->calls[slot];
        }
    }

    class ScopedStage final {
    public:
        explicit ScopedStage(const Stage stage) noexcept : m_stage(stage), m_started(ProfileClock::now()) {}
        ~ScopedStage() noexcept { record_elapsed(m_stage, m_started); }
        ScopedStage(const ScopedStage&) = delete;
        ScopedStage& operator=(const ScopedStage&) = delete;

    private:
        Stage m_stage;
        ProfileClock::time_point m_started;
    };

} // namespace agent_memory::lexical_topk_research

#endif // AGENT_MEMORY_HEADER_TOOLS_LEXICAL_TOPK_STAGE_PROFILE_HPP_INCLUDED
