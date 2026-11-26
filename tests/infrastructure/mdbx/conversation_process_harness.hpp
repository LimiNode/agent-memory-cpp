#pragma once
#ifndef AGENT_MEMORY_HEADER_TESTS_INFRASTRUCTURE_MDBX_CONVERSATION_PROCESS_HARNESS_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_TESTS_INFRASTRUCTURE_MDBX_CONVERSATION_PROCESS_HARNESS_HPP_INCLUDED

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace conversation_process_test {
namespace detail {

inline constexpr std::size_t maximum_line_size = 4096;
inline constexpr auto message_timeout = std::chrono::seconds(15);
using Deadline = std::chrono::steady_clock::time_point;

inline Deadline deadline() {
    return std::chrono::steady_clock::now() + message_timeout;
}

inline int remaining_milliseconds(Deadline limit) {
    const auto remaining = limit - std::chrono::steady_clock::now();
    if (remaining <= std::chrono::steady_clock::duration::zero()) {
        throw std::runtime_error("child process protocol timeout");
    }
    return static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count()) + 1;
}

inline void validate_line(const std::string& line) {
    if (line.size() > maximum_line_size || line.find_first_of("\r\n") != std::string::npos ||
        line.find('\0') != std::string::npos) {
        throw std::runtime_error("invalid child process protocol line");
    }
}

#ifdef _WIN32

inline std::runtime_error system_error(const char* operation) {
    return std::runtime_error(std::string(operation) + ": Windows error " + std::to_string(GetLastError()));
}

class Handle {
public:
    Handle() = default;
    explicit Handle(HANDLE value) : m_value(value) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : m_value(std::exchange(other.m_value, nullptr)) {}
    Handle& operator=(Handle&& other) noexcept {
        reset(std::exchange(other.m_value, nullptr));
        return *this;
    }
    HANDLE get() const { return m_value; }
    explicit operator bool() const { return m_value && m_value != INVALID_HANDLE_VALUE; }
    void reset(HANDLE value = nullptr) noexcept {
        if (*this) {
            CloseHandle(m_value);
        }
        m_value = value;
    }
private:
    HANDLE m_value = nullptr;
};

inline std::wstring widen(const std::string& value) {
    if (value.empty()) {
        return {};
    }
    const int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                                       static_cast<int>(value.size()), nullptr, 0);
    if (size == 0) {
        throw system_error("MultiByteToWideChar");
    }
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                           static_cast<int>(value.size()), result.data(), size) != size) {
        throw system_error("MultiByteToWideChar");
    }
    return result;
}

inline std::wstring quote_argument(const std::wstring& argument) {
    std::wstring result = L"\"";
    std::size_t slashes = 0;
    for (const wchar_t character : argument) {
        if (character == L'\\') {
            ++slashes;
        } else {
            result.append(character == L'\"' ? slashes * 2 + 1 : slashes, L'\\');
            result += character;
            slashes = 0;
        }
    }
    result.append(slashes * 2, L'\\');
    result += L'\"';
    return result;
}

struct Pipe {
    Handle parent;
    Handle child;
};

inline Pipe make_pipe(bool parent_writes) {
    static std::atomic<unsigned long> next_pipe{0};
    const auto name = L"\\\\.\\pipe\\agent-memory-conversation-" + std::to_wstring(GetCurrentProcessId()) +
                      L"-" + std::to_wstring(next_pipe.fetch_add(1));
    Pipe pipe;
    // Only the parent endpoint is overlapped; the child's standard streams remain ordinary byte streams.
    pipe.parent.reset(CreateNamedPipeW(name.c_str(), FILE_FLAG_OVERLAPPED |
        (parent_writes ? PIPE_ACCESS_OUTBOUND : PIPE_ACCESS_INBOUND), PIPE_TYPE_BYTE | PIPE_WAIT,
        1, 8192, 8192, 0, nullptr));
    if (!pipe.parent) {
        throw system_error("CreateNamedPipeW");
    }
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    pipe.child.reset(CreateFileW(name.c_str(), parent_writes ? GENERIC_READ : GENERIC_WRITE,
                                0, &security, OPEN_EXISTING, 0, nullptr));
    if (!pipe.child) {
        throw system_error("CreateFileW child pipe");
    }
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event) {
        throw system_error("CreateEventW");
    }
    OVERLAPPED operation{};
    operation.hEvent = event.get();
    if (!ConnectNamedPipe(pipe.parent.get(), &operation) && GetLastError() != ERROR_PIPE_CONNECTED) {
        throw system_error("ConnectNamedPipe");
    }
    return pipe;
}

