#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <unknwn.h>
#include <WebView2.h>

#include "qfc/copy_engine.h"
#include "../resources/resource.h"
#include "resource.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr UINT WM_QFC_WEB_MESSAGE = WM_APP + 1;
constexpr wchar_t kWindowClass[] = L"QuickFileCopyNativeWebView2";

using CreateEnvironmentFn = HRESULT(STDAPICALLTYPE*)(
    PCWSTR,
    PCWSTR,
    ICoreWebView2EnvironmentOptions*,
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler*
);

template <typename Interface>
REFIID callback_iid();

template <>
REFIID callback_iid<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>() {
    return IID_ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler;
}

template <>
REFIID callback_iid<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>() {
    return IID_ICoreWebView2CreateCoreWebView2ControllerCompletedHandler;
}

template <>
REFIID callback_iid<ICoreWebView2WebMessageReceivedEventHandler>() {
    return IID_ICoreWebView2WebMessageReceivedEventHandler;
}

template <typename Interface>
class CallbackBase : public Interface {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
        if (iid == IID_IUnknown || iid == callback_iid<Interface>()) {
            *object = static_cast<Interface*>(this);
            AddRef();
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG value = --references_;
        if (value == 0) delete this;
        return value;
    }
private:
    std::atomic<ULONG> references_{1};
};

class EnvironmentHandler final : public CallbackBase<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler> {
public:
    explicit EnvironmentHandler(std::function<HRESULT(HRESULT, ICoreWebView2Environment*)> fn)
        : fn_(std::move(fn)) {}
    HRESULT STDMETHODCALLTYPE Invoke(HRESULT result, ICoreWebView2Environment* environment) override {
        return fn_(result, environment);
    }
private:
    std::function<HRESULT(HRESULT, ICoreWebView2Environment*)> fn_;
};

class ControllerHandler final : public CallbackBase<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler> {
public:
    explicit ControllerHandler(std::function<HRESULT(HRESULT, ICoreWebView2Controller*)> fn)
        : fn_(std::move(fn)) {}
    HRESULT STDMETHODCALLTYPE Invoke(HRESULT result, ICoreWebView2Controller* controller) override {
        return fn_(result, controller);
    }
private:
    std::function<HRESULT(HRESULT, ICoreWebView2Controller*)> fn_;
};

class MessageHandler final : public CallbackBase<ICoreWebView2WebMessageReceivedEventHandler> {
public:
    explicit MessageHandler(std::function<HRESULT(ICoreWebView2WebMessageReceivedEventArgs*)> fn)
        : fn_(std::move(fn)) {}
    HRESULT STDMETHODCALLTYPE Invoke(ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs* args) override {
        return fn_(args);
    }
private:
    std::function<HRESULT(ICoreWebView2WebMessageReceivedEventArgs*)> fn_;
};

HWND g_window = nullptr;
ICoreWebView2Controller* g_controller = nullptr;
ICoreWebView2* g_webview = nullptr;
HMODULE g_webview_loader = nullptr;
std::vector<std::wstring> g_sources;
std::wstring g_destination;
std::unique_ptr<qfc::CopySession> g_session;
std::thread g_copy_thread;
std::atomic_bool g_running{false};
std::atomic<HANDLE> g_privileged_pipe{INVALID_HANDLE_VALUE};

std::wstring executable_directory() {
    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    buffer.resize(length);
    const auto split = buffer.find_last_of(L"\\/");
    return split == std::wstring::npos ? L"." : buffer.substr(0, split);
}

std::wstring executable_path() {
    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    buffer.resize(length);
    return buffer;
}

std::wstring json_escape(const std::wstring& value) {
    std::wostringstream output;
    for (const wchar_t ch : value) {
        switch (ch) {
            case L'\\': output << L"\\\\"; break;
            case L'"': output << L"\\\""; break;
            case L'\b': output << L"\\b"; break;
            case L'\f': output << L"\\f"; break;
            case L'\n': output << L"\\n"; break;
            case L'\r': output << L"\\r"; break;
            case L'\t': output << L"\\t"; break;
            default:
                if (ch < 0x20) {
                    output << L"\\u" << std::hex << static_cast<unsigned int>(ch) << std::dec;
                } else {
                    output << ch;
                }
        }
    }
    return output.str();
}

