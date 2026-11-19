#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitNumericKeyPilot.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <iostream>
#include <stdexcept>

namespace {

using namespace agent_memory;

[[noreturn]] void fail(const char* message) { throw std::runtime_error(message); }
void expect(bool condition, const char* message) { if(!condition) fail(message); }

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path() /
            "agent_memory_numeric_key_pilot_test.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);

        MdbxKnowledgeUnitNumericKeyPilotOptions options;
        options.path = path.string();
        options.table_prefix = "numeric pilot";
        {
            MdbxKnowledgeUnitNumericKeyPilot pilot(options);

            bool zero_rejected = false;
            try {
                pilot.put(KnowledgeUnitId{}, "reserved");
            } catch(const std::invalid_argument&) {
                zero_rejected = true;
            }
            expect(zero_rejected, "zero numeric identity was accepted");

            pilot.put(KnowledgeUnitId{10}, "ten");
            pilot.put(KnowledgeUnitId{2}, "two");
            pilot.put(KnowledgeUnitId{5}, "five");
            expect(pilot.find(KnowledgeUnitId{10}) == std::optional<std::string>{"ten"},
                   "numeric point lookup failed");

            const auto ordered = pilot.scan(KnowledgeUnitId{1}, KnowledgeUnitId{10});
            expect(ordered.size() == 3U && ordered[0].value() == 2U &&
                       ordered[1].value() == 5U && ordered[2].value() == 10U,
                   "integer-key range order was not numeric");

            expect(pilot.erase(KnowledgeUnitId{5}), "numeric row was not erased");
            bool reuse_rejected = false;
            try {
                pilot.put(KnowledgeUnitId{5}, "reused");
            } catch(const std::runtime_error&) {
                reuse_rejected = true;
            }
            expect(reuse_rejected, "erased numeric identity was reused");
        }

        MdbxKnowledgeUnitNumericKeyPilot reopened(options);
        expect(reopened.find(KnowledgeUnitId{2}) == std::optional<std::string>{"two"},
               "numeric key did not survive reopen");
        expect(!reopened.find(KnowledgeUnitId{5}),
               "erased numeric row reappeared after reopen");

        std::filesystem::remove(path, error);
        std::cout << "mdbx numeric knowledge-unit key pilot: PASS\n";
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