inline DWORD transfer(HANDLE pipe, char* bytes, DWORD size, bool is_write, Deadline limit) {
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event) {
        throw system_error("CreateEventW");
    }
    OVERLAPPED operation{};
    operation.hEvent = event.get();
    DWORD transferred = 0;
    const BOOL is_complete = is_write ? WriteFile(pipe, bytes, size, &transferred, &operation) :
                                       ReadFile(pipe, bytes, size, &transferred, &operation);
    if (!is_complete) {
        if (GetLastError() != ERROR_IO_PENDING) {
            throw system_error(is_write ? "WriteFile child command" : "ReadFile child event");
        }
        DWORD result = WAIT_FAILED;
        try {
            result = WaitForSingleObject(event.get(), static_cast<DWORD>(remaining_milliseconds(limit)));
        } catch (...) {
            CancelIoEx(pipe, &operation);
            GetOverlappedResult(pipe, &operation, &transferred, TRUE);
            throw;
        }
        if (result != WAIT_OBJECT_0) {
            // Drain cancellation before the stack-owned OVERLAPPED or its event can be destroyed.
            CancelIoEx(pipe, &operation);
            GetOverlappedResult(pipe, &operation, &transferred, TRUE);
            throw std::runtime_error("child process protocol timeout or wait failure");
        }
        if (!GetOverlappedResult(pipe, &operation, &transferred, FALSE)) {
            throw system_error("GetOverlappedResult child pipe");
        }
    }
    if (transferred == 0) {
        throw std::runtime_error("child process protocol pipe closed");
    }
    return transferred;
}

class InheritedHandles {
public:
    explicit InheritedHandles(const std::vector<HANDLE>& handles) {
        SIZE_T size = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
        m_storage.resize(size);
        auto* list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(m_storage.data());
        if (!InitializeProcThreadAttributeList(list, 1, 0, &size)) {
            throw system_error("InitializeProcThreadAttributeList");
        }
        m_list = list;
        if (!UpdateProcThreadAttribute(m_list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                       const_cast<HANDLE*>(handles.data()), handles.size() * sizeof(HANDLE),
                                       nullptr, nullptr)) {
            DeleteProcThreadAttributeList(m_list);
            m_list = nullptr;
            throw system_error("UpdateProcThreadAttribute");
        }
    }
    ~InheritedHandles() { DeleteProcThreadAttributeList(m_list); }
    InheritedHandles(const InheritedHandles&) = delete;
    InheritedHandles& operator=(const InheritedHandles&) = delete;
    LPPROC_THREAD_ATTRIBUTE_LIST get() const { return m_list; }
private:
    std::vector<unsigned char> m_storage;
    LPPROC_THREAD_ATTRIBUTE_LIST m_list = nullptr;
};

#else

inline std::runtime_error system_error(const char* operation) {
    return std::runtime_error(std::string(operation) + ": errno " + std::to_string(errno));
}

class Descriptor {
public:
    Descriptor() = default;
    explicit Descriptor(int value) : m_value(value) {}
    ~Descriptor() { reset(); }
    Descriptor(const Descriptor&) = delete;
    Descriptor& operator=(const Descriptor&) = delete;
    int get() const { return m_value; }
    void reset(int value = -1) noexcept {
        if (m_value >= 0) {
            close(m_value);
        }
        m_value = value;
    }
private:
    int m_value = -1;
};