std::wstring json_string(const std::wstring& json, const std::wstring& key) {
    const std::wstring token = L"\"" + key + L"\"";
    std::size_t pos = json.find(token);
    if (pos == std::wstring::npos) return {};
    pos = json.find(L':', pos + token.size());
    if (pos == std::wstring::npos) return {};
    pos = json.find(L'"', pos + 1);
    if (pos == std::wstring::npos) return {};
    ++pos;
    std::wstring value;
    bool escaped = false;
    for (; pos < json.size(); ++pos) {
        const wchar_t ch = json[pos];
        if (escaped) {
            switch (ch) {
                case L'n': value.push_back(L'\n'); break;
                case L'r': value.push_back(L'\r'); break;
                case L't': value.push_back(L'\t'); break;
                case L'"': value.push_back(L'"'); break;
                case L'\\': value.push_back(L'\\'); break;
                default: return {};
            }
            escaped = false;
        } else if (ch == L'\\') {
            escaped = true;
        } else if (ch == L'"') {
            return value;
        } else {
            value.push_back(ch);
        }
    }
    return {};
}

void post_json(std::wstring json) {
    if (!g_window) return;
    PostMessageW(g_window, WM_QFC_WEB_MESSAGE, 0, reinterpret_cast<LPARAM>(new std::wstring(std::move(json))));
}

void post_ui_error(const wchar_t* message_key, const std::wstring& detail = {}) {
    std::wstring json = L"{\"version\":1,\"event\":\"error\",\"payload\":{\"messageKey\":\"";
    json += message_key;
    json += L"\"";
    if (!detail.empty()) json += L",\"detail\":\"" + json_escape(detail) + L"\"";
    json += L"}}";
    post_json(std::move(json));
}

const wchar_t* system_language_text(const wchar_t* japanese, const wchar_t* english) {
    return PRIMARYLANGID(GetUserDefaultUILanguage()) == LANG_JAPANESE ? japanese : english;
}

std::wstring progress_json(const qfc::CopyProgress& p) {
    std::wostringstream json;
    const double speed = p.elapsed_seconds > 0
        ? static_cast<double>(p.bytes_copied) / p.elapsed_seconds / 1024.0 / 1024.0
        : 0.0;
    double percent = p.scan_complete && p.total_bytes > 0
        ? static_cast<double>(p.bytes_processed) * 100.0 / static_cast<double>(p.total_bytes)
        : 0.0;
    percent = std::clamp(percent, 0.0, 100.0);
    if (p.complete && !p.cancelled) percent = 100.0;
    json << L"{\"version\":1,\"event\":\"progress\",\"payload\":{"
         << L"\"workers\":" << p.workers_used << L","
         << L"\"filesFound\":" << p.files_found << L","
         << L"\"directoriesFound\":" << p.directories_found << L","
         << L"\"totalBytes\":" << p.total_bytes << L","
         << L"\"filesCopied\":" << p.files_copied << L","
         << L"\"filesSkipped\":" << p.files_skipped << L","
         << L"\"filesResumed\":" << p.files_resumed << L","
         << L"\"bytesCopied\":" << p.bytes_copied << L","
         << L"\"bytesProcessed\":" << p.bytes_processed << L","
         << L"\"errors\":" << p.error_count << L","
         << L"\"elapsed\":" << p.elapsed_seconds << L","
         << L"\"speedMiB\":" << speed << L","
         << L"\"percent\":" << percent << L","
         << L"\"scanComplete\":" << (p.scan_complete ? L"true" : L"false") << L","
         << L"\"complete\":" << (p.complete ? L"true" : L"false") << L","
         << L"\"cancelled\":" << (p.cancelled ? L"true" : L"false") << L","
         << L"\"currentPath\":\"" << json_escape(p.current_path) << L"\"}}";
    return json.str();
}

