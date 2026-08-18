#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <aclapi.h>
#include <bcrypt.h>
#include <winternl.h>
#include <winioctl.h>
#include <malloc.h>

#include "qfc/copy_engine.h"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <cwctype>
#include <mutex>
#include <optional>
#include <string_view>
#include <thread>
#include <unordered_map>

namespace qfc {
namespace {

struct CopyJob {
    std::wstring source;
    std::wstring destination;
    std::uint64_t size = 0;
    FILETIME last_write{};
    DWORD attributes = 0;
    bool reparse_point = false;
    bool directory = false;
};

struct DirectoryMetadata {
    std::wstring source;
    std::wstring destination;
    FILETIME creation{};
    FILETIME access{};
    FILETIME write{};
    DWORD attributes = FILE_ATTRIBUTE_DIRECTORY;
};

struct HardLinkIdentity {
    DWORD volume_serial = 0;
    DWORD file_index_high = 0;
    DWORD file_index_low = 0;

    bool operator==(const HardLinkIdentity&) const = default;
};

struct HardLinkIdentityHash {
    std::size_t operator()(const HardLinkIdentity& value) const noexcept {
        std::size_t hash = value.volume_serial;
        hash = hash * 1315423911u + value.file_index_high;
        hash = hash * 1315423911u + value.file_index_low;
        return hash;
    }
};

struct PendingHardLink {
    std::wstring source;
    std::wstring destination;
    std::wstring existing_destination;
    FILETIME last_write{};
};

template <typename T>
class BoundedQueue {
public:
    explicit BoundedQueue(std::size_t capacity) : capacity_(capacity) {}

    bool push(T value, const std::atomic_bool& cancelled) {
        std::unique_lock lock(mutex_);
        not_full_.wait(lock, [&] {
            return queue_.size() < capacity_ || closed_ || cancelled.load(std::memory_order_relaxed);
        });
        if (closed_ || cancelled.load(std::memory_order_relaxed)) return false;
        queue_.push_back(std::move(value));
        not_empty_.notify_one();
        return true;
    }

    std::optional<T> pop(const std::atomic_bool& cancelled) {
        std::unique_lock lock(mutex_);
        not_empty_.wait(lock, [&] {
            return !queue_.empty() || closed_ || cancelled.load(std::memory_order_relaxed);
        });
        if (queue_.empty()) return std::nullopt;
        T value = std::move(queue_.front());
        queue_.pop_front();
        not_full_.notify_one();
        return value;
    }

    void close() {
        std::lock_guard lock(mutex_);
        closed_ = true;
        not_empty_.notify_all();
        not_full_.notify_all();
    }

    void wake_all() {
        not_empty_.notify_all();
        not_full_.notify_all();
    }

private:
    std::size_t capacity_;
    std::deque<T> queue_;
    std::mutex mutex_;
    std::condition_variable not_empty_;
    std::condition_variable not_full_;
    bool closed_ = false;
};

std::wstring join_path(const std::wstring& left, const std::wstring& right) {
    if (left.empty()) return right;
    if (left.back() == L'\\' || left.back() == L'/') return left + right;
    return left + L"\\" + right;
}

std::wstring leaf_name(std::wstring path) {
    while (path.size() > 1 && (path.back() == L'\\' || path.back() == L'/')) path.pop_back();
    if (path.size() == 2 && path[1] == L':') return path.substr(0, 1);
    const auto pos = path.find_last_of(L"\\/");
    if (pos == std::wstring::npos) return path;
    if (pos + 1 < path.size()) return path.substr(pos + 1);
    if (path.size() >= 2 && path[1] == L':') return path.substr(0, 1);
    return L"root";
}

std::uint64_t file_size(const WIN32_FIND_DATAW& data) {
    return (static_cast<std::uint64_t>(data.nFileSizeHigh) << 32) | data.nFileSizeLow;
}

bool exists(const std::wstring& path, WIN32_FILE_ATTRIBUTE_DATA* data = nullptr) {
    WIN32_FILE_ATTRIBUTE_DATA local{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &local)) return false;
    if (data) *data = local;
    return true;
}

bool ensure_directory(const std::wstring& path) {
    if (path.empty()) return false;
    if (CreateDirectoryW(path.c_str(), nullptr)) return true;
    const DWORD code = GetLastError();
    if (code == ERROR_ALREADY_EXISTS) {
        const DWORD attrs = GetFileAttributesW(path.c_str());
        return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY) != 0;
    }
    const auto split = path.find_last_of(L"\\/");
    if (split == std::wstring::npos) return false;
    const std::wstring parent = path.substr(0, split);
    if (!parent.empty() && !ensure_directory(parent)) return false;
    if (CreateDirectoryW(path.c_str(), nullptr)) return true;
    return GetLastError() == ERROR_ALREADY_EXISTS;
}

bool enable_token_privilege(const wchar_t* name) {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) {
        return false;
    }
    LUID luid{};
    if (!LookupPrivilegeValueW(nullptr, name, &luid)) {
        CloseHandle(token);
        return false;
    }
    TOKEN_PRIVILEGES privileges{};
    privileges.PrivilegeCount = 1;
    privileges.Privileges[0].Luid = luid;
    privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    SetLastError(ERROR_SUCCESS);
    const BOOL adjusted = AdjustTokenPrivileges(
        token, FALSE, &privileges, sizeof(privileges), nullptr, nullptr
    );
    const DWORD code = GetLastError();
    CloseHandle(token);
    return adjusted && code == ERROR_SUCCESS;
}

std::wstring parent_path(const std::wstring& path) {
    const auto split = path.find_last_of(L"\\/");
    return split == std::wstring::npos ? std::wstring{} : path.substr(0, split);
}

bool path_equal_case_insensitive(const std::wstring& left, const std::wstring& right) {
    return CompareStringOrdinal(
        left.c_str(), static_cast<int>(left.size()),
        right.c_str(), static_cast<int>(right.size()), TRUE
    ) == CSTR_EQUAL;
}

bool path_is_inside(const std::wstring& candidate, const std::wstring& parent) {
    std::wstring prefix = parent;
    while (!prefix.empty() && (prefix.back() == L'\\' || prefix.back() == L'/')) prefix.pop_back();
    prefix.push_back(L'\\');
    if (candidate.size() <= prefix.size()) return false;
    return CompareStringOrdinal(
        candidate.c_str(), static_cast<int>(prefix.size()),
        prefix.c_str(), static_cast<int>(prefix.size()), TRUE
    ) == CSTR_EQUAL;
}

bool is_unc_path(const std::wstring& path) {
    return path.rfind(L"\\\\?\\UNC\\", 0) == 0 || path.rfind(L"\\\\", 0) == 0;
}

bool is_fixed_local_path(const std::wstring& path) {
    if (is_unc_path(path)) return false;
    wchar_t volume_path[MAX_PATH]{};
    if (!GetVolumePathNameW(path.c_str(), volume_path, MAX_PATH)) return false;
    return GetDriveTypeW(volume_path) == DRIVE_FIXED;
}

bool has_cloud_or_reparse_component(const std::wstring& path) {
    constexpr DWORD excluded = FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_OFFLINE |
        FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS;
    std::wstring cursor = path;
    for (unsigned int depth = 0; depth < 64 && !cursor.empty(); ++depth) {
        const DWORD attributes = GetFileAttributesW(cursor.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & excluded) != 0) return true;
        const auto parent = parent_path(cursor);
        if (parent.empty() || path_equal_case_insensitive(parent, cursor)) break;
        cursor = parent;
    }
    return false;
}

bool has_named_data_stream(const std::wstring& path) {
    WIN32_FIND_STREAM_DATA stream{};
    HANDLE find = FindFirstStreamW(path.c_str(), FindStreamInfoStandard, &stream, 0);
    if (find == INVALID_HANDLE_VALUE) return false;
    bool named = wcscmp(stream.cStreamName, L"::$DATA") != 0;
    while (!named && FindNextStreamW(find, &stream)) {
        named = wcscmp(stream.cStreamName, L"::$DATA") != 0;
    }
    FindClose(find);
    return named;
}

std::wstring temporary_destination(const std::wstring& destination) {
    static std::atomic<std::uint64_t> sequence{0};
    const auto id = sequence.fetch_add(1, std::memory_order_relaxed);
    return destination + L".qfc-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
           std::to_wstring(id) + L".part";
}

void remove_stale_temporaries(const std::wstring& destination) {
    const std::wstring pattern = destination + L".qfc-*.part";
    WIN32_FIND_DATAW data{};
    HANDLE find = FindFirstFileW(pattern.c_str(), &data);
    if (find == INVALID_HANDLE_VALUE) return;
    const std::wstring directory = parent_path(destination);
    do {
        const std::wstring candidate = join_path(directory, data.cFileName);
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
            RemoveDirectoryW(candidate.c_str());
        } else {
            DeleteFileW(candidate.c_str());
        }
    } while (FindNextFileW(find, &data));
    FindClose(find);
}

std::uint64_t filetime_value(const FILETIME& value) {
    ULARGE_INTEGER combined{};
    combined.LowPart = value.dwLowDateTime;
    combined.HighPart = value.dwHighDateTime;
    return combined.QuadPart;
}

std::string journal_utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(
        CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr
    );
    if (size <= 0) return {};
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(
        CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
        result.data(), size, nullptr, nullptr
    );
    return result;
}

std::wstring journal_wide(const std::string& value) {
    if (value.empty()) return {};
    const int size = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0
    );
    if (size <= 0) return {};
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
        result.data(), size
    );
    return result;
}

