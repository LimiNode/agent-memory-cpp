#include <agent_memory/infrastructure/mdbx/MdbxPromptAssemblyProfileStorage.hpp>

#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>

#ifndef AGENT_MEMORY_HAS_MDBX
#   error "AGENT_MEMORY_HAS_MDBX must be defined by the agent_memory target"
#endif

#if !AGENT_MEMORY_HAS_MDBX
#   error "Prompt profile storage test requires AGENT_MEMORY_ENABLE_MDBX=ON"
#endif

namespace {

int fail(std::string_view message) {
    std::cerr << message << '\n';
    return 1;
}

std::filesystem::path database_path() {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        ("agent_memory_prompt_profiles_" + std::to_string(stamp) + "_" +
         std::to_string(std::random_device{}()) + ".mdbx");
}

class Cleanup final {
public:
    explicit Cleanup(std::filesystem::path path) : path_(std::move(path)) {}
    ~Cleanup() {
        std::error_code error;
        std::filesystem::remove(path_, error);
    }
    [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }
private:
    std::filesystem::path path_;
};

void append_u64(std::string& value, std::uint64_t number) {
    for(unsigned shift = 0U; shift < 64U; shift += 8U) {
        value.push_back(static_cast<char>((number >> shift) & 0xffU));
    }
}

std::string key_for(const std::string& profile_id, std::uint64_t revision) {
    std::string key;
    append_u64(key, static_cast<std::uint64_t>(profile_id.size()));
    key += profile_id;
    append_u64(key, revision);
    return key;
}

agent_memory::PromptAssemblyProfile profile(std::uint64_t revision, std::string renderer) {
    agent_memory::PromptAssemblyProfile result;
    result.profile_id = "assistant-default";
    result.revision = revision;
    result.renderer_revision = std::move(renderer);
    result.default_enabled = false;
    result.limits.max_template_bytes = 1024U;
    result.limits.max_expansions = 8U;
    result.limits.max_result_bytes = 4096U;
    result.selections.push_back(agent_memory::PromptBlockSelection{
        agent_memory::ContentBlockId{"system"}, true, true, true, false,
        std::nullopt, std::nullopt, std::nullopt, {}, {}, -2});
    result.selections.push_back(agent_memory::PromptBlockSelection{
        agent_memory::ContentBlockId{"context"}, true, false, false, true,
        std::string{"ru"}, std::nullopt, std::nullopt, {}, {}, 0});
    result.variants.emplace("language", "ru");
    return result;
}

bool same(const agent_memory::PromptAssemblyProfile& lhs,
          const agent_memory::PromptAssemblyProfile& rhs) {
    return lhs.profile_id == rhs.profile_id && lhs.revision == rhs.revision &&
        lhs.renderer_revision == rhs.renderer_revision &&
        lhs.default_enabled == rhs.default_enabled &&
        lhs.limits.max_template_bytes == rhs.limits.max_template_bytes &&
        lhs.limits.max_expansions == rhs.limits.max_expansions &&
        lhs.limits.max_result_bytes == rhs.limits.max_result_bytes &&
        lhs.selections.size() == rhs.selections.size() &&
        lhs.selections[0].block_id == rhs.selections[0].block_id &&
        lhs.selections[0].variant_priority == rhs.selections[0].variant_priority &&
        lhs.selections[1].locale == rhs.selections[1].locale &&
        lhs.variants == rhs.variants;
}

} // namespace