class BlockedSignal {
public:
    explicit BlockedSignal(int signal_number) {
        sigemptyset(&m_blocked);
        sigaddset(&m_blocked, signal_number);
        if (sigprocmask(SIG_BLOCK, &m_blocked, &m_previous) != 0) {
            throw system_error("sigprocmask block");
        }
    }
    ~BlockedSignal() { sigprocmask(SIG_SETMASK, &m_previous, nullptr); }
    BlockedSignal(const BlockedSignal&) = delete;
    BlockedSignal& operator=(const BlockedSignal&) = delete;
    void wait(Deadline limit) const {
        const int milliseconds = remaining_milliseconds(limit);
        const timespec timeout{milliseconds / 1000, (milliseconds % 1000) * 1000000L};
        if (sigtimedwait(&m_blocked, nullptr, &timeout) < 0 && errno != EINTR && errno != EAGAIN) {
            throw system_error("sigtimedwait child exit");
        }
    }
private:
    sigset_t m_blocked{};
    sigset_t m_previous{};
};

inline void make_pipe(Descriptor& reader, Descriptor& writer) {
    int descriptors[2];
    if (pipe(descriptors) != 0) {
        throw system_error("pipe");
    }
    reader.reset(descriptors[0]);
    writer.reset(descriptors[1]);
    if (fcntl(reader.get(), F_SETFD, FD_CLOEXEC) < 0 || fcntl(writer.get(), F_SETFD, FD_CLOEXEC) < 0) {
        throw system_error("fcntl FD_CLOEXEC");
    }
}

inline void wait_pipe(int descriptor, short events, Deadline limit) {
    pollfd item{descriptor, events, 0};
    for (;;) {
        const int result = poll(&item, 1, remaining_milliseconds(limit));
        if (result > 0) {
            if ((item.revents & events) != 0) {
                return;
            }
            throw std::runtime_error("child process protocol pipe closed");
        }
        if (result == 0) {
            throw std::runtime_error("child process protocol timeout");
        }
        if (errno != EINTR) {
            throw system_error("poll child pipe");
        }
    }
}

inline ssize_t write_pipe(int descriptor, const char* bytes, std::size_t size) {
    // A dead child must fail this test with an exception, not kill the parent with SIGPIPE.
    sigset_t blocked;
    sigset_t previous;
    sigset_t pending;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGPIPE);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous) != 0) {
        throw system_error("sigprocmask block");
    }
    sigpending(&pending);
    const bool has_pending = sigismember(&pending, SIGPIPE) == 1;
    const ssize_t result = write(descriptor, bytes, size);
    const int error = errno;
    if (result < 0 && error == EPIPE && !has_pending) {
        const timespec no_wait{0, 0};
        while (sigtimedwait(&blocked, nullptr, &no_wait) < 0 && errno == EINTR) {}
    }
    sigprocmask(SIG_SETMASK, &previous, nullptr);
    errno = error;
    return result;
}

#endif
} // namespace detail

// Standard output belongs exclusively to this acknowledged line protocol; child diagnostics use stderr.
inline std::string receive_command() {
    std::string line;
    char character = 0;
    while (std::cin.get(character)) {
        if (character == '\n') {
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            detail::validate_line(line);
            return line;
        }
        if (line.size() >= detail::maximum_line_size &&
            !(line.size() == detail::maximum_line_size && character == '\r')) {
            throw std::runtime_error("child command exceeds protocol line limit");
        }
        line += character;
    }
    throw std::runtime_error("parent command pipe closed");
}

inline void send_event(const std::string& line) {
    detail::validate_line(line);
    std::cout << line << '\n' << std::flush;
    if (!std::cout) {
        throw std::runtime_error("cannot send child event");
    }
}

// Every parent-side operation has a deadline; failure kills and reaps the owned child.
// Blocking child stream reads are bounded by the parent's next receive() deadline and RAII cleanup.
class ChildProcess {
public:
    ChildProcess(const std::string& executable, const std::vector<std::string>& arguments) {
        if (!std::filesystem::path(executable).is_absolute() || executable.find('\0') != std::string::npos) {
            throw std::runtime_error("child executable must be an absolute path");
        }
        for (const auto& argument : arguments) {
            if (argument.find('\0') != std::string::npos) {
                throw std::runtime_error("child argument contains a null byte");
            }
        }
        try {
            start(executable, arguments);
        } catch (...) {
            terminate_noexcept();
            throw;
        }
    }
    ~ChildProcess() { terminate_noexcept(); }
    ChildProcess(const ChildProcess&) = delete;
    ChildProcess& operator=(const ChildProcess&) = delete;

