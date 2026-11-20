#pragma once
#ifndef AGENT_MEMORY_HEADER_RESEARCH_RERANKER_RESEARCH_PROFILE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RESEARCH_RERANKER_RESEARCH_PROFILE_HPP_INCLUDED

#include <array>
#include <chrono>
#include <cstddef>

namespace agent_memory_research {

    enum class AdmissionStage : std::size_t {
        IndexSearch,
        TrustedPoolCopy,
        Reranker,
        AdmissionTotal,
        LookupConstruction,
        ValidationAndLookup,
        TrustedResultCopy,
        ResponseMaterialization,
        Count
    };

    struct AdmissionProfile final {
        std::array<double, static_cast<std::size_t>(AdmissionStage::Count)> milliseconds{};
        std::array<std::size_t, static_cast<std::size_t>(AdmissionStage::Count)> calls{};
    };

    // One executable owns these research-only counters; separate reads do not share state.
    inline thread_local AdmissionProfile admission_profile;

    template <bool Enabled = true>
    class AdmissionStageTimer final {
    public:
        explicit AdmissionStageTimer(const AdmissionStage stage) noexcept
            : m_stage(stage) {
            if constexpr(Enabled) {
                m_started = Clock::now();
            }
        }

        ~AdmissionStageTimer() noexcept {
            finish();
        }

        void finish() noexcept {
            if constexpr(Enabled) {
                if(!m_active) {
                    return;
                }
                const auto index = static_cast<std::size_t>(m_stage);
                admission_profile.milliseconds[index] += std::chrono::duration<double, std::milli>(
                    Clock::now() - m_started
                ).count();
                ++admission_profile.calls[index];
                m_active = false;
            }
        }

        AdmissionStageTimer(const AdmissionStageTimer&) = delete;
        AdmissionStageTimer& operator=(const AdmissionStageTimer&) = delete;

    private:
        using Clock = std::chrono::steady_clock;
        AdmissionStage m_stage;
        Clock::time_point m_started{};
        bool m_active = Enabled;
    };

} // namespace agent_memory_research

#endif