std::wstring resume_journal_path(
    const std::vector<std::wstring>& sources,
    const std::wstring& destination,
    DirectoryLayout layout
) {
    std::uint64_t hash = 1469598103934665603ull;
    auto add = [&](const std::wstring& value) {
        for (wchar_t ch : value) {
            ch = static_cast<wchar_t>(std::towlower(ch));
            hash ^= static_cast<std::uint16_t>(ch);
            hash *= 1099511628211ull;
        }
        hash ^= 0xffffu;
        hash *= 1099511628211ull;
    };
    for (const auto& source : sources) add(normalize_long_path(source));
    add(destination);
    hash ^= static_cast<std::uint64_t>(layout);
    wchar_t suffix[32]{};
    swprintf(suffix, std::size(suffix), L"%016llx", static_cast<unsigned long long>(hash));
    return join_path(destination, L".qfc-resume-" + std::wstring(suffix) + L".journal");
}

class ResumeJournal {
public:
    struct Record {
        std::uint64_t size = 0;
        std::uint64_t last_write = 0;
    };

    ResumeJournal(std::wstring path, bool enabled) : path_(std::move(path)) {
        if (!enabled) return;
        handle_ = CreateFileW(
            path_.c_str(), GENERIC_READ | FILE_APPEND_DATA,
            FILE_SHARE_READ, nullptr, OPEN_ALWAYS,
            FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED, nullptr
        );
        if (handle_ == INVALID_HANDLE_VALUE) return;
        LARGE_INTEGER size{};
        if (!GetFileSizeEx(handle_, &size) || size.QuadPart < 0 || size.QuadPart > 256ll * 1024 * 1024) {
            CloseHandle(handle_);
            handle_ = INVALID_HANDLE_VALUE;
            return;
        }
        resuming_ = size.QuadPart > 0;
        std::string bytes(static_cast<std::size_t>(size.QuadPart), '\0');
        DWORD offset = 0;
        while (offset < bytes.size()) {
            DWORD read = 0;
            const DWORD request = static_cast<DWORD>(
                std::min<std::size_t>(bytes.size() - offset, 4 * 1024 * 1024)
            );
            if (!ReadFile(handle_, bytes.data() + offset, request, &read, nullptr) || read == 0) break;
            offset += read;
        }
        bytes.resize(offset);
        load(bytes);
    }

    ~ResumeJournal() {
        if (handle_ != INVALID_HANDLE_VALUE) {
            FlushFileBuffers(handle_);
            CloseHandle(handle_);
        }
    }

    bool completed(const CopyJob& job) {
        Record record{};
        {
            std::lock_guard lock(mutex_);
            const auto found = records_.find(job.destination);
            if (found == records_.end()) return false;
            record = found->second;
        }
        if (record.size != job.size || record.last_write != filetime_value(job.last_write)) return false;
        WIN32_FILE_ATTRIBUTE_DATA data{};
        if (!GetFileAttributesExW(job.destination.c_str(), GetFileExInfoStandard, &data)) return false;
        const std::uint64_t destination_size =
            (static_cast<std::uint64_t>(data.nFileSizeHigh) << 32) | data.nFileSizeLow;
        return destination_size == job.size &&
               filetime_value(data.ftLastWriteTime) == record.last_write;
    }

    bool resuming() const noexcept { return resuming_; }

    void mark(const CopyJob& job) {
        if (handle_ == INVALID_HANDLE_VALUE) return;
        const Record record{job.size, filetime_value(job.last_write)};
        const std::wstring line = std::to_wstring(record.size) + L"\t" +
            std::to_wstring(record.last_write) + L"\t" + job.destination + L"\n";
        const std::string bytes = journal_utf8(line);
        std::lock_guard lock(mutex_);
        records_[job.destination] = record;
        DWORD written = 0;
        if (!bytes.empty()) WriteFile(
            handle_, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr
        );
        if (++unflushed_ >= 32) {
            FlushFileBuffers(handle_);
            unflushed_ = 0;
        }
    }

    void finish_success() {
        std::lock_guard lock(mutex_);
        if (handle_ != INVALID_HANDLE_VALUE) {
            FlushFileBuffers(handle_);
            CloseHandle(handle_);
            handle_ = INVALID_HANDLE_VALUE;
        }
        DeleteFileW(path_.c_str());
    }

private:
    void load(const std::string& bytes) {
        const std::wstring text = journal_wide(bytes);
        std::size_t start = 0;
        while (start < text.size()) {
            const std::size_t end = text.find(L'\n', start);
            const std::wstring_view line(
                text.data() + start,
                (end == std::wstring::npos ? text.size() : end) - start
            );
            const std::size_t first = line.find(L'\t');
            const std::size_t second = first == std::wstring_view::npos
                ? std::wstring_view::npos : line.find(L'\t', first + 1);
            if (first != std::wstring_view::npos && second != std::wstring_view::npos) {
                try {
                    const auto size = std::stoull(std::wstring(line.substr(0, first)));
                    const auto write = std::stoull(std::wstring(line.substr(first + 1, second - first - 1)));
                    records_[std::wstring(line.substr(second + 1))] = Record{size, write};
                } catch (...) {
                }
            }
            if (end == std::wstring::npos) break;
            start = end + 1;
        }
    }

    std::wstring path_;
    HANDLE handle_ = INVALID_HANDLE_VALUE;
    std::mutex mutex_;
    std::unordered_map<std::wstring, Record> records_;
    unsigned int unflushed_ = 0;
    bool resuming_ = false;
};

using NtQueryEaFileProc = NTSTATUS (NTAPI*)(
    HANDLE, PIO_STATUS_BLOCK, PVOID, ULONG, BOOLEAN, PVOID, ULONG, PULONG, BOOLEAN
);
using NtSetEaFileProc = NTSTATUS (NTAPI*)(HANDLE, PIO_STATUS_BLOCK, PVOID, ULONG);
using RtlNtStatusToDosErrorProc = ULONG (NTAPI*)(NTSTATUS);

DWORD ea_status_to_error(NTSTATUS status, RtlNtStatusToDosErrorProc convert) {
    if (status >= 0) return ERROR_SUCCESS;
    return convert ? convert(status) : ERROR_INVALID_DATA;
}

bool is_ea_unsupported_error(DWORD error) {
    return error == ERROR_INVALID_FUNCTION ||
           error == ERROR_NOT_SUPPORTED ||
           error == ERROR_EAS_NOT_SUPPORTED;
}

DWORD copy_extended_attributes(
    const std::wstring& source,
    const std::wstring& destination
) {
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (!ntdll) return ERROR_CALL_NOT_IMPLEMENTED;
    const auto query = reinterpret_cast<NtQueryEaFileProc>(
        GetProcAddress(ntdll, "NtQueryEaFile")
    );
    const auto set = reinterpret_cast<NtSetEaFileProc>(
        GetProcAddress(ntdll, "NtSetEaFile")
    );
    const auto convert = reinterpret_cast<RtlNtStatusToDosErrorProc>(
        GetProcAddress(ntdll, "RtlNtStatusToDosError")
    );
    if (!query || !set) return ERROR_CALL_NOT_IMPLEMENTED;

    const DWORD open_flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT;
    HANDLE source_handle = CreateFileW(
        source.c_str(), FILE_READ_EA,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, open_flags, nullptr
    );
    if (source_handle == INVALID_HANDLE_VALUE) {
        const DWORD open_error = GetLastError();
        return is_ea_unsupported_error(open_error) ? ERROR_SUCCESS : open_error;
    }

    constexpr NTSTATUS status_no_eas = static_cast<NTSTATUS>(0xC0000052u);
    constexpr NTSTATUS status_eas_not_supported = static_cast<NTSTATUS>(0xC000004Fu);
    constexpr NTSTATUS status_invalid_device_request = static_cast<NTSTATUS>(0xC0000010u);
    constexpr NTSTATUS status_not_supported = static_cast<NTSTATUS>(0xC00000BBu);
    constexpr NTSTATUS status_buffer_overflow = static_cast<NTSTATUS>(0x80000005u);
    constexpr NTSTATUS status_buffer_too_small = static_cast<NTSTATUS>(0xC0000023u);
    std::vector<unsigned char> ea_data(64 * 1024 + 1024);
    IO_STATUS_BLOCK query_status{};
    NTSTATUS status = query(
        source_handle, &query_status, ea_data.data(), static_cast<ULONG>(ea_data.size()),
        FALSE, nullptr, 0, nullptr, TRUE
    );
    CloseHandle(source_handle);

    if (status == status_no_eas ||
        status == status_eas_not_supported ||
        status == status_invalid_device_request ||
        status == status_not_supported) {
        return ERROR_SUCCESS;
    }
    if (status == status_buffer_overflow || status == status_buffer_too_small) {
        return ERROR_BUFFER_OVERFLOW;
    }
    if (status < 0) {
        const DWORD query_error = ea_status_to_error(status, convert);
        return is_ea_unsupported_error(query_error) ? ERROR_SUCCESS : query_error;
    }
    const auto ea_size = static_cast<std::size_t>(query_status.Information);
    if (ea_size == 0) return ERROR_SUCCESS;
    if (ea_size > ea_data.size()) return ERROR_INVALID_DATA;
    ea_data.resize(ea_size);

    HANDLE destination_handle = CreateFileW(
        destination.c_str(), FILE_WRITE_EA,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, open_flags, nullptr
    );
    if (destination_handle == INVALID_HANDLE_VALUE) return GetLastError();
    IO_STATUS_BLOCK set_status{};
    status = set(
        destination_handle, &set_status, ea_data.data(), static_cast<ULONG>(ea_data.size())
    );
    CloseHandle(destination_handle);
    return ea_status_to_error(status, convert);
}

class EncryptedRawPipe {
public:
    explicit EncryptedRawPipe(const std::atomic_bool& cancelled) : cancelled_(cancelled) {}