std::wstring completion_json(const qfc::CopyResult& result) {
    std::wostringstream done;
    done << L"{\"version\":1,\"event\":\"completed\",\"payload\":{"
         << L"\"cancelled\":" << (result.progress.cancelled ? L"true" : L"false") << L","
         << L"\"errors\":" << result.progress.error_count << L",\"errorDetails\":[";
    const std::size_t limit = std::min<std::size_t>(result.errors.size(), 100);
    for (std::size_t index = 0; index < limit; ++index) {
        if (index) done << L",";
        const auto& error = result.errors[index];
        done << L"{\"code\":" << error.code
             << L",\"path\":\"" << json_escape(error.path)
             << L"\",\"message\":\"" << json_escape(error.message) << L"\"}";
    }
    done << L"],\"errorsTruncated\":"
         << (result.errors.size() > limit ? L"true" : L"false") << L"}}";
    return done.str();
}

std::string utf8_from_wide(const std::wstring& value) {
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

std::wstring wide_from_utf8(const std::string& value) {
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

bool pipe_write_all(HANDLE pipe, const void* data, DWORD size) {
    const auto* bytes = static_cast<const unsigned char*>(data);
    DWORD offset = 0;
    while (offset < size) {
        DWORD written = 0;
        if (!WriteFile(pipe, bytes + offset, size - offset, &written, nullptr) || written == 0) {
            return false;
        }
        offset += written;
    }
    return true;
}

bool pipe_read_all(HANDLE pipe, void* data, DWORD size) {
    auto* bytes = static_cast<unsigned char*>(data);
    DWORD offset = 0;
    while (offset < size) {
        DWORD read = 0;
        if (!ReadFile(pipe, bytes + offset, size - offset, &read, nullptr) || read == 0) {
            return false;
        }
        offset += read;
    }
    return true;
}

bool pipe_write_json(HANDLE pipe, const std::wstring& json) {
    const std::string utf8 = utf8_from_wide(json);
    if (utf8.size() > 4 * 1024 * 1024) return false;
    const auto size = static_cast<std::uint32_t>(utf8.size());
    return pipe_write_all(pipe, &size, sizeof(size)) &&
           pipe_write_all(pipe, utf8.data(), size);
}

bool pipe_read_json(HANDLE pipe, std::wstring& json) {
    std::uint32_t size = 0;
    if (!pipe_read_all(pipe, &size, sizeof(size)) || size > 4 * 1024 * 1024) return false;
    std::string utf8(size, '\0');
    if (size > 0 && !pipe_read_all(pipe, utf8.data(), size)) return false;
    json = wide_from_utf8(utf8);
    return !json.empty();
}

std::wstring quote_command_argument(const std::wstring& value) {
    std::wstring quoted = L"\"";
    std::size_t backslashes = 0;
    for (const wchar_t ch : value) {
        if (ch == L'\\') {
            ++backslashes;
        } else if (ch == L'\"') {
            quoted.append(backslashes * 2 + 1, L'\\');
            quoted.push_back(L'\"');
            backslashes = 0;
        } else {
            quoted.append(backslashes, L'\\');
            backslashes = 0;
            quoted.push_back(ch);
        }
    }
    quoted.append(backslashes * 2, L'\\');
    quoted.push_back(L'\"');
    return quoted;
}

int run_privileged_worker(int argc, wchar_t** argv) {
    if (argc < 8) return 2;
    const std::wstring pipe_name = argv[2];
    HANDLE pipe = CreateFileW(
        pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr
    );
    if (pipe == INVALID_HANDLE_VALUE) return 3;

    qfc::CopyOptions options;
    const std::wstring policy = argv[4];
    if (policy == L"overwrite") options.conflict = qfc::ConflictPolicy::overwrite;
    else if (policy == L"newer") options.conflict = qfc::ConflictPolicy::overwrite_if_newer;
    else options.conflict = qfc::ConflictPolicy::skip;
    options.directory_layout = std::wstring(argv[5]) == L"folder"
        ? qfc::DirectoryLayout::include_source_directory
        : qfc::DirectoryLayout::contents;
    options.preserve_security = true;
    options.preserve_sacl = true;
    options.preserve_hard_links = true;
    options.preserve_extended_attributes = true;
    options.verify_contents = std::wstring(argv[6]) == L"verify";
    std::vector<std::wstring> sources;
    for (int index = 7; index < argc; ++index) sources.emplace_back(argv[index]);

    qfc::CopySession session;
    std::atomic_bool worker_done{false};
    std::thread cancellation_reader([&] {
        while (!worker_done.load(std::memory_order_relaxed)) {
            DWORD available = 0;
            if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
                session.cancel();
                break;
            }
            if (available > 0) {
                char command = 0;
                DWORD read = 0;
                if (ReadFile(pipe, &command, 1, &read, nullptr) && read == 1 && command == 'C') {
                    session.cancel();
                    break;
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    });

    const auto result = session.run(sources, argv[3], options, [&](const qfc::CopyProgress& progress) {
        if (!pipe_write_json(pipe, progress_json(progress))) session.cancel();
    });
    worker_done.store(true, std::memory_order_relaxed);
    cancellation_reader.join();

    pipe_write_json(pipe, completion_json(result));
    FlushFileBuffers(pipe);
    CloseHandle(pipe);
    return result.progress.cancelled ? 3 : (result.progress.error_count == 0 ? 0 : 1);
}

std::wstring load_embedded_html() {
    HRSRC resource = FindResourceW(nullptr, MAKEINTRESOURCEW(IDR_APP_HTML), RT_RCDATA);
    if (!resource) return {};
    HGLOBAL loaded = LoadResource(nullptr, resource);
    if (!loaded) return {};
    const auto* bytes = static_cast<const char*>(LockResource(loaded));
    const DWORD size = SizeofResource(nullptr, resource);
    if (!bytes || size == 0) return {};
    const int chars = MultiByteToWideChar(CP_UTF8, 0, bytes, static_cast<int>(size), nullptr, 0);
    if (chars <= 0) return {};
    std::wstring html(chars, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, bytes, static_cast<int>(size), html.data(), chars);
    return html;
}

std::vector<std::wstring> pick_folders(HWND owner, bool multiple) {
    IFileOpenDialog* dialog = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&dialog));
    if (FAILED(hr) || !dialog) return {};
    FILEOPENDIALOGOPTIONS flags{};
    dialog->GetOptions(&flags);
    DWORD options = flags | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST;
    if (multiple) options |= FOS_ALLOWMULTISELECT;
    dialog->SetOptions(options);
    hr = dialog->Show(owner);
    std::vector<std::wstring> paths;
    if (SUCCEEDED(hr)) {
        IShellItemArray* items = nullptr;
        if (SUCCEEDED(dialog->GetResults(&items)) && items) {
            DWORD count = 0;
            items->GetCount(&count);
            for (DWORD index = 0; index < count; ++index) {
                IShellItem* item = nullptr;
                if (SUCCEEDED(items->GetItemAt(index, &item)) && item) {
                    PWSTR raw = nullptr;
                    if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &raw)) && raw) {
                        paths.emplace_back(raw);
                        CoTaskMemFree(raw);
                    }
                    item->Release();
                }
            }
            items->Release();
        }
    }
    dialog->Release();
    return paths;
}

