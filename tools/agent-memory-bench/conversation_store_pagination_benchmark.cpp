#include <agent_memory/storage/ConversationStore.hpp>

#include <chrono>
#include <iostream>
#include <stdexcept>
#ifdef _WIN32
#include <windows.h>
#include <psapi.h>
#else
#include <sys/resource.h>
#endif

namespace am = agent_memory;
int main(int argc, char** argv) {
    if(argc != 3) return 2;
    const auto count = static_cast<std::size_t>(std::stoull(argv[1]));
    const auto page_size = static_cast<std::size_t>(std::stoull(argv[2]));
    if(!count || count > 4096 || !page_size || page_size > count) return 2;
    am::ConversationStoreOptions options;
    options.allocation_namespace = "pagination";
    am::InMemoryConversationStore store(options);
    const am::ConversationRef conversation{"pagination", am::ConversationId{std::uint64_t{1}}};
    const am::ConversationStoreAccess access{am::ScopeId{"scope"}, "host.v1"};
    (void)store.create_conversation(access, conversation);
    for(std::size_t i = 1; i <= count; ++i) {
        am::ConversationEntry entry;
        entry.ref = {conversation, am::EntryId{static_cast<std::uint64_t>(i)},
            am::EventRevision{std::uint64_t{1}}};
        entry.scope = access.scope;
        entry.payload = am::ConversationMessageCreated{{std::string(64, 'x'), {}}};
        if(store.append(access, am::make_conversation_envelope(std::move(entry))).status !=
            am::ConversationWriteStatus::Applied) throw std::runtime_error("fixture append failed");
    }
    am::ConversationReadLimits limits;
    limits.max_items = page_size;
    limits.max_output_bytes = 256U * 1024U * 1024U;
    limits.max_decoded_bytes = 256U * 1024U * 1024U;
    limits.max_visited_records = 262144;
    std::optional<am::ConversationContinuation> token;
    std::size_t pages = 0, visits = 0, bytes = 0, returned = 0;
    std::uint64_t frontier = 0;
    const auto start = std::chrono::steady_clock::now();
    do {
        const auto result = store.read_current(access, conversation, limits, token);
        if(result.status != am::ConversationReadStatus::Complete && result.status != am::ConversationReadStatus::Partial)
            throw std::runtime_error("bounded fold failed");
        if(pages && result.commit_generation != frontier) throw std::runtime_error("frontier changed");
        frontier = result.commit_generation;
        for(const auto& value : result.messages) {
            ++returned;
            if(value.origin.entry.value() != returned || !value.content || value.content->text != std::string(64, 'x'))
                throw std::runtime_error("pagination disagrees with canonical oracle");
        }
        visits += result.visited_records;
        bytes += result.decoded_bytes;
        token = result.continuation;
        ++pages;
    } while(token);
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - start).count();
    if(returned != count) throw std::runtime_error("incomplete enumeration");
    std::uint64_t peak_bytes = 0;
#ifdef _WIN32
    PROCESS_MEMORY_COUNTERS counters{};
    if(!GetProcessMemoryInfo(GetCurrentProcess(), &counters, sizeof(counters))) return 3;
    peak_bytes = static_cast<std::uint64_t>(counters.PeakWorkingSetSize);
#else
    rusage usage{};
    if(getrusage(RUSAGE_SELF, &usage) != 0) return 3;
#ifdef __APPLE__
    peak_bytes = static_cast<std::uint64_t>(usage.ru_maxrss);
#else
    peak_bytes = static_cast<std::uint64_t>(usage.ru_maxrss) * 1024;
#endif
#endif
    std::cout << count << ',' << page_size << ',' << pages << ',' << visits << ',' << bytes << ','
        << elapsed << ',' << peak_bytes << '\n';
}