    void send(const std::string& line) {
        try {
            detail::validate_line(line);
            auto bytes = line + '\n';
            std::size_t offset = 0;
            const auto limit = detail::deadline();
            while (offset < bytes.size()) {
#ifdef _WIN32
                offset += detail::transfer(m_input.get(), bytes.data() + offset,
                    static_cast<DWORD>(bytes.size() - offset), true, limit);
#else
                detail::wait_pipe(m_input.get(), POLLOUT, limit);
                const auto size = detail::write_pipe(m_input.get(), bytes.data() + offset, bytes.size() - offset);
                if (size > 0) {
                    offset += static_cast<std::size_t>(size);
                } else if (size == 0 || (errno != EINTR && errno != EAGAIN)) {
                    throw detail::system_error("write child command");
                }
#endif
            }
        } catch (...) {
            terminate_noexcept();
            throw;
        }
    }

    std::string receive() {
        try {
            const auto limit = detail::deadline();
            std::string line;
            for (;;) {
                char character = 0;
#ifdef _WIN32
                detail::transfer(m_output.get(), &character, 1, false, limit);
#else
                detail::wait_pipe(m_output.get(), POLLIN, limit);
                const auto size = read(m_output.get(), &character, 1);
                if (size < 0 && (errno == EINTR || errno == EAGAIN)) {
                    continue;
                }
                if (size != 1) {
                    throw std::runtime_error("child event pipe closed");
                }
#endif
                if (character == '\n') {
                    if (!line.empty() && line.back() == '\r') {
                        line.pop_back();
                    }
                    detail::validate_line(line);
                    return line;
                }
                if (line.size() >= detail::maximum_line_size &&
                    !(line.size() == detail::maximum_line_size && character == '\r')) {
                    throw std::runtime_error("child event exceeds protocol line limit");
                }
                line += character;
            }
        } catch (...) {
            terminate_noexcept();
            throw;
        }
    }

    void terminate_and_wait() {
        if (!is_live()) {
            return;
        }
        try {
#ifdef _WIN32
            if (!TerminateProcess(m_process.get(), 91) && WaitForSingleObject(m_process.get(), 0) != WAIT_OBJECT_0) {
                throw detail::system_error("TerminateProcess");
            }
#else
            if (kill(m_process, SIGKILL) != 0 && errno != ESRCH) {
                throw detail::system_error("kill child");
            }
#endif
            wait_for_exit(false);
        } catch (...) {
            terminate_noexcept();
            throw;
        }
    }

    void wait_success() {
        try {
            m_input.reset();
            wait_for_exit(true);
        } catch (...) {
            terminate_noexcept();
            throw;
        }
    }

private:
    bool is_live() const {
#ifdef _WIN32
        return static_cast<bool>(m_process);
#else
        return m_process > 0;
#endif
    }

    void start(const std::string& executable, const std::vector<std::string>& arguments) {
#ifdef _WIN32
        auto input = detail::make_pipe(true);
        auto output = detail::make_pipe(false);
        detail::Handle error_output;
        HANDLE duplicate = nullptr;
        const HANDLE error_stream = GetStdHandle(STD_ERROR_HANDLE);
        if (error_stream && error_stream != INVALID_HANDLE_VALUE &&
            DuplicateHandle(GetCurrentProcess(), error_stream, GetCurrentProcess(), &duplicate, 0, TRUE,
                            DUPLICATE_SAME_ACCESS)) {
            error_output.reset(duplicate);
        } else {
            SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
            error_output.reset(CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                          &security, OPEN_EXISTING, 0, nullptr));
        }
        if (!error_output) {
            throw detail::system_error("create child stderr");
        }
        const std::vector<HANDLE> inherited{input.child.get(), output.child.get(), error_output.get()};
        detail::InheritedHandles attributes(inherited);
        STARTUPINFOEXW startup{};
        startup.StartupInfo.cb = sizeof(startup);
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = input.child.get();
        startup.StartupInfo.hStdOutput = output.child.get();
        startup.StartupInfo.hStdError = error_output.get();
        startup.lpAttributeList = attributes.get();
        const auto application = detail::widen(executable);
        auto command = detail::quote_argument(application);
        for (const auto& argument : arguments) {
            command += L' ';
            command += detail::quote_argument(detail::widen(argument));
        }
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(application.c_str(), command.data(), nullptr, nullptr, TRUE,
                            CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, nullptr, nullptr,
                            &startup.StartupInfo, &process)) {
            throw detail::system_error("CreateProcessW");
        }
        m_process.reset(process.hProcess);
        detail::Handle thread(process.hThread);
        m_input = std::move(input.parent);
        m_output = std::move(output.parent);