void send_selection_state() {
    std::wostringstream json;
    json << L"{\"version\":1,\"event\":\"selection\",\"payload\":{\"sources\":[";
    for (std::size_t index = 0; index < g_sources.size(); ++index) {
        if (index) json << L",";
        json << L"\"" << json_escape(g_sources[index]) << L"\"";
    }
    json << L"],\"destination\":\"" << json_escape(g_destination) << L"\"}}";
    post_json(json.str());
}

bool existing_directory(const std::wstring& path) {
    if (path.empty()) return false;
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES &&
           (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

void run_privileged_copy(
    const std::vector<std::wstring>& sources,
    const std::wstring& destination,
    const std::wstring& policy,
    const std::wstring& layout,
    bool verify
) {
    const std::wstring pipe_name = L"\\\\.\\pipe\\QuickFileCopy-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64());
    HANDLE pipe = CreateNamedPipeW(
        pipe_name.c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 64 * 1024, 64 * 1024, 0, nullptr
    );
    if (pipe == INVALID_HANDLE_VALUE) {
        post_ui_error(L"privilegedPipe");
        g_running.store(false);
        return;
    }

    std::wstring parameters = L"--privileged-worker " + quote_command_argument(pipe_name) +
        L" " + quote_command_argument(destination) + L" " + quote_command_argument(policy) +
        L" " + quote_command_argument(layout) + L" " +
        quote_command_argument(verify ? L"verify" : L"noverify");
    for (const auto& source : sources) parameters += L" " + quote_command_argument(source);
    if (parameters.size() >= 30000) {
        CloseHandle(pipe);
        post_ui_error(L"tooManySources");
        g_running.store(false);
        return;
    }
    const std::wstring application = executable_path();
    SHELLEXECUTEINFOW launch{};
    launch.cbSize = sizeof(launch);
    launch.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
    launch.hwnd = g_window;
    launch.lpVerb = L"runas";
    launch.lpFile = application.c_str();
    launch.lpParameters = parameters.c_str();
    launch.nShow = SW_HIDE;
    if (!ShellExecuteExW(&launch)) {
        const DWORD code = GetLastError();
        CloseHandle(pipe);
        post_ui_error(L"privilegedLaunch", qfc::format_windows_error(code));
        g_running.store(false);
        return;
    }

    const BOOL connected = ConnectNamedPipe(pipe, nullptr)
        ? TRUE : GetLastError() == ERROR_PIPE_CONNECTED;
    if (!connected) {
        CloseHandle(pipe);
        if (launch.hProcess) CloseHandle(launch.hProcess);
        post_ui_error(L"privilegedConnect");
        g_running.store(false);
        return;
    }

    g_privileged_pipe.store(pipe, std::memory_order_release);
    bool completed_received = false;
    std::wstring message;
    while (pipe_read_json(pipe, message)) {
        if (message.find(L"\"event\":\"completed\"") != std::wstring::npos) {
            completed_received = true;
        }
        post_json(std::move(message));
        message.clear();
    }
    g_privileged_pipe.store(INVALID_HANDLE_VALUE, std::memory_order_release);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
    if (launch.hProcess) {
        WaitForSingleObject(launch.hProcess, INFINITE);
        CloseHandle(launch.hProcess);
    }
    if (!completed_received) {
        post_ui_error(L"privilegedNoResult");
    }
    g_running.store(false);
}

void start_copy(
    const std::wstring& policy,
    const std::wstring& mode,
    const std::wstring& layout
) {
    if (g_running.load()) return;
    if (g_sources.empty() || g_destination.empty()) {
        post_ui_error(L"missingSelection");
        return;
    }
    if (g_copy_thread.joinable()) g_copy_thread.join();
    const std::vector<std::wstring> sources = g_sources;
    const std::wstring destination = g_destination;
    if (mode == L"complete" || mode == L"completeVerify") {
        g_session.reset();
        g_running.store(true);
        post_json(L"{\"version\":1,\"event\":\"started\",\"payload\":{}}");
        const bool verify = mode == L"completeVerify";
        g_copy_thread = std::thread([sources, destination, policy, layout, verify] {
            run_privileged_copy(sources, destination, policy, layout, verify);
        });
        return;
    }
    g_session = std::make_unique<qfc::CopySession>();
    qfc::CopyOptions options;
    if (policy == L"overwrite") options.conflict = qfc::ConflictPolicy::overwrite;
    else if (policy == L"newer") options.conflict = qfc::ConflictPolicy::overwrite_if_newer;
    else options.conflict = qfc::ConflictPolicy::skip;
    options.verify_contents = mode == L"verify";
    options.directory_layout = layout == L"folder"
        ? qfc::DirectoryLayout::include_source_directory
        : qfc::DirectoryLayout::contents;

    g_running.store(true);
    post_json(L"{\"version\":1,\"event\":\"started\",\"payload\":{}}");
    g_copy_thread = std::thread([sources, destination, options] {
        auto result = g_session->run(sources, destination, options, [](const qfc::CopyProgress& progress) {
            post_json(progress_json(progress));
        });
        post_json(completion_json(result));
        g_running.store(false);
    });
}

HRESULT handle_web_message(ICoreWebView2WebMessageReceivedEventArgs* args) {
    LPWSTR raw = nullptr;
    if (FAILED(args->get_WebMessageAsJson(&raw)) || !raw) return E_INVALIDARG;
    const std::wstring json(raw);
    CoTaskMemFree(raw);
    const std::wstring command = json_string(json, L"command");
    if (command == L"pickSource") {
        const auto selected = pick_folders(g_window, true);
        if (!selected.empty()) g_sources = selected;
        send_selection_state();
    } else if (command == L"pickDestination") {
        const auto selected = pick_folders(g_window, false);
        if (!selected.empty()) g_destination = selected.front();
        send_selection_state();
    } else if (command == L"setSource") {
        const auto selected = json_string(json, L"path");
        if (!existing_directory(selected)) {
            post_ui_error(L"sourceHistoryMissing");
        } else {
            g_sources = {selected};
            send_selection_state();
        }
    } else if (command == L"setDestination") {
        const auto selected = json_string(json, L"path");
        if (!existing_directory(selected)) {
            post_ui_error(L"destinationHistoryMissing");
        } else {
            g_destination = selected;
            send_selection_state();
        }
    } else if (command == L"startCopy") {
        start_copy(
            json_string(json, L"policy"),
            json_string(json, L"mode"),
            json_string(json, L"layout")
        );
    } else if (command == L"cancel") {
        const HANDLE privileged_pipe = g_privileged_pipe.load(std::memory_order_acquire);
        if (privileged_pipe != INVALID_HANDLE_VALUE) {
            const char cancel = 'C';
            DWORD written = 0;
            WriteFile(privileged_pipe, &cancel, 1, &written, nullptr);
        } else if (g_session) {
            g_session->cancel();
        }
    } else if (command == L"getState") {
        send_selection_state();
    } else {
        return E_INVALIDARG;
    }
    return S_OK;
}

void initialize_webview() {
    const std::wstring app_directory = executable_directory();
    const std::wstring loader_path = app_directory + L"\\WebView2Loader.dll";
    g_webview_loader = LoadLibraryW(loader_path.c_str());
    if (!g_webview_loader) g_webview_loader = LoadLibraryW(L"C:\\tools\\webview2\\build\\native\\x64\\WebView2Loader.dll");
    if (!g_webview_loader) {
        MessageBoxW(
            g_window,
            system_language_text(
                L"WebView2Loader.dll が見つかりません。",
                L"WebView2Loader.dll could not be found."
            ),
            L"QuickFileCopy", MB_ICONERROR
        );
        return;
    }
    auto create_environment = reinterpret_cast<CreateEnvironmentFn>(
        GetProcAddress(g_webview_loader, "CreateCoreWebView2EnvironmentWithOptions")
    );
    if (!create_environment) return;

    wchar_t temp[MAX_PATH]{};
    GetTempPathW(MAX_PATH, temp);
    const std::wstring user_data = std::wstring(temp) + L"QuickFileCopy_WebView2";
    CreateDirectoryW(user_data.c_str(), nullptr);
    const std::wstring html = load_embedded_html();

    create_environment(nullptr, user_data.c_str(), nullptr, new EnvironmentHandler(
        [html](HRESULT result, ICoreWebView2Environment* environment) -> HRESULT {
            if (FAILED(result) || !environment) return result;
            return environment->CreateCoreWebView2Controller(g_window, new ControllerHandler(
                [html](HRESULT controller_result, ICoreWebView2Controller* controller) -> HRESULT {
                    if (FAILED(controller_result) || !controller) return controller_result;
                    g_controller = controller;
                    g_controller->AddRef();
                    g_controller->get_CoreWebView2(&g_webview);
                    RECT bounds{};
                    GetClientRect(g_window, &bounds);
                    g_controller->put_Bounds(bounds);
                    g_controller->put_IsVisible(TRUE);

                    ICoreWebView2Settings* settings = nullptr;
                    if (SUCCEEDED(g_webview->get_Settings(&settings)) && settings) {
                        settings->put_AreDefaultContextMenusEnabled(FALSE);
                        settings->put_AreDevToolsEnabled(FALSE);
                        settings->put_IsStatusBarEnabled(FALSE);
                        settings->Release();
                    }

                    EventRegistrationToken token{};
                    g_webview->add_WebMessageReceived(new MessageHandler(handle_web_message), &token);
                    if (html.empty()) {
                        MessageBoxW(
                            g_window,
                            system_language_text(
                                L"埋め込みUIを読み込めませんでした。",
                                L"The embedded user interface could not be loaded."
                            ),
                            L"QuickFileCopy", MB_ICONERROR
                        );
                        return E_FAIL;
                    }
                    return g_webview->NavigateToString(html.c_str());
                }
            ));
        }
    ));
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
        case WM_SIZE:
            if (g_controller) {
                RECT bounds{};
                GetClientRect(window, &bounds);
                g_controller->put_Bounds(bounds);
            }
            return 0;
        case WM_QFC_WEB_MESSAGE: {
            std::unique_ptr<std::wstring> json(reinterpret_cast<std::wstring*>(lparam));
            if (g_webview && json) g_webview->PostWebMessageAsJson(json->c_str());
            return 0;
        }
        case WM_DPICHANGED: {
            const RECT* suggested = reinterpret_cast<const RECT*>(lparam);
            SetWindowPos(
                window, nullptr, suggested->left, suggested->top,
                suggested->right - suggested->left, suggested->bottom - suggested->top,
                SWP_NOZORDER | SWP_NOACTIVATE
            );
            return 0;
        }
        case WM_DESTROY:
            if (const HANDLE privileged_pipe = g_privileged_pipe.load(std::memory_order_acquire);
                privileged_pipe != INVALID_HANDLE_VALUE) {
                const char cancel = 'C';
                DWORD written = 0;
                WriteFile(privileged_pipe, &cancel, 1, &written, nullptr);
            }
            if (g_session) g_session->cancel();
            if (g_copy_thread.joinable()) g_copy_thread.join();
            if (g_controller) {
                g_controller->Close();
                g_controller->Release();
                g_controller = nullptr;
            }
            if (g_webview) {
                g_webview->Release();
                g_webview = nullptr;
            }
            if (g_webview_loader) {
                FreeLibrary(g_webview_loader);
                g_webview_loader = nullptr;
            }
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProcW(window, message, wparam, lparam);
    }
}

} // namespace