int main() {
    const Cleanup cleanup{database_path()};
    const auto options = [&cleanup] {
        agent_memory::MdbxPromptAssemblyProfileStorageOptions result;
        result.path = cleanup.path().string();
        result.table_prefix = "prompt_profile_test";
        return result;
    }();

    const auto first = profile(1U, "renderer-v1");
    const auto second = profile(2U, "renderer-v2");
    {
        agent_memory::MdbxPromptAssemblyProfileStorage storage(options);
        storage.put_profile(first);
        storage.put_profile(second);
        storage.put_profile(first); // idempotent replay of the same immutable revision

        const auto old = storage.find_profile(first.profile_id, 1U);
        if(!old || !same(*old, first)) return fail("profile history was not retained");
        const auto latest = storage.find_latest_profile(first.profile_id);
        if(!latest || !same(*latest, second)) return fail("latest profile revision is incorrect");
        if(storage.find_profile(first.profile_id, 3U)) return fail("missing profile revision was found");

        auto conflict = first;
        conflict.renderer_revision = "different-renderer";
        try {
            storage.put_profile(std::move(conflict));
            return fail("same profile revision accepted a different payload");
        } catch(const std::runtime_error&) {
        }
    }

    {
        agent_memory::MdbxPromptAssemblyProfileStorage reopened(options);
        const auto old = reopened.find_profile(first.profile_id, 1U);
        const auto latest = reopened.find_latest_profile(first.profile_id);
        if(!old || !latest || old->revision != 1U || latest->revision != 2U) {
            return fail("profile history did not survive reopen");
        }
    }

    // A head is a strict binary revision value, not an arbitrary string.
    {
        {
            auto context = agent_memory::MdbxStorageContext::open(cleanup.path().string());
            mdbxc::KeyValueTable<std::string, std::string> heads(
                context->connection(), "prompt_profile_test_prompt_profile_heads");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            heads.insert_or_assign(first.profile_id, "malformed", txn);
            txn.commit();
        }
        agent_memory::MdbxPromptAssemblyProfileStorage storage(options);
        try {
            (void)storage.find_latest_profile(first.profile_id);
            return fail("malformed profile head was accepted");
        } catch(const std::runtime_error&) {
        }
    }

    // A valid payload under a different history key must not be reinterpreted.
    {
        {
            auto context = agent_memory::MdbxStorageContext::open(cleanup.path().string());
            mdbxc::KeyValueTable<std::string, std::string> rows(
                context->connection(), "prompt_profile_test_prompt_profiles");
            auto read_txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            const auto payload = rows.find(key_for(first.profile_id, 1U), read_txn);
            read_txn.commit();
            if(!payload) return fail("profile payload was not available for identity test");
            auto write_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            rows.insert_or_assign(key_for(first.profile_id, 8U), *payload, write_txn);
            write_txn.commit();
        }

        agent_memory::MdbxPromptAssemblyProfileStorage storage(options);
        try {
            (void)storage.find_profile(first.profile_id, 8U);
            return fail("profile payload/key identity mismatch was accepted");
        } catch(const std::runtime_error&) {
        }
    }

    // A readable table row with an invalid wire version must fail closed.
    {
        {
            auto context = agent_memory::MdbxStorageContext::open(cleanup.path().string());
            mdbxc::KeyValueTable<std::string, std::string> rows(
                context->connection(), "prompt_profile_test_prompt_profiles");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            rows.insert_or_assign(key_for(first.profile_id, 9U), "corrupt", txn);
            txn.commit();
        }
        agent_memory::MdbxPromptAssemblyProfileStorage storage(options);
        try {
            (void)storage.find_profile(first.profile_id, 9U);
            return fail("corrupt profile payload was accepted");
        } catch(const std::runtime_error&) {
        }
    }

    // A head pointing at a missing historical row is corruption, not absence.
    {
        {
            auto context = agent_memory::MdbxStorageContext::open(cleanup.path().string());
            mdbxc::KeyValueTable<std::string, std::string> heads(
                context->connection(), "prompt_profile_test_prompt_profile_heads");
            std::string missing_revision;
            append_u64(missing_revision, 99U);
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            heads.insert_or_assign(first.profile_id, missing_revision, txn);
            txn.commit();
        }
        agent_memory::MdbxPromptAssemblyProfileStorage storage(options);
        try {
            (void)storage.find_latest_profile(first.profile_id);
            return fail("orphaned profile head was accepted");
        } catch(const std::runtime_error&) {
        }
    }

    std::cout << "MDBX prompt assembly profile storage: PASS\n";
    return 0;
}