    DWORD push(const unsigned char* data, ULONG length) {
        if (length == 0) return ERROR_SUCCESS;
        std::unique_lock lock(mutex_);
        not_full_.wait(lock, [&] {
            return aborted_ || cancelled_.load(std::memory_order_relaxed) ||
                   queued_bytes_ == 0 || queued_bytes_ + length <= max_queued_bytes;
        });
        if (aborted_ || cancelled_.load(std::memory_order_relaxed)) {
            return ERROR_OPERATION_ABORTED;
        }
        chunks_.emplace_back(data, data + length);
        queued_bytes_ += length;
        not_empty_.notify_one();
        return ERROR_SUCCESS;
    }

    DWORD pull(unsigned char* destination, ULONG* length) {
        if (!length) return ERROR_INVALID_PARAMETER;
        const ULONG requested = *length;
        *length = 0;
        std::unique_lock lock(mutex_);
        not_empty_.wait(lock, [&] {
            return aborted_ || cancelled_.load(std::memory_order_relaxed) ||
                   !chunks_.empty() || producer_done_;
        });
        if (aborted_ || cancelled_.load(std::memory_order_relaxed)) {
            return ERROR_OPERATION_ABORTED;
        }
        if (chunks_.empty()) return producer_error_;

        ULONG supplied = 0;
        while (supplied < requested && !chunks_.empty()) {
            auto& chunk = chunks_.front();
            const auto available = chunk.size() - front_offset_;
            const auto take = std::min<std::size_t>(available, requested - supplied);
            std::copy_n(chunk.data() + front_offset_, take, destination + supplied);
            front_offset_ += take;
            supplied += static_cast<ULONG>(take);
            queued_bytes_ -= take;
            if (front_offset_ == chunk.size()) {
                chunks_.pop_front();
                front_offset_ = 0;
            }
        }
        *length = supplied;
        not_full_.notify_one();
        return ERROR_SUCCESS;
    }

    void finish(DWORD error) {
        std::lock_guard lock(mutex_);
        producer_done_ = true;
        producer_error_ = error;
        not_empty_.notify_all();
    }

    void abort() {
        std::lock_guard lock(mutex_);
        aborted_ = true;
        not_empty_.notify_all();
        not_full_.notify_all();
    }

    DWORD producer_error() const {
        std::lock_guard lock(mutex_);
        return producer_error_;
    }

private:
    static constexpr std::size_t max_queued_bytes = 8 * 1024 * 1024;
    const std::atomic_bool& cancelled_;
    mutable std::mutex mutex_;
    std::condition_variable not_empty_;
    std::condition_variable not_full_;
    std::deque<std::vector<unsigned char>> chunks_;
    std::size_t front_offset_ = 0;
    std::size_t queued_bytes_ = 0;
    bool producer_done_ = false;
    bool aborted_ = false;
    DWORD producer_error_ = ERROR_SUCCESS;
};

DWORD CALLBACK export_encrypted_raw(PBYTE data, PVOID context, ULONG length) {
    return static_cast<EncryptedRawPipe*>(context)->push(data, length);
}

DWORD CALLBACK import_encrypted_raw(PBYTE data, PVOID context, PULONG length) {
    return static_cast<EncryptedRawPipe*>(context)->pull(data, length);
}

DWORD copy_encrypted_raw(
    const std::wstring& source,
    const std::wstring& destination,
    bool directory,
    const std::atomic_bool& cancelled
) {
    PVOID source_context = nullptr;
    DWORD code = OpenEncryptedFileRawW(source.c_str(), 0, &source_context);
    if (code != ERROR_SUCCESS) return code;

    PVOID destination_context = nullptr;
    const ULONG import_flags = CREATE_FOR_IMPORT | (directory ? CREATE_FOR_DIR : 0);
    code = OpenEncryptedFileRawW(destination.c_str(), import_flags, &destination_context);
    if (code != ERROR_SUCCESS) {
        CloseEncryptedFileRaw(source_context);
        return code;
    }

    EncryptedRawPipe pipe(cancelled);
    std::thread exporter([&] {
        pipe.finish(ReadEncryptedFileRaw(export_encrypted_raw, &pipe, source_context));
    });
    const DWORD import_code = WriteEncryptedFileRaw(
        import_encrypted_raw, &pipe, destination_context
    );
    if (import_code != ERROR_SUCCESS) pipe.abort();
    exporter.join();
    const DWORD export_code = pipe.producer_error();
    CloseEncryptedFileRaw(destination_context);
    CloseEncryptedFileRaw(source_context);

    code = import_code != ERROR_SUCCESS ? import_code : export_code;
    if (code != ERROR_SUCCESS) {
        directory ? RemoveDirectoryW(destination.c_str()) : DeleteFileW(destination.c_str());
    }
    return code;
}

DWORD copy_security_descriptor(
    const std::wstring& source,
    const std::wstring& destination,
    bool include_sacl
) {
    PSID owner = nullptr;
    PSID group = nullptr;
    PACL dacl = nullptr;
    PACL sacl = nullptr;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    SECURITY_INFORMATION requested = OWNER_SECURITY_INFORMATION |
                                     GROUP_SECURITY_INFORMATION |
                                     DACL_SECURITY_INFORMATION;
    if (include_sacl) requested |= SACL_SECURITY_INFORMATION;

    DWORD code = GetNamedSecurityInfoW(
        const_cast<wchar_t*>(source.c_str()), SE_FILE_OBJECT, requested,
        &owner, &group, &dacl, include_sacl ? &sacl : nullptr, &descriptor
    );
    if (code != ERROR_SUCCESS) return code;
    code = SetNamedSecurityInfoW(
        const_cast<wchar_t*>(destination.c_str()), SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION, nullptr, nullptr, dacl, nullptr
    );
    if (code != ERROR_SUCCESS) {
        if (descriptor) LocalFree(descriptor);
        return code;
    }

    PSID destination_owner = nullptr;
    PSID destination_group = nullptr;
    PSECURITY_DESCRIPTOR destination_descriptor = nullptr;
    DWORD destination_code = GetNamedSecurityInfoW(
        const_cast<wchar_t*>(destination.c_str()), SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION,
        &destination_owner, &destination_group, nullptr, nullptr, &destination_descriptor
    );
    if (destination_code != ERROR_SUCCESS) {
        if (descriptor) LocalFree(descriptor);
        return destination_code;
    }

    if (owner && (!destination_owner || !EqualSid(owner, destination_owner))) {
        code = SetNamedSecurityInfoW(
            const_cast<wchar_t*>(destination.c_str()), SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION, owner, nullptr, nullptr, nullptr
        );
    }
    if (code == ERROR_SUCCESS && group &&
        (!destination_group || !EqualSid(group, destination_group))) {
        code = SetNamedSecurityInfoW(
            const_cast<wchar_t*>(destination.c_str()), SE_FILE_OBJECT,
            GROUP_SECURITY_INFORMATION, nullptr, group, nullptr, nullptr
        );
    }
    if (code == ERROR_SUCCESS && include_sacl) {
        code = SetNamedSecurityInfoW(
            const_cast<wchar_t*>(destination.c_str()), SE_FILE_OBJECT,
            SACL_SECURITY_INFORMATION, nullptr, nullptr, nullptr, sacl
        );
    }
    if (destination_descriptor) LocalFree(destination_descriptor);
    if (descriptor) LocalFree(descriptor);
    return code;
}

DWORD copy_reparse_point(
    const std::wstring& source,
    const std::wstring& destination,
    bool directory
) {
    HANDLE source_handle = CreateFileW(
        source.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        nullptr
    );
    if (source_handle == INVALID_HANDLE_VALUE) return GetLastError();

    std::vector<unsigned char> reparse_data(MAXIMUM_REPARSE_DATA_BUFFER_SIZE);
    DWORD reparse_size = 0;
    if (!DeviceIoControl(
            source_handle, FSCTL_GET_REPARSE_POINT,
            nullptr, 0, reparse_data.data(), static_cast<DWORD>(reparse_data.size()),
            &reparse_size, nullptr)) {
        const DWORD code = GetLastError();
        CloseHandle(source_handle);
        return code;
    }

    FILETIME created{}, accessed{}, written{};
    GetFileTime(source_handle, &created, &accessed, &written);
    CloseHandle(source_handle);

    if (directory) {
        if (!CreateDirectoryW(destination.c_str(), nullptr)) return GetLastError();
    } else {
        HANDLE created_file = CreateFileW(
            destination.c_str(), GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr
        );
        if (created_file == INVALID_HANDLE_VALUE) return GetLastError();
        CloseHandle(created_file);
    }

    HANDLE destination_handle = CreateFileW(
        destination.c_str(), GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        nullptr
    );
    if (destination_handle == INVALID_HANDLE_VALUE) {
        const DWORD code = GetLastError();
        directory ? RemoveDirectoryW(destination.c_str()) : DeleteFileW(destination.c_str());
        return code;
    }

    DWORD ignored = 0;
    const BOOL set_ok = DeviceIoControl(
        destination_handle, FSCTL_SET_REPARSE_POINT,
        reparse_data.data(), reparse_size, nullptr, 0, &ignored, nullptr
    );
    DWORD code = set_ok ? ERROR_SUCCESS : GetLastError();
    if (set_ok) SetFileTime(destination_handle, &created, &accessed, &written);
    CloseHandle(destination_handle);
    if (!set_ok) directory ? RemoveDirectoryW(destination.c_str()) : DeleteFileW(destination.c_str());
    return code;
}