int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int show) {
    int argument_count = 0;
    LPWSTR* arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    if (arguments && argument_count > 1 && wcscmp(arguments[1], L"--privileged-worker") == 0) {
        const int result = run_privileged_worker(argument_count, arguments);
        LocalFree(arguments);
        return result;
    }
    if (arguments) LocalFree(arguments);

    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    window_class.lpszClassName = kWindowClass;
    window_class.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(IDI_APP_ICON));
    window_class.hIconSm = window_class.hIcon;
    if (!RegisterClassExW(&window_class)) return 1;

    const UINT dpi = GetDpiForSystem();
    const int window_height = MulDiv(530, static_cast<int>(dpi), USER_DEFAULT_SCREEN_DPI);
    g_window = CreateWindowExW(
        0, kWindowClass, L"QuickFileCopy - Native High-Speed File Copy",
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 980, window_height,
        nullptr, nullptr, instance, nullptr
    );
    if (!g_window) return 1;
    BOOL dark = TRUE;
    DwmSetWindowAttribute(g_window, DWMWA_USE_IMMERSIVE_DARK_MODE, &dark, sizeof(dark));
    ShowWindow(g_window, show);
    UpdateWindow(g_window);
    initialize_webview();

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    CoUninitialize();
    return static_cast<int>(message.wParam);
}
