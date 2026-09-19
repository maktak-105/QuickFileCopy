#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include "qfc/copy_engine.h"

#include <algorithm>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace {

std::atomic<qfc::CopySession*> g_active_session{nullptr};

BOOL WINAPI console_control_handler(DWORD signal) {
    if (signal == CTRL_C_EVENT || signal == CTRL_BREAK_EVENT || signal == CTRL_CLOSE_EVENT) {
        if (auto* session = g_active_session.load(std::memory_order_relaxed)) session->cancel();
        return TRUE;
    }
    return FALSE;
}

void print_usage() {
    std::wcout
        << L"QuickFileCopy native CLI\n\n"
        << L"Usage:\n"
        << L"  QuickFileCopy_cli.exe copy <source> <destination-directory> [options]\n\n"
        << L"Options:\n"
        << L"  --policy skip|overwrite|newer\n"
        << L"  --contents (default: merge folder contents into destination)\n"
        << L"  --folder (create the source folder under destination)\n"
        << L"  --add-source PATH (repeatable)\n"
        << L"  --workers N\n"
        << L"  --copy-security\n"
        << L"  --copy-sacl (requires SeSecurityPrivilege)\n"
        << L"  --preserve-ea\n"
        << L"  --preserve-hard-links\n"
        << L"  --verify (SHA-256)\n"
        << L"  --unbuffered-threshold-mib N\n"
        << L"  --quiet\n";
}

qfc::ConflictPolicy parse_policy(const std::wstring& value) {
    if (value == L"overwrite") return qfc::ConflictPolicy::overwrite;
    if (value == L"newer" || value == L"overwrite-if-newer") {
        return qfc::ConflictPolicy::overwrite_if_newer;
    }
    return qfc::ConflictPolicy::skip;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCtrlHandler(console_control_handler, TRUE);
    if (argc < 4 || std::wstring(argv[1]) != L"copy") {
        print_usage();
        return argc == 2 && std::wstring(argv[1]) == L"--help" ? 0 : 2;
    }

    std::vector<std::wstring> sources{argv[2]};
    const std::wstring destination = argv[3];
    qfc::CopyOptions options;
    bool quiet = false;

    for (int i = 4; i < argc; ++i) {
        const std::wstring arg = argv[i];
        if (arg == L"--policy" && i + 1 < argc) {
            options.conflict = parse_policy(argv[++i]);
        } else if (arg == L"--contents") {
            options.directory_layout = qfc::DirectoryLayout::contents;
        } else if (arg == L"--folder") {
            options.directory_layout = qfc::DirectoryLayout::include_source_directory;
        } else if (arg == L"--add-source" && i + 1 < argc) {
            sources.emplace_back(argv[++i]);
        } else if (arg == L"--workers" && i + 1 < argc) {
            options.workers = static_cast<unsigned int>(_wtoi(argv[++i]));
        } else if (arg == L"--quiet") {
            quiet = true;
        } else if (arg == L"--copy-security") {
            options.preserve_security = true;
        } else if (arg == L"--copy-sacl") {
            options.preserve_security = true;
            options.preserve_sacl = true;
        } else if (arg == L"--preserve-ea") {
            options.preserve_extended_attributes = true;
        } else if (arg == L"--preserve-hard-links") {
            options.preserve_hard_links = true;
        } else if (arg == L"--verify") {
            options.verify_contents = true;
        } else if (arg == L"--unbuffered-threshold-mib" && i + 1 < argc) {
            const auto mib = _wtoi64(argv[++i]);
            if (mib < 0) {
                std::wcerr << L"The unbuffered threshold must be zero or greater.\n";
                return 2;
            }
            options.unbuffered_copy_threshold = static_cast<std::uint64_t>(mib) * 1024 * 1024;
        } else {
            std::wcerr << L"Unknown option: " << arg << L"\n";
            return 2;
        }
    }

    qfc::CopySession session;
    g_active_session.store(&session, std::memory_order_relaxed);
    auto result = session.run(sources, destination, options, quiet ? qfc::ProgressCallback{} : [](const qfc::CopyProgress& p) {
        if (!p.complete) {
            const double mbps = p.elapsed_seconds > 0
                ? static_cast<double>(p.bytes_copied) / p.elapsed_seconds / 1024.0 / 1024.0
                : 0.0;
            std::wcout << L"\r";
            if (!p.scan_complete) {
                std::wcout << L"Scanning... " << p.files_found << L" files  ";
            } else {
                const double percent = p.total_bytes > 0
                    ? std::clamp(
                        static_cast<double>(p.bytes_processed) * 100.0 /
                            static_cast<double>(p.total_bytes),
                        0.0, 100.0
                    )
                    : 100.0;
                std::wcout << std::fixed << std::setprecision(1)
                           << percent << L"%  " << mbps << L" MiB/s  "
                           << p.files_copied << L" files";
            }
            std::wcout << std::flush;
        }
    });
    g_active_session.store(nullptr, std::memory_order_relaxed);

    if (!quiet) std::wcout << L"\n";
    for (const auto& error : result.errors) {
        std::wcerr << L"ERROR " << error.code << L"  " << error.path << L": " << error.message << L"\n";
    }
    if (!quiet) {
        std::wcout << L"Copied: " << result.progress.files_copied
                   << L", skipped: " << result.progress.files_skipped
                   << L", resumed: " << result.progress.files_resumed
                   << L", errors: " << result.progress.error_count
                   << L", workers: " << result.progress.workers_used
                   << L", elapsed: " << std::fixed << std::setprecision(2)
                   << result.progress.elapsed_seconds << L" s\n";
    }
    if (result.progress.cancelled) return 3;
    return result.errors.empty() ? 0 : 1;
}
