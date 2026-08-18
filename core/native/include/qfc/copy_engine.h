#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace qfc {

enum class ConflictPolicy {
    skip,
    overwrite,
    overwrite_if_newer,
};

enum class DirectoryLayout {
    contents,
    include_source_directory,
};

struct CopyOptions {
    ConflictPolicy conflict = ConflictPolicy::skip;
    DirectoryLayout directory_layout = DirectoryLayout::contents;
    unsigned int workers = 0;
    bool preserve_reparse_points = true;
    bool preserve_extended_attributes = false;
    bool preserve_encryption = true;
    bool preserve_security = false;
    bool preserve_sacl = false;
    bool preserve_hard_links = false;
    bool preserve_sparse_files = true;
    bool preserve_compression = true;
    bool verify_contents = false;
    bool enable_resume_journal = true;
    bool request_smb_compression = true;
    bool use_unbuffered_large_file_copy = true;
    std::uint64_t unbuffered_copy_threshold = 512ull * 1024 * 1024;
    bool use_privileged_preallocation = true;
    std::uint64_t privileged_preallocation_threshold = 512ull * 1024 * 1024;
};

struct CopyError {
    std::wstring path;
    unsigned long code = 0;
    std::wstring message;
};

struct CopyProgress {
    unsigned int workers_used = 0;
    std::uint64_t files_found = 0;
    std::uint64_t directories_found = 0;
    std::uint64_t total_bytes = 0;
    std::uint64_t files_copied = 0;
    std::uint64_t files_skipped = 0;
    std::uint64_t files_resumed = 0;
    std::uint64_t bytes_copied = 0;
    std::uint64_t bytes_processed = 0;
    std::uint64_t error_count = 0;
    double elapsed_seconds = 0.0;
    bool scan_complete = false;
    bool complete = false;
    bool cancelled = false;
    std::wstring current_path;
};

struct CopyResult {
    CopyProgress progress;
    std::vector<CopyError> errors;
};

using ProgressCallback = std::function<void(const CopyProgress&)>;

class CopySession {
public:
    CopySession();
    ~CopySession();

    CopySession(const CopySession&) = delete;
    CopySession& operator=(const CopySession&) = delete;

    CopyResult run(
        const std::vector<std::wstring>& sources,
        const std::wstring& destination_directory,
        const CopyOptions& options,
        ProgressCallback progress_callback = {}
    );

    void cancel() noexcept;
    bool cancellation_requested() const noexcept;

private:
    struct Impl;
    Impl* impl_;
};

std::wstring format_windows_error(unsigned long code);
std::wstring normalize_long_path(const std::wstring& path);

} // namespace qfc