DWORD commit_directory_reparse_point(
    const std::wstring& temporary,
    const std::wstring& destination
) {
    const DWORD destination_attributes = GetFileAttributesW(destination.c_str());
    if (destination_attributes == INVALID_FILE_ATTRIBUTES) {
        return MoveFileExW(temporary.c_str(), destination.c_str(), MOVEFILE_WRITE_THROUGH)
            ? ERROR_SUCCESS : GetLastError();
    }
    if ((destination_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
        (destination_attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0) {
        return ERROR_ALREADY_EXISTS;
    }

    const std::wstring displaced = temporary_destination(destination);
    RemoveDirectoryW(displaced.c_str());
    if (!MoveFileExW(destination.c_str(), displaced.c_str(), MOVEFILE_WRITE_THROUGH)) {
        return GetLastError();
    }
    if (!MoveFileExW(temporary.c_str(), destination.c_str(), MOVEFILE_WRITE_THROUGH)) {
        const DWORD code = GetLastError();
        MoveFileExW(displaced.c_str(), destination.c_str(), MOVEFILE_WRITE_THROUGH);
        return code;
    }
    RemoveDirectoryW(displaced.c_str());
    return ERROR_SUCCESS;
}

DWORD apply_directory_metadata(const DirectoryMetadata& metadata) {
    HANDLE handle = CreateFileW(
        metadata.destination.c_str(), FILE_WRITE_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr
    );
    if (handle == INVALID_HANDLE_VALUE) return GetLastError();
    const BOOL time_ok = SetFileTime(handle, &metadata.creation, &metadata.access, &metadata.write);
    DWORD code = time_ok ? ERROR_SUCCESS : GetLastError();
    CloseHandle(handle);
    if (code != ERROR_SUCCESS) return code;

    DWORD attributes = metadata.attributes & ~(
        FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT |
        FILE_ATTRIBUTE_ENCRYPTED | FILE_ATTRIBUTE_COMPRESSED
    );
    if (attributes == 0) attributes = FILE_ATTRIBUTE_NORMAL;
    if (!SetFileAttributesW(metadata.destination.c_str(), attributes)) return GetLastError();
    return ERROR_SUCCESS;
}

std::optional<std::pair<HardLinkIdentity, DWORD>> hard_link_identity(const std::wstring& path) {
    HANDLE handle = CreateFileW(
        path.c_str(), FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr
    );
    if (handle == INVALID_HANDLE_VALUE) return std::nullopt;
    BY_HANDLE_FILE_INFORMATION information{};
    const BOOL ok = GetFileInformationByHandle(handle, &information);
    CloseHandle(handle);
    if (!ok) return std::nullopt;
    return std::make_pair(
        HardLinkIdentity{
            information.dwVolumeSerialNumber,
            information.nFileIndexHigh,
            information.nFileIndexLow
        },
        information.nNumberOfLinks
    );
}

DWORD preserve_sparse_layout(
    const std::wstring& source,
    const std::wstring& destination,
    std::uint64_t size
) {
    HANDLE source_handle = CreateFileW(
        source.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr
    );
    if (source_handle == INVALID_HANDLE_VALUE) return GetLastError();
    HANDLE destination_handle = CreateFileW(
        destination.c_str(), GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, 0, nullptr
    );
    if (destination_handle == INVALID_HANDLE_VALUE) {
        const DWORD code = GetLastError();
        CloseHandle(source_handle);
        return code;
    }

    FILE_SET_SPARSE_BUFFER sparse{TRUE};
    DWORD returned = 0;
    if (!DeviceIoControl(
            destination_handle, FSCTL_SET_SPARSE,
            &sparse, sizeof(sparse), nullptr, 0, &returned, nullptr)) {
        const DWORD code = GetLastError();
        CloseHandle(destination_handle);
        CloseHandle(source_handle);
        return code;
    }

    std::vector<FILE_ALLOCATED_RANGE_BUFFER> ranges;
    std::uint64_t cursor = 0;
    while (cursor < size) {
        FILE_ALLOCATED_RANGE_BUFFER request{};
        request.FileOffset.QuadPart = static_cast<LONGLONG>(cursor);
        request.Length.QuadPart = static_cast<LONGLONG>(size - cursor);
        std::vector<FILE_ALLOCATED_RANGE_BUFFER> output(512);
        returned = 0;
        const BOOL ok = DeviceIoControl(
            source_handle, FSCTL_QUERY_ALLOCATED_RANGES,
            &request, sizeof(request), output.data(),
            static_cast<DWORD>(output.size() * sizeof(output[0])), &returned, nullptr
        );
        const DWORD query_code = ok ? ERROR_SUCCESS : GetLastError();
        if (!ok && query_code != ERROR_MORE_DATA) {
            CloseHandle(destination_handle);
            CloseHandle(source_handle);
            return query_code;
        }
        const std::size_t count = returned / sizeof(FILE_ALLOCATED_RANGE_BUFFER);
        if (count == 0) break;
        ranges.insert(ranges.end(), output.begin(), output.begin() + static_cast<std::ptrdiff_t>(count));
        const auto& last = output[count - 1];
        const std::uint64_t next = static_cast<std::uint64_t>(last.FileOffset.QuadPart + last.Length.QuadPart);
        if (next <= cursor) break;
        cursor = next;
        if (ok) break;
    }

    auto punch = [&](std::uint64_t start, std::uint64_t end) -> DWORD {
        if (end <= start) return ERROR_SUCCESS;
        FILE_ZERO_DATA_INFORMATION zero{};
        zero.FileOffset.QuadPart = static_cast<LONGLONG>(start);
        zero.BeyondFinalZero.QuadPart = static_cast<LONGLONG>(end);
        DWORD ignored = 0;
        if (!DeviceIoControl(
                destination_handle, FSCTL_SET_ZERO_DATA,
                &zero, sizeof(zero), nullptr, 0, &ignored, nullptr)) {
            return GetLastError();
        }
        return ERROR_SUCCESS;
    };

    std::uint64_t allocated_end = 0;
    DWORD code = ERROR_SUCCESS;
    for (const auto& range : ranges) {
        const std::uint64_t start = static_cast<std::uint64_t>(range.FileOffset.QuadPart);
        const std::uint64_t end = start + static_cast<std::uint64_t>(range.Length.QuadPart);
        code = punch(allocated_end, start);
        if (code != ERROR_SUCCESS) break;
        allocated_end = std::max(allocated_end, end);
    }
    if (code == ERROR_SUCCESS) code = punch(allocated_end, size);
    CloseHandle(destination_handle);
    CloseHandle(source_handle);
    return code;
}

DWORD set_ntfs_compression(const std::wstring& destination) {
    HANDLE handle = CreateFileW(
        destination.c_str(), GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, 0, nullptr
    );
    if (handle == INVALID_HANDLE_VALUE) return GetLastError();
    USHORT format = COMPRESSION_FORMAT_DEFAULT;
    DWORD returned = 0;
    const BOOL ok = DeviceIoControl(
        handle, FSCTL_SET_COMPRESSION,
        &format, sizeof(format), nullptr, 0, &returned, nullptr
    );
    const DWORD code = ok ? ERROR_SUCCESS : GetLastError();
    CloseHandle(handle);
    return code;
}

DWORD sha256_file(
    const std::wstring& path,
    const std::atomic_bool& cancelled,
    std::vector<unsigned char>& digest,
    bool unbuffered = false
) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD object_size = 0;
    DWORD hash_size = 0;
    DWORD returned = 0;
    std::vector<unsigned char> hash_object;
    HANDLE file = INVALID_HANDLE_VALUE;
    DWORD code = ERROR_SUCCESS;

    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) {
        return ERROR_NOT_SUPPORTED;
    }
    if (BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&object_size), sizeof(object_size), &returned, 0) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_HASH_LENGTH,
                          reinterpret_cast<PUCHAR>(&hash_size), sizeof(hash_size), &returned, 0) < 0) {
        code = ERROR_INVALID_DATA;
        goto cleanup;
    }
    hash_object.resize(object_size);
    digest.resize(hash_size);
    if (BCryptCreateHash(algorithm, &hash, hash_object.data(), object_size, nullptr, 0, 0) < 0) {
        code = ERROR_INVALID_DATA;
        goto cleanup;
    }
    file = CreateFileW(
        path.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING,
        FILE_FLAG_SEQUENTIAL_SCAN | (unbuffered ? FILE_FLAG_NO_BUFFERING : 0), nullptr
    );
    if (file == INVALID_HANDLE_VALUE) {
        code = GetLastError();
        goto cleanup;
    }
    {
        constexpr DWORD buffer_size = 4 * 1024 * 1024;
        auto* buffer = static_cast<unsigned char*>(_aligned_malloc(buffer_size, 64 * 1024));
        if (!buffer) {
            code = ERROR_NOT_ENOUGH_MEMORY;
            goto cleanup;
        }
        while (!cancelled.load(std::memory_order_relaxed)) {
            DWORD read = 0;
            if (!ReadFile(file, buffer, buffer_size, &read, nullptr)) {
                code = GetLastError();
                _aligned_free(buffer);
                goto cleanup;
            }
            if (read == 0) break;
            if (BCryptHashData(hash, buffer, read, 0) < 0) {
                code = ERROR_INVALID_DATA;
                _aligned_free(buffer);
                goto cleanup;
            }
        }
        _aligned_free(buffer);
        if (cancelled.load(std::memory_order_relaxed)) {
            code = ERROR_REQUEST_ABORTED;
            goto cleanup;
        }
    }
    if (BCryptFinishHash(hash, digest.data(), hash_size, 0) < 0) code = ERROR_INVALID_DATA;

cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    return code;
}

DWORD verify_file_pair(
    const std::wstring& source,
    const std::wstring& destination,
    const std::atomic_bool& cancelled
) {
    HANDLE destination_handle = CreateFileW(
        destination.c_str(), GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr
    );
    if (destination_handle == INVALID_HANDLE_VALUE) return GetLastError();
    const BOOL flushed = FlushFileBuffers(destination_handle);
    const DWORD flush_code = flushed ? ERROR_SUCCESS : GetLastError();
    CloseHandle(destination_handle);
    if (flush_code != ERROR_SUCCESS) return flush_code;

    std::vector<unsigned char> source_hash;
    std::vector<unsigned char> destination_hash;
    DWORD code = sha256_file(source, cancelled, source_hash);
    if (code != ERROR_SUCCESS) return code;
    code = sha256_file(destination, cancelled, destination_hash, true);
    if (code != ERROR_SUCCESS) return code;
    return source_hash == destination_hash ? ERROR_SUCCESS : ERROR_CRC;
}