#else
        detail::Descriptor child_input;
        detail::Descriptor child_output;
        detail::make_pipe(child_input, m_input);
        detail::make_pipe(m_output, child_output);
        std::vector<char*> argv{const_cast<char*>(executable.c_str())};
        for (const auto& argument : arguments) {
            argv.push_back(const_cast<char*>(argument.c_str()));
        }
        argv.push_back(nullptr);
        const pid_t process = fork();
        if (process < 0) {
            throw detail::system_error("fork");
        }
        if (process == 0) {
            if (dup2(child_input.get(), STDIN_FILENO) < 0 || dup2(child_output.get(), STDOUT_FILENO) < 0) {
                _exit(126);
            }
            child_input.reset();
            child_output.reset();
            m_input.reset();
            m_output.reset();
            execv(executable.c_str(), argv.data());
            _exit(127);
        }
        m_process = process;
        if (fcntl(m_input.get(), F_SETFL, O_NONBLOCK) < 0 || fcntl(m_output.get(), F_SETFL, O_NONBLOCK) < 0) {
            throw detail::system_error("fcntl O_NONBLOCK");
        }
#endif
    }

    void wait_for_exit(bool require_success) {
        if (!is_live()) {
            throw std::runtime_error("child process already reaped");
        }
        bool has_succeeded = false;
#ifdef _WIN32
        if (WaitForSingleObject(m_process.get(), static_cast<DWORD>(
                std::chrono::duration_cast<std::chrono::milliseconds>(detail::message_timeout).count())) !=
            WAIT_OBJECT_0) {
            throw std::runtime_error("child process exit timeout or wait failure");
        }
        DWORD code = 0;
        if (!GetExitCodeProcess(m_process.get(), &code)) {
            throw detail::system_error("GetExitCodeProcess");
        }
        has_succeeded = code == 0;
        m_process.reset();
#else
        m_input.reset();
        const auto limit = detail::deadline();
        // Block before checking waitpid so exit between the check and wait remains a pending notification.
        const detail::BlockedSignal child_exit(SIGCHLD);
        for (;;) {
            int status = 0;
            const pid_t result = waitpid(m_process, &status, WNOHANG);
            if (result == m_process) {
                has_succeeded = WIFEXITED(status) && WEXITSTATUS(status) == 0;
                m_process = -1;
                break;
            }
            if (result < 0 && errno != EINTR) {
                throw detail::system_error("waitpid child");
            }
            child_exit.wait(limit);
        }
#endif
        m_input.reset();
        m_output.reset();
        if (require_success && !has_succeeded) {
            throw std::runtime_error("child process exited unsuccessfully");
        }
    }

    void terminate_noexcept() noexcept {
#ifdef _WIN32
        if (m_process) {
            TerminateProcess(m_process.get(), 91);
            WaitForSingleObject(m_process.get(), 15000);
            m_process.reset();
        }
#else
        if (m_process > 0) {
            kill(m_process, SIGKILL);
            int status = 0;
            while (waitpid(m_process, &status, 0) < 0 && errno == EINTR) {}
            m_process = -1;
        }
#endif
        m_input.reset();
        m_output.reset();
    }

#ifdef _WIN32
    detail::Handle m_process;
    detail::Handle m_input;
    detail::Handle m_output;
#else
    pid_t m_process = -1;
    detail::Descriptor m_input;
    detail::Descriptor m_output;
#endif
};

} // namespace conversation_process_test

#endif
