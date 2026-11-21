#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitNumericKeyPilot.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

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

        const auto markerless_path = std::filesystem::temp_directory_path() /
            "agent_memory_numeric_key_pilot_markerless_test.mdbx";
        std::filesystem::remove(markerless_path, error);
        {
            auto context = MdbxStorageContext::open(markerless_path.string());
            mdbxc::KeyValueTable<std::uint64_t, std::string> records(
                context->connection(), "agent_memory_knowledge_units_numeric_v1");
            mdbxc::KeyValueTable<std::uint64_t, std::string> used_ids(
                context->connection(), "agent_memory_knowledge_units_numeric_v1_used");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            records.insert_or_assign(7U, "markerless", txn);
            used_ids.insert_or_assign(7U, "used", txn);
            txn.commit();
        }
        bool markerless_rejected = false;
        try {
            MdbxKnowledgeUnitNumericKeyPilotOptions markerless_options;
            markerless_options.path = markerless_path.string();
            static_cast<void>(MdbxKnowledgeUnitNumericKeyPilot(markerless_options));
        } catch(const std::runtime_error&) {
            markerless_rejected = true;
        }
        expect(markerless_rejected, "markerless non-empty pilot tables were accepted");
        std::filesystem::remove(markerless_path, error);

        const auto corrupt_path = std::filesystem::temp_directory_path() /
            "agent_memory_numeric_key_pilot_corrupt_ledger_test.mdbx";
        std::filesystem::remove(corrupt_path, error);
        MdbxKnowledgeUnitNumericKeyPilotOptions corrupt_options;
        corrupt_options.path = corrupt_path.string();
        {
            MdbxKnowledgeUnitNumericKeyPilot corrupt_pilot(corrupt_options);
            corrupt_pilot.put(KnowledgeUnitId{11}, "eleven");
        }
        {
            auto context = MdbxStorageContext::open(corrupt_path.string());
            mdbxc::KeyValueTable<std::uint64_t, std::string> used_ids(
                context->connection(), "agent_memory_knowledge_units_numeric_v1_used");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            used_ids.insert_or_assign(11U, "forged", txn);
            txn.commit();
        }
        {
            MdbxKnowledgeUnitNumericKeyPilot corrupt_pilot(corrupt_options);
            bool corruption_rejected = false;
            try {
                static_cast<void>(corrupt_pilot.find(KnowledgeUnitId{11}));
            } catch(const std::runtime_error&) {
                corruption_rejected = true;
            }
            expect(corruption_rejected, "forged ledger marker was accepted by find");
            corruption_rejected = false;
            try {
                static_cast<void>(corrupt_pilot.scan(KnowledgeUnitId{1}, KnowledgeUnitId{20}));
            } catch(const std::runtime_error&) {
                corruption_rejected = true;
            }
            expect(corruption_rejected, "forged ledger marker was accepted by scan");
        }
        std::filesystem::remove(corrupt_path, error);

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