DWORD normal_copy_flags(
    const CopyJob& job,
    const std::wstring& copy_target,
    const CopyOptions& options
) {
    constexpr DWORD copy_file_request_compressed_traffic = 0x10000000u;
    DWORD flags = COPY_FILE_FAIL_IF_EXISTS;
    const bool network_path = is_unc_path(job.source) || is_unc_path(copy_target);
    if (network_path && options.request_smb_compression) {
        flags |= copy_file_request_compressed_traffic;
    } else if (!network_path && options.use_unbuffered_large_file_copy &&
               job.size >= options.unbuffered_copy_threshold) {
        flags |= COPY_FILE_NO_BUFFERING;
    }
    return flags;
}

enum class PrivilegedCopyStatus { success, fallback, failed };

struct PrivilegedCopyResult {
    PrivilegedCopyStatus status = PrivilegedCopyStatus::fallback;
    DWORD code = ERROR_SUCCESS;
    std::uint64_t transferred = 0;
};

PrivilegedCopyResult copy_preallocated_file(
    const CopyJob& job,
    const std::wstring& destination,
    const std::atomic_bool& cancelled,
    std::atomic<std::uint64_t>& bytes_copied,
    std::atomic<std::uint64_t>& bytes_processed
) {
    HANDLE token = nullptr;
    std::vector<unsigned char> token_user;
    BYTE system_sid_buffer[SECURITY_MAX_SID_SIZE]{};
    DWORD system_sid_size = sizeof(system_sid_buffer);
    ACL* acl = nullptr;
    HANDLE source = INVALID_HANDLE_VALUE;
    HANDLE target = INVALID_HANDLE_VALUE;
    PrivilegedCopyResult result{};

    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return result;
    DWORD token_size = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &token_size);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || token_size == 0) goto cleanup;
    token_user.resize(token_size);
    if (!GetTokenInformation(token, TokenUser, token_user.data(), token_size, &token_size)) goto cleanup;
    if (!CreateWellKnownSid(WinLocalSystemSid, nullptr, system_sid_buffer, &system_sid_size)) goto cleanup;
    {
        const auto* user = reinterpret_cast<const TOKEN_USER*>(token_user.data());
        const DWORD acl_size = sizeof(ACL) +
            sizeof(ACCESS_ALLOWED_ACE) - sizeof(DWORD) + GetLengthSid(user->User.Sid) +
            sizeof(ACCESS_ALLOWED_ACE) - sizeof(DWORD) + GetLengthSid(system_sid_buffer);
        acl = static_cast<ACL*>(LocalAlloc(LPTR, acl_size));
        if (!acl || !InitializeAcl(acl, acl_size, ACL_REVISION) ||
            !AddAccessAllowedAceEx(acl, ACL_REVISION, 0, FILE_ALL_ACCESS, user->User.Sid) ||
            !AddAccessAllowedAceEx(acl, ACL_REVISION, 0, FILE_ALL_ACCESS, system_sid_buffer)) {
            goto cleanup;
        }
        SECURITY_DESCRIPTOR descriptor{};
        if (!InitializeSecurityDescriptor(&descriptor, SECURITY_DESCRIPTOR_REVISION) ||
            !SetSecurityDescriptorOwner(&descriptor, user->User.Sid, FALSE) ||
            !SetSecurityDescriptorDacl(&descriptor, TRUE, acl, FALSE)) {
            goto cleanup;
        }
        SECURITY_ATTRIBUTES security{sizeof(security), &descriptor, FALSE};
        source = CreateFileW(
            job.source.c_str(), GENERIC_READ | FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr
        );
        if (source == INVALID_HANDLE_VALUE) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = GetLastError();
            goto cleanup;
        }
        target = CreateFileW(
            destination.c_str(), GENERIC_WRITE | FILE_WRITE_ATTRIBUTES,
            0, &security, CREATE_NEW,
            FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_SEQUENTIAL_SCAN,
            nullptr
        );
    }
    if (target == INVALID_HANDLE_VALUE) {
        result.status = PrivilegedCopyStatus::failed;
        result.code = GetLastError();
        goto cleanup;
    }
    {
        LARGE_INTEGER size{};
        size.QuadPart = static_cast<LONGLONG>(job.size);
        if (!SetFilePointerEx(target, size, nullptr, FILE_BEGIN) || !SetEndOfFile(target)) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = GetLastError();
            goto cleanup;
        }
        if (!SetFileValidData(target, size.QuadPart)) {
            result.status = PrivilegedCopyStatus::fallback;
            result.code = GetLastError();
            goto cleanup;
        }
        LARGE_INTEGER start{};
        if (!SetFilePointerEx(target, start, nullptr, FILE_BEGIN)) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = GetLastError();
            goto cleanup;
        }

        constexpr DWORD buffer_size = 4 * 1024 * 1024;
        std::vector<unsigned char> buffer(buffer_size);
        while (!cancelled.load(std::memory_order_relaxed)) {
            DWORD read = 0;
            if (!ReadFile(source, buffer.data(), buffer_size, &read, nullptr)) {
                result.status = PrivilegedCopyStatus::failed;
                result.code = GetLastError();
                goto cleanup;
            }
            if (read == 0) break;
            DWORD offset = 0;
            while (offset < read) {
                DWORD written = 0;
                if (!WriteFile(target, buffer.data() + offset, read - offset, &written, nullptr) || written == 0) {
                    result.status = PrivilegedCopyStatus::failed;
                    result.code = GetLastError();
                    goto cleanup;
                }
                offset += written;
                result.transferred += written;
                bytes_copied.fetch_add(written, std::memory_order_relaxed);
                bytes_processed.fetch_add(written, std::memory_order_relaxed);
            }
        }
        if (cancelled.load(std::memory_order_relaxed)) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = ERROR_REQUEST_ABORTED;
            goto cleanup;
        }
        if (result.transferred != job.size) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = ERROR_HANDLE_EOF;
            goto cleanup;
        }
        FILETIME created{}, accessed{}, written{};
        if (!GetFileTime(source, &created, &accessed, &written) ||
            !SetFileTime(target, &created, &accessed, &written) ||
            !FlushFileBuffers(target)) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = GetLastError();
            goto cleanup;
        }
        result.status = PrivilegedCopyStatus::success;
    }

cleanup:
    if (target != INVALID_HANDLE_VALUE) CloseHandle(target);
    if (source != INVALID_HANDLE_VALUE) CloseHandle(source);
    if (token) CloseHandle(token);
    if (acl) LocalFree(acl);
    if (result.status == PrivilegedCopyStatus::success) {
        DWORD attributes = job.attributes & ~(
            FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT |
            FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED |
            FILE_ATTRIBUTE_ENCRYPTED | FILE_ATTRIBUTE_OFFLINE |
            FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        );
        if (attributes == 0) attributes = FILE_ATTRIBUTE_NORMAL;
        if (!SetFileAttributesW(destination.c_str(), attributes)) {
            result.status = PrivilegedCopyStatus::failed;
            result.code = GetLastError();
        }
    }
    if (result.status != PrivilegedCopyStatus::success) DeleteFileW(destination.c_str());
    return result;
}

struct ProgressContext {
    std::atomic_bool* cancelled = nullptr;
    std::atomic<std::uint64_t>* bytes_copied = nullptr;
    std::atomic<std::uint64_t>* bytes_processed = nullptr;
    std::uint64_t last_total = 0;
};

DWORD CALLBACK copy_progress_routine(
    LARGE_INTEGER total_file_size,
    LARGE_INTEGER total_bytes_transferred,
    LARGE_INTEGER,
    LARGE_INTEGER,
    DWORD,
    DWORD,
    HANDLE,
    HANDLE,
    LPVOID context
) {
    auto* state = static_cast<ProgressContext*>(context);
    const auto transferred = static_cast<std::uint64_t>(total_bytes_transferred.QuadPart);
    if (transferred > state->last_total) {
        const auto delta = transferred - state->last_total;
        state->bytes_copied->fetch_add(delta, std::memory_order_relaxed);
        state->bytes_processed->fetch_add(delta, std::memory_order_relaxed);
        state->last_total = transferred;
    }
    if (state->cancelled->load(std::memory_order_relaxed)) return PROGRESS_CANCEL;
    (void)total_file_size;
    return PROGRESS_CONTINUE;
}

} // namespace

struct CopySession::Impl {
    std::atomic_bool cancelled{false};
    BoundedQueue<CopyJob>* active_queue = nullptr;
    unsigned int workers_used = 0;

    std::atomic<std::uint64_t> files_found{0};
    std::atomic<std::uint64_t> directories_found{0};
    std::atomic<std::uint64_t> total_bytes{0};
    std::atomic<std::uint64_t> files_copied{0};
    std::atomic<std::uint64_t> files_skipped{0};
    std::atomic<std::uint64_t> files_resumed{0};
    std::atomic<std::uint64_t> bytes_copied{0};
    std::atomic<std::uint64_t> bytes_processed{0};
    std::atomic<std::uint64_t> error_count{0};
    std::atomic_bool scan_complete{false};

    std::mutex errors_mutex;
    std::vector<CopyError> errors;
    std::mutex current_mutex;
    std::wstring current_path;
    std::chrono::steady_clock::time_point started;

    void reset() {
        cancelled = false;
        active_queue = nullptr;
        workers_used = 0;
        files_found = 0;
        directories_found = 0;
        total_bytes = 0;
        files_copied = 0;
        files_skipped = 0;
        files_resumed = 0;
        bytes_copied = 0;
        bytes_processed = 0;
        error_count = 0;
        scan_complete = false;
        {
            std::lock_guard lock(errors_mutex);
            errors.clear();
        }
        {
            std::lock_guard lock(current_mutex);
            current_path.clear();
        }
        started = std::chrono::steady_clock::now();
    }

