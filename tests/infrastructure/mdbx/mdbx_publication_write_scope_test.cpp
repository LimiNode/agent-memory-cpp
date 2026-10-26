#include <agent_memory/infrastructure/mdbx/MdbxPublicationWriteScope.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <filesystem>
#include <future>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>

namespace {

using agent_memory::MdbxPublicationParticipant;
using agent_memory::MdbxPublicationWriteScope;
using agent_memory::MdbxStorageContext;

int fail(std::string_view message) {
    std::cerr << message << '\n';
    return 1;
}

std::filesystem::path test_path() {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path()
        / ("agent_memory_publication_scope_" + std::to_string(stamp) + ".mdbx");
}

class RowParticipant final : public MdbxPublicationParticipant {
public:
    RowParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        std::string key,
        std::string value
    ) : m_context(std::move(context)),
        m_table(m_context->connection(), "publication_rows"),
        m_key(std::move(key)),
        m_value(std::move(value)) {}

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override {
        return *m_context;
    }

    void apply(const mdbxc::Transaction& transaction) override {
        m_table.insert_or_assign(m_key, m_value, transaction);
    }

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    mdbxc::KeyValueTable<std::string, std::string> m_table;
    std::string m_key;
    std::string m_value;
};

class ThrowingParticipant final : public MdbxPublicationParticipant {
public:
    explicit ThrowingParticipant(std::shared_ptr<MdbxStorageContext> context)
        : m_context(std::move(context)),
          m_table(m_context->connection(), "publication_rows") {}

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override {
        return *m_context;
    }

    void apply(const mdbxc::Transaction& transaction) override {
        m_table.insert_or_assign("transient", "must-rollback", transaction);
        throw std::runtime_error("injected participant failure");
    }

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    mdbxc::KeyValueTable<std::string, std::string> m_table;
};

std::optional<std::string> read_value(
    const std::shared_ptr<MdbxStorageContext>& context,
    const std::string& key
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(),
        "publication_rows"
    );
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto value = table.find(key, transaction);
    transaction.commit();
    return value;
}

} // namespace

int main() {
    const auto path = test_path();
    std::error_code cleanup_error;

    auto context = MdbxStorageContext::open(path.string(), false, 32);
    {
    RowParticipant first{context, "first", "one"};
    RowParticipant second{context, "second", "two"};

    {
        auto scope = MdbxPublicationWriteScope::begin(context);
        scope.apply(first);
        scope.apply(second);
        scope.commit();
    }

    {
        auto reader_context = MdbxStorageContext::attach(context->connection());
        std::promise<void> reader_ready;
        auto reader_ready_future = reader_ready.get_future();
        std::promise<bool> reader_result;
        auto reader_result_future = reader_result.get_future();
        std::promise<void> release_reader;
        auto release_future = release_reader.get_future().share();
        std::thread reader([reader_context,
                            ready = std::move(reader_ready),
                            result = std::move(reader_result),
                            release_future = std::move(release_future)]() mutable {
            mdbxc::KeyValueTable<std::string, std::string> reader_table(
                reader_context->connection(),
                "publication_rows"
            );
            auto reader_transaction = reader_context->connection()->transaction(
                mdbxc::TransactionMode::READ_ONLY
            );
            ready.set_value();
            release_future.wait();
            const auto old_view = reader_table.find("first", reader_transaction)
                == std::optional<std::string>{"one"};
            reader_transaction.commit();
            result.set_value(old_view);
        });
        reader_ready_future.wait();
        RowParticipant replacement{context, "first", "updated"};
        auto scope = MdbxPublicationWriteScope::begin(context);
        scope.apply(replacement);
        scope.commit();
        release_reader.set_value();
        reader.join();
        if(!reader_result_future.get()) {
            return fail("an old pinned reader must retain the prior committed view");
        }
        if(read_value(context, "first") != std::optional<std::string>{"updated"}) {
            return fail("a new reader must observe the committed replacement");
        }
    }

    {
        auto scope = MdbxPublicationWriteScope::begin(context);
        scope.apply(first);
        scope.apply(second);
        if(!scope.active() || !scope.on_owner_thread()) {
            return fail("write scope must remain active and owner-bound before commit");
        }
        scope.commit();
        if(scope.active()) {
            return fail("commit must finish the coordinator-owned scope");
        }
        try {
            scope.commit();
            return fail("double commit must be rejected");
        } catch(const std::logic_error&) {
        }
    }

    if(read_value(context, "first") != std::optional<std::string>{"one"}
        || read_value(context, "second") != std::optional<std::string>{"two"}) {
        return fail("all participant writes must become visible in one commit");
    }

    {
        ThrowingParticipant failing{context};
        auto scope = MdbxPublicationWriteScope::begin(context);
        try {
            scope.apply(failing);
            return fail("participant failure must propagate");
        } catch(const std::runtime_error&) {
            scope.abort();
        }
        try {
            scope.abort();
            return fail("double abort must be rejected");
        } catch(const std::logic_error&) {
        }
        if(scope.active() || read_value(context, "transient").has_value()) {
            return fail("participant failure must roll back every write");
        }
    }

    {
        auto scope = MdbxPublicationWriteScope::begin(context);
        try {
            auto nested = MdbxPublicationWriteScope::begin(context);
            (void)nested;
            return fail("nested writable publication scopes must be rejected");
        } catch(const std::logic_error&) {
        }
        bool rejected = false;
        std::thread worker([&scope, &rejected] {
            try {
                scope.commit();
            } catch(const std::logic_error&) {
                rejected = true;
            }
        });
        worker.join();
        if(!rejected || !scope.active()) {
            return fail("cross-thread transaction use must be rejected");
        }
        scope.abort();
    }

    auto other_context = MdbxStorageContext::open(
        (path.string() + ".other"),
        false,
        32
    );
    RowParticipant foreign{other_context, "foreign", "must-reject"};
    {
        auto scope = MdbxPublicationWriteScope::begin(context);
        try {
            scope.apply(foreign);
            return fail("participant from another context must be rejected");
    } catch(const std::invalid_argument&) {
            scope.abort();
        }
    }
    }

    context.reset();
    auto reopened = MdbxStorageContext::open(path.string(), false, 32);
    if(read_value(reopened, "first") != std::optional<std::string>{"one"}
        || read_value(reopened, "second") != std::optional<std::string>{"two"}) {
        return fail("committed participant rows must survive reopen");
    }
    reopened.reset();
    std::filesystem::remove(path, cleanup_error);
    std::filesystem::remove(path.string() + ".other", cleanup_error);
    return 0;
}

#else

int main() {
    return 0;
}

#endif
