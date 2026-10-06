#include "CanonicalContent.hpp"

#include <utility>

namespace agent_memory {

    ContentBlockId::ContentBlockId(std::string value)
        : m_value(std::move(value)) {}

    const std::string& ContentBlockId::value() const noexcept {
        return m_value;
    }

    bool ContentBlockId::empty() const noexcept {
        return m_value.empty();
    }

    bool operator==(const ContentBlockId& lhs, const ContentBlockId& rhs) noexcept {
        return lhs.value() == rhs.value();
    }

    bool operator!=(const ContentBlockId& lhs, const ContentBlockId& rhs) noexcept {
        return !(lhs == rhs);
    }

    bool operator<(const ContentBlockId& lhs, const ContentBlockId& rhs) noexcept {
        return lhs.value() < rhs.value();
    }

    bool CanonicalEditResult::succeeded() const noexcept {
        return status == CanonicalEditStatus::Ok || status == CanonicalEditStatus::NoChange;
    }

} // namespace agent_memory