    void add_error(const std::wstring& path, DWORD code, std::wstring message = {}) {
        if (message.empty()) message = format_windows_error(code);
        {
            std::lock_guard lock(errors_mutex);
            errors.push_back(CopyError{path, code, std::move(message)});
        }
        error_count.fetch_add(1, std::memory_order_relaxed);
    }

    CopyProgress snapshot(bool complete = false) {
        CopyProgress p;
        p.workers_used = workers_used;
        p.files_found = files_found.load(std::memory_order_relaxed);
        p.directories_found = directories_found.load(std::memory_order_relaxed);
        p.total_bytes = total_bytes.load(std::memory_order_relaxed);
        p.files_copied = files_copied.load(std::memory_order_relaxed);
        p.files_skipped = files_skipped.load(std::memory_order_relaxed);
        p.files_resumed = files_resumed.load(std::memory_order_relaxed);
        p.bytes_copied = bytes_copied.load(std::memory_order_relaxed);
        p.bytes_processed = bytes_processed.load(std::memory_order_relaxed);
        p.error_count = error_count.load(std::memory_order_relaxed);
        p.scan_complete = scan_complete.load(std::memory_order_relaxed);
        p.complete = complete;
        p.cancelled = cancelled.load(std::memory_order_relaxed);
        p.elapsed_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        {
            std::lock_guard lock(current_mutex);
            p.current_path = current_path;
        }
        return p;
    }
};

std::wstring format_windows_error(unsigned long code) {
    wchar_t* buffer = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        code,
        0,
        reinterpret_cast<wchar_t*>(&buffer),
        0,
        nullptr
    );
    std::wstring message = length && buffer ? std::wstring(buffer, length) : L"Unknown Windows error";
    if (buffer) LocalFree(buffer);
    while (!message.empty() && (message.back() == L'\r' || message.back() == L'\n')) message.pop_back();
    return message;
}

std::wstring normalize_long_path(const std::wstring& path) {
    if (path.rfind(L"\\\\?\\", 0) == 0) return path;
    std::vector<wchar_t> buffer(32768);
    const DWORD length = GetFullPathNameW(path.c_str(), static_cast<DWORD>(buffer.size()), buffer.data(), nullptr);
    std::wstring full = length > 0 && length < buffer.size() ? std::wstring(buffer.data(), length) : path;
    if (full.rfind(L"\\\\", 0) == 0) return L"\\\\?\\UNC\\" + full.substr(2);
    return L"\\\\?\\" + full;
}

unsigned int automatic_worker_count(
    const std::wstring& destination,
    unsigned int detected_processors
) {
    if (destination.rfind(L"\\\\?\\UNC\\", 0) == 0) {
        return 8;
    }

    constexpr DWORD cloud_attributes = FILE_ATTRIBUTE_OFFLINE |
        FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS;
    std::wstring cursor = destination;
    for (unsigned int depth = 0; depth < 64 && !cursor.empty(); ++depth) {
        const DWORD attributes = GetFileAttributesW(cursor.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES &&
            ((attributes & cloud_attributes) != 0 ||
             (depth > 0 && (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0))) {
            return 12;
        }
        const auto parent = parent_path(cursor);
        if (parent.empty() || path_equal_case_insensitive(parent, cursor)) break;
        cursor = parent;
    }

    return std::clamp(detected_processors * 2u, 4u, 16u);
}

CopySession::CopySession() : impl_(new Impl()) {}
CopySession::~CopySession() { delete impl_; }

void CopySession::cancel() noexcept {
    impl_->cancelled.store(true, std::memory_order_relaxed);
    if (impl_->active_queue) impl_->active_queue->wake_all();
}

bool CopySession::cancellation_requested() const noexcept {
    return impl_->cancelled.load(std::memory_order_relaxed);
}

CopyResult CopySession::run(
    const std::vector<std::wstring>& raw_sources,
    const std::wstring& raw_destination,
    const CopyOptions& options,
    ProgressCallback progress_callback
) {
    impl_->reset();
    BoundedQueue<CopyJob> queue(4096);
    impl_->active_queue = &queue;
    std::vector<DirectoryMetadata> directory_metadata;
    std::vector<PendingHardLink> pending_hard_links;
    std::unordered_map<HardLinkIdentity, std::wstring, HardLinkIdentityHash> first_hard_link;

    if (options.preserve_security) {
        enable_token_privilege(SE_BACKUP_NAME);
        enable_token_privilege(SE_RESTORE_NAME);
    }
    const bool privileged_preallocation_available =
        options.use_privileged_preallocation && options.preserve_security &&
        enable_token_privilege(SE_MANAGE_VOLUME_NAME);
    if (options.preserve_sacl && !enable_token_privilege(SE_SECURITY_NAME)) {
        impl_->add_error(
            raw_destination, ERROR_PRIVILEGE_NOT_HELD,
            L"SACL preservation requires an elevated process with SeSecurityPrivilege."
        );
        impl_->scan_complete = true;
        CopyResult result{impl_->snapshot(true), {}};
        std::lock_guard lock(impl_->errors_mutex);
        result.errors = impl_->errors;
        impl_->active_queue = nullptr;
        return result;
    }

    const auto destination_directory = normalize_long_path(raw_destination);
    if (!ensure_directory(destination_directory)) {
        const DWORD code = GetLastError();
        impl_->add_error(raw_destination, code);
        impl_->scan_complete = true;
        CopyResult result{impl_->snapshot(true), {}};
        std::lock_guard lock(impl_->errors_mutex);
        result.errors = impl_->errors;
        impl_->active_queue = nullptr;
        return result;
    }

    ResumeJournal resume_journal(
        resume_journal_path(raw_sources, destination_directory, options.directory_layout),
        options.enable_resume_journal
    );

    const unsigned int detected = std::max(1u, std::thread::hardware_concurrency());
    const unsigned int worker_count = options.workers == 0
        ? automatic_worker_count(destination_directory, detected)
        : options.workers;
    impl_->workers_used = worker_count;

    auto scanner = std::thread([&, this] {
        struct PendingDirectory { std::wstring source; std::wstring destination; };

        auto enqueue_file = [&](CopyJob job) {
            if (options.preserve_hard_links && !job.reparse_point) {
                const auto identity = hard_link_identity(job.source);
                if (identity && identity->second > 1) {
                    const auto found = first_hard_link.find(identity->first);
                    if (found != first_hard_link.end()) {
                        pending_hard_links.push_back({
                            job.source, job.destination, found->second, job.last_write
                        });
                        impl_->files_found.fetch_add(1, std::memory_order_relaxed);
                        return true;
                    }
                    first_hard_link.emplace(identity->first, job.destination);
                }
            }
            impl_->files_found.fetch_add(1, std::memory_order_relaxed);
            impl_->total_bytes.fetch_add(job.size, std::memory_order_relaxed);
            return queue.push(std::move(job), impl_->cancelled);
        };

        auto ensure_copy_directory = [&](const std::wstring& source_path,
                                         const std::wstring& destination_path,
                                         DWORD attributes) -> bool {
            const DWORD destination_attributes = GetFileAttributesW(destination_path.c_str());
            if (destination_attributes != INVALID_FILE_ATTRIBUTES) {
                if ((destination_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
                    impl_->add_error(destination_path, ERROR_DIRECTORY);
                    return false;
                }
                if (options.preserve_encryption &&
                    (attributes & FILE_ATTRIBUTE_ENCRYPTED) != 0 &&
                    (destination_attributes & FILE_ATTRIBUTE_ENCRYPTED) == 0 &&
                    !EncryptFileW(destination_path.c_str())) {
                    impl_->add_error(
                        destination_path, GetLastError(),
                        L"Existing destination directory could not be enabled for EFS encryption."
                    );
                    return false;
                }
                return true;
            }

            if (options.preserve_encryption &&
                (attributes & FILE_ATTRIBUTE_ENCRYPTED) != 0) {
                const DWORD encryption_code = copy_encrypted_raw(
                    source_path, destination_path, true, impl_->cancelled
                );
                if (encryption_code != ERROR_SUCCESS) {
                    impl_->add_error(
                        source_path, encryption_code,
                        L"EFS directory preservation failed: " + format_windows_error(encryption_code)
                    );
                    return false;
                }
                return true;
            }

            if (!ensure_directory(destination_path)) {
                impl_->add_error(destination_path, GetLastError());
                return false;
            }
            return true;
        };

        for (const auto& raw_source : raw_sources) {
            if (impl_->cancelled.load(std::memory_order_relaxed)) break;
            const auto source = normalize_long_path(raw_source);
            const DWORD root_attributes = GetFileAttributesW(source.c_str());
            if (root_attributes == INVALID_FILE_ATTRIBUTES) {
                impl_->add_error(raw_source, GetLastError());
                continue;
            }
            const bool source_is_directory = (root_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
            const bool merge_directory_contents = source_is_directory &&
                (root_attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 &&
                options.directory_layout == DirectoryLayout::contents;
            const auto root_destination = merge_directory_contents
                ? destination_directory
                : join_path(destination_directory, leaf_name(raw_source));
            if (path_equal_case_insensitive(source, root_destination) ||
                (source_is_directory &&
                 path_is_inside(root_destination, source))) {
                impl_->add_error(
                    raw_source, ERROR_INVALID_PARAMETER,
                    L"The destination resolves to the source itself or a directory inside it."
                );
                continue;
            }

            if ((root_attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
                WIN32_FILE_ATTRIBUTE_DATA data{};
                if (!GetFileAttributesExW(source.c_str(), GetFileExInfoStandard, &data)) {
                    impl_->add_error(raw_source, GetLastError());
                    continue;
                }
                const bool is_directory = (root_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
                CopyJob job{source, root_destination, 0, data.ftLastWriteTime,
                            data.dwFileAttributes, true, is_directory};
                enqueue_file(std::move(job));
                continue;
            }

            if ((root_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
                WIN32_FILE_ATTRIBUTE_DATA data{};
                if (!GetFileAttributesExW(source.c_str(), GetFileExInfoStandard, &data)) {
                    impl_->add_error(raw_source, GetLastError());
                    continue;
                }
                CopyJob job{source, root_destination,
                            (static_cast<std::uint64_t>(data.nFileSizeHigh) << 32) | data.nFileSizeLow,
                            data.ftLastWriteTime, data.dwFileAttributes, false, false};
                enqueue_file(std::move(job));
                continue;
            }

            if (!merge_directory_contents &&
                !ensure_copy_directory(source, root_destination, root_attributes)) {
                continue;
            }
            if (merge_directory_contents && !ensure_directory(root_destination)) {
                impl_->add_error(root_destination, GetLastError());
                continue;
            }
            if (!merge_directory_contents) {
                WIN32_FILE_ATTRIBUTE_DATA root_data{};
                if (GetFileAttributesExW(source.c_str(), GetFileExInfoStandard, &root_data)) {
                    directory_metadata.push_back({
                        source, root_destination, root_data.ftCreationTime,
                        root_data.ftLastAccessTime, root_data.ftLastWriteTime,
                        root_data.dwFileAttributes
                    });
                }
                impl_->directories_found.fetch_add(1, std::memory_order_relaxed);
            }
            std::vector<PendingDirectory> pending{{source, root_destination}};

            while (!pending.empty() && !impl_->cancelled.load(std::memory_order_relaxed)) {
                PendingDirectory current = std::move(pending.back());
                pending.pop_back();
                const auto pattern = join_path(current.source, L"*");
                WIN32_FIND_DATAW data{};
                HANDLE find = FindFirstFileExW(
                    pattern.c_str(), FindExInfoBasic, &data, FindExSearchNameMatch, nullptr,
                    FIND_FIRST_EX_LARGE_FETCH
                );
                if (find == INVALID_HANDLE_VALUE) {
                    impl_->add_error(current.source, GetLastError());
                    continue;
                }

                do {
                    if (impl_->cancelled.load(std::memory_order_relaxed)) break;
                    const std::wstring_view name(data.cFileName);
                    if (name == L"." || name == L"..") continue;
                    const auto source_path = join_path(current.source, std::wstring(name));
                    const auto destination_path = join_path(current.destination, std::wstring(name));
                    if ((data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
                        const bool is_directory = (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
                        CopyJob job{source_path, destination_path, 0, data.ftLastWriteTime,
                                    data.dwFileAttributes, true, is_directory};
                        if (!enqueue_file(std::move(job))) break;
                    } else if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                        if (ensure_copy_directory(source_path, destination_path, data.dwFileAttributes)) {
                            impl_->directories_found.fetch_add(1, std::memory_order_relaxed);
                            directory_metadata.push_back({
                                source_path, destination_path, data.ftCreationTime,
                                data.ftLastAccessTime, data.ftLastWriteTime,
                                data.dwFileAttributes
                            });
                            pending.push_back({source_path, destination_path});
                        }
                    } else {
                        CopyJob job{source_path, destination_path, file_size(data), data.ftLastWriteTime,
                                    data.dwFileAttributes, false, false};
                        if (!enqueue_file(std::move(job))) break;
                    }
                } while (FindNextFileW(find, &data));
                const DWORD end_code = GetLastError();
                FindClose(find);
                if (end_code != ERROR_NO_MORE_FILES && !impl_->cancelled.load(std::memory_order_relaxed)) {
                    impl_->add_error(current.source, end_code);
                }
            }
        }
        impl_->scan_complete.store(true, std::memory_order_relaxed);
        queue.close();
    });

    std::vector<std::thread> workers;
    workers.reserve(worker_count);
    for (unsigned int index = 0; index < worker_count; ++index) {
        workers.emplace_back([&, this] {
            while (!impl_->cancelled.load(std::memory_order_relaxed)) {
                auto maybe_job = queue.pop(impl_->cancelled);
                if (!maybe_job) break;
                auto& job = *maybe_job;
                {
                    std::lock_guard lock(impl_->current_mutex);
                    impl_->current_path = job.source;
                }

                if (resume_journal.resuming()) remove_stale_temporaries(job.destination);
                if (!job.reparse_point && resume_journal.completed(job)) {
                    impl_->files_resumed.fetch_add(1, std::memory_order_relaxed);
                    impl_->bytes_processed.fetch_add(job.size, std::memory_order_relaxed);
                    continue;
                }

                WIN32_FILE_ATTRIBUTE_DATA destination_data{};
                const bool destination_exists = exists(job.destination, &destination_data);
                if (destination_exists) {
                    bool skip = options.conflict == ConflictPolicy::skip;
                    if (options.conflict == ConflictPolicy::overwrite_if_newer) {
                        skip = CompareFileTime(&job.last_write, &destination_data.ftLastWriteTime) <= 0;
                    }
                    if (skip) {
                        impl_->files_skipped.fetch_add(1, std::memory_order_relaxed);
                        impl_->bytes_processed.fetch_add(job.size, std::memory_order_relaxed);
                        continue;
                    }
                }

                if (!ensure_directory(parent_path(job.destination))) {
                    impl_->add_error(job.destination, GetLastError());
                    impl_->bytes_processed.fetch_add(job.size, std::memory_order_relaxed);
                    continue;
                }

                const std::wstring copy_target = temporary_destination(job.destination);
                DeleteFileW(copy_target.c_str());
                RemoveDirectoryW(copy_target.c_str());

                if (job.reparse_point) {
                    if (!options.preserve_reparse_points) {
                        impl_->files_skipped.fetch_add(1, std::memory_order_relaxed);
                        continue;
                    }
                    const DWORD reparse_code = copy_reparse_point(job.source, copy_target, job.directory);
                    if (reparse_code != ERROR_SUCCESS) {
                        impl_->add_error(
                            job.source, reparse_code,
                            L"Reparse-point preservation failed: " + format_windows_error(reparse_code)
                        );
                        continue;
                    }
                    if (options.preserve_security) {
                        const DWORD security_code = copy_security_descriptor(
                            job.source, copy_target, options.preserve_sacl
                        );
                        if (security_code != ERROR_SUCCESS) {
                            job.directory ? RemoveDirectoryW(copy_target.c_str()) : DeleteFileW(copy_target.c_str());
                            impl_->add_error(job.source, security_code,
                                             L"Security descriptor preservation failed: " + format_windows_error(security_code));
                            continue;
                        }
                    }
                    if (options.preserve_extended_attributes) {
                        const DWORD ea_code = copy_extended_attributes(job.source, copy_target);
                        if (ea_code != ERROR_SUCCESS) {
                            job.directory ? RemoveDirectoryW(copy_target.c_str()) : DeleteFileW(copy_target.c_str());
                            impl_->add_error(
                                job.source, ea_code,
                                L"Extended-attribute preservation failed: " + format_windows_error(ea_code)
                            );
                            continue;
                        }
                    }
                    const DWORD commit_code = job.directory
                        ? commit_directory_reparse_point(copy_target, job.destination)
                        : (MoveFileExW(
                               copy_target.c_str(), job.destination.c_str(),
                               MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)
                               ? ERROR_SUCCESS : GetLastError());
                    if (commit_code != ERROR_SUCCESS) {
                        job.directory ? RemoveDirectoryW(copy_target.c_str()) : DeleteFileW(copy_target.c_str());
                        impl_->add_error(job.destination, commit_code,
                                         L"The reparse point could not replace the destination: " + format_windows_error(commit_code));
                        continue;
                    }
                    impl_->files_copied.fetch_add(1, std::memory_order_relaxed);
                    resume_journal.mark(job);
                    continue;
                }

                const bool encrypted_raw = options.preserve_encryption &&
                    (job.attributes & FILE_ATTRIBUTE_ENCRYPTED) != 0;
                if (encrypted_raw) {
                    const DWORD encryption_code = copy_encrypted_raw(
                        job.source, copy_target, false, impl_->cancelled
                    );
                    if (encryption_code != ERROR_SUCCESS) {
                        if (encryption_code != ERROR_OPERATION_ABORTED) {
                            impl_->bytes_processed.fetch_add(job.size, std::memory_order_relaxed);
                            impl_->add_error(
                                job.source, encryption_code,
                                L"EFS raw preservation failed: " + format_windows_error(encryption_code)
                            );
                        }
                        continue;
                    }
                    impl_->bytes_copied.fetch_add(job.size, std::memory_order_relaxed);
                    impl_->bytes_processed.fetch_add(job.size, std::memory_order_relaxed);
                } else {
                    constexpr DWORD excluded_attributes = FILE_ATTRIBUTE_REPARSE_POINT |
                        FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED |
                        FILE_ATTRIBUTE_ENCRYPTED | FILE_ATTRIBUTE_OFFLINE |
                        FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS;
                    const bool preallocation_candidate = privileged_preallocation_available &&
                        job.size >= options.privileged_preallocation_threshold &&
                        (job.attributes & excluded_attributes) == 0 &&
                        is_fixed_local_path(job.source) && is_fixed_local_path(copy_target) &&
                        !has_cloud_or_reparse_component(job.source) &&
                        !has_cloud_or_reparse_component(copy_target) &&
                        !has_named_data_stream(job.source);
                    PrivilegedCopyResult privileged_result{};
                    if (preallocation_candidate) {
                        privileged_result = copy_preallocated_file(
                            job, copy_target, impl_->cancelled,
                            impl_->bytes_copied, impl_->bytes_processed
                        );
                    }
                    if (!preallocation_candidate ||
                        privileged_result.status == PrivilegedCopyStatus::fallback) {
                        ProgressContext context{
                            &impl_->cancelled, &impl_->bytes_copied, &impl_->bytes_processed, 0
                        };
                        const BOOL ok = CopyFileExW(
                            job.source.c_str(), copy_target.c_str(), copy_progress_routine,
                            &context, nullptr, normal_copy_flags(job, copy_target, options)
                        );
                        if (!ok) {
                            const DWORD code = GetLastError();
                            impl_->bytes_copied.fetch_sub(context.last_total, std::memory_order_relaxed);
                            if (code != ERROR_REQUEST_ABORTED) {
                                impl_->bytes_processed.fetch_add(
                                    job.size - std::min(job.size, context.last_total),
                                    std::memory_order_relaxed
                                );
                                impl_->add_error(job.source, code);
                            }
                            DeleteFileW(copy_target.c_str());
                            continue;
                        }
                        if (context.last_total < job.size) {
                            const auto remaining = job.size - context.last_total;
                            impl_->bytes_copied.fetch_add(remaining, std::memory_order_relaxed);
                            impl_->bytes_processed.fetch_add(remaining, std::memory_order_relaxed);
                        }
                    } else if (privileged_result.status == PrivilegedCopyStatus::failed) {
                        impl_->bytes_copied.fetch_sub(privileged_result.transferred, std::memory_order_relaxed);
                        if (privileged_result.code != ERROR_REQUEST_ABORTED) {
                            impl_->bytes_processed.fetch_add(
                                job.size - std::min(job.size, privileged_result.transferred),
                                std::memory_order_relaxed
                            );
                            impl_->add_error(
                                job.source, privileged_result.code,
                                L"Privileged preallocated copy failed: " +
                                    format_windows_error(privileged_result.code)
                            );
                        }
                        continue;
                    }
                }

                if (options.preserve_extended_attributes) {
                    const DWORD ea_code = copy_extended_attributes(job.source, copy_target);
                    if (ea_code != ERROR_SUCCESS) {
                        DeleteFileW(copy_target.c_str());
                        impl_->bytes_copied.fetch_sub(job.size, std::memory_order_relaxed);
                        impl_->add_error(
                            job.source, ea_code,
                            L"Extended-attribute preservation failed: " + format_windows_error(ea_code)
                        );
                        continue;
                    }
                }

                if (!encrypted_raw && options.preserve_sparse_files &&
                    (job.attributes & FILE_ATTRIBUTE_SPARSE_FILE) != 0) {
                    const DWORD sparse_code = preserve_sparse_layout(job.source, copy_target, job.size);
                    if (sparse_code != ERROR_SUCCESS) {
                        DeleteFileW(copy_target.c_str());
                        impl_->bytes_copied.fetch_sub(job.size, std::memory_order_relaxed);
                        impl_->add_error(
                            job.source, sparse_code,
                            L"Sparse-file layout preservation failed: " + format_windows_error(sparse_code)
                        );
                        continue;
                    }
                }

                if (!encrypted_raw && options.preserve_compression &&
                    (job.attributes & FILE_ATTRIBUTE_COMPRESSED) != 0) {
                    const DWORD compression_code = set_ntfs_compression(copy_target);
                    if (compression_code != ERROR_SUCCESS) {
                        DeleteFileW(copy_target.c_str());
                        impl_->bytes_copied.fetch_sub(job.size, std::memory_order_relaxed);
                        impl_->add_error(
                            job.source, compression_code,
                            L"NTFS compression preservation failed: " + format_windows_error(compression_code)
                        );
                        continue;
                    }
                }

                if (options.verify_contents) {
                    const DWORD verify_code = verify_file_pair(
                        job.source, copy_target, impl_->cancelled
                    );
                    if (verify_code != ERROR_SUCCESS) {
                        DeleteFileW(copy_target.c_str());
                        impl_->bytes_copied.fetch_sub(job.size, std::memory_order_relaxed);
                        if (verify_code != ERROR_REQUEST_ABORTED) {
                            impl_->add_error(
                                job.source, verify_code,
                                L"SHA-256 verification failed: " + format_windows_error(verify_code)
                            );
                        }
                        continue;
                    }
                }

                if (options.preserve_security) {
                    const DWORD security_code = copy_security_descriptor(
                        job.source, copy_target, options.preserve_sacl
                    );
                    if (security_code != ERROR_SUCCESS) {
                        DeleteFileW(copy_target.c_str());
                        impl_->bytes_copied.fetch_sub(job.size, std::memory_order_relaxed);
                        impl_->add_error(
                            job.source, security_code,
                            L"Security descriptor preservation failed: " + format_windows_error(security_code)
                        );
                        continue;
                    }
                }

                DWORD original_attributes = INVALID_FILE_ATTRIBUTES;
                if (destination_exists) {
                    original_attributes = GetFileAttributesW(job.destination.c_str());
                    if (original_attributes != INVALID_FILE_ATTRIBUTES &&
                        (original_attributes & FILE_ATTRIBUTE_READONLY) != 0) {
                        SetFileAttributesW(job.destination.c_str(), original_attributes & ~FILE_ATTRIBUTE_READONLY);
                    }
                }
                if (!MoveFileExW(
                        copy_target.c_str(), job.destination.c_str(),
                        MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
                    const DWORD code = GetLastError();
                    if (destination_exists && original_attributes != INVALID_FILE_ATTRIBUTES) {
                        SetFileAttributesW(job.destination.c_str(), original_attributes);
                    }
                    DeleteFileW(copy_target.c_str());
                    impl_->bytes_copied.fetch_sub(job.size, std::memory_order_relaxed);
                    impl_->add_error(job.destination, code, L"The completed temporary copy could not replace the destination: " + format_windows_error(code));
                    continue;
                }
                impl_->files_copied.fetch_add(1, std::memory_order_relaxed);
                resume_journal.mark(job);
            }
        });
    }

    std::atomic_bool reporter_done{false};
    std::thread reporter;
    if (progress_callback) {
        reporter = std::thread([&, this] {
            while (!reporter_done.load(std::memory_order_relaxed)) {
                progress_callback(impl_->snapshot());
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        });
    }

    if (scanner.joinable()) scanner.join();
    for (auto& worker : workers) if (worker.joinable()) worker.join();
    reporter_done.store(true, std::memory_order_relaxed);
    if (reporter.joinable()) reporter.join();
    impl_->active_queue = nullptr;

    if (!impl_->cancelled.load(std::memory_order_relaxed)) {
        for (const auto& hard_link : pending_hard_links) {
            WIN32_FILE_ATTRIBUTE_DATA destination_data{};
            const bool destination_exists = exists(hard_link.destination, &destination_data);
            if (destination_exists) {
                bool skip = options.conflict == ConflictPolicy::skip;
                if (options.conflict == ConflictPolicy::overwrite_if_newer) {
                    skip = CompareFileTime(&hard_link.last_write, &destination_data.ftLastWriteTime) <= 0;
                }
                if (skip) {
                    impl_->files_skipped.fetch_add(1, std::memory_order_relaxed);
                    continue;
                }
            }

            const std::wstring temporary = temporary_destination(hard_link.destination);
            DeleteFileW(temporary.c_str());
            if (!CreateHardLinkW(temporary.c_str(), hard_link.existing_destination.c_str(), nullptr)) {
                const DWORD code = GetLastError();
                impl_->add_error(hard_link.source, code,
                                 L"Hard-link preservation failed: " + format_windows_error(code));
                continue;
            }
            DWORD original_attributes = INVALID_FILE_ATTRIBUTES;
            if (destination_exists) {
                original_attributes = GetFileAttributesW(hard_link.destination.c_str());
                if (original_attributes != INVALID_FILE_ATTRIBUTES &&
                    (original_attributes & FILE_ATTRIBUTE_READONLY) != 0) {
                    SetFileAttributesW(hard_link.destination.c_str(), original_attributes & ~FILE_ATTRIBUTE_READONLY);
                }
            }
            if (!MoveFileExW(
                    temporary.c_str(), hard_link.destination.c_str(),
                    MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
                const DWORD code = GetLastError();
                DeleteFileW(temporary.c_str());
                if (destination_exists && original_attributes != INVALID_FILE_ATTRIBUTES) {
                    SetFileAttributesW(hard_link.destination.c_str(), original_attributes);
                }
                impl_->add_error(hard_link.destination, code,
                                 L"Hard-link commit failed: " + format_windows_error(code));
                continue;
            }
            impl_->files_copied.fetch_add(1, std::memory_order_relaxed);
        }
    }

    if (!impl_->cancelled.load(std::memory_order_relaxed)) {
        for (auto iterator = directory_metadata.rbegin(); iterator != directory_metadata.rend(); ++iterator) {
            if (options.preserve_extended_attributes) {
                const DWORD ea_code = copy_extended_attributes(iterator->source, iterator->destination);
                if (ea_code != ERROR_SUCCESS) {
                    impl_->add_error(
                        iterator->source, ea_code,
                        L"Directory extended-attribute preservation failed: " + format_windows_error(ea_code)
                    );
                }
            }
            const DWORD metadata_code = apply_directory_metadata(*iterator);
            if (metadata_code != ERROR_SUCCESS) {
                impl_->add_error(
                    iterator->destination, metadata_code,
                    L"Directory timestamp/attribute preservation failed: " + format_windows_error(metadata_code)
                );
            }
            if (options.preserve_security) {
                const DWORD security_code = copy_security_descriptor(
                    iterator->source, iterator->destination, options.preserve_sacl
                );
                if (security_code != ERROR_SUCCESS) {
                    impl_->add_error(
                        iterator->source, security_code,
                        L"Directory security descriptor preservation failed: " + format_windows_error(security_code)
                    );
                }
            }
        }
    }

    CopyResult result;
    result.progress = impl_->snapshot(true);
    {
        std::lock_guard lock(impl_->errors_mutex);
        result.errors = impl_->errors;
    }
    if (!result.progress.cancelled && result.progress.error_count == 0) {
        resume_journal.finish_success();
    }
    if (progress_callback) progress_callback(result.progress);
    return result;
}

} // namespace qfc
