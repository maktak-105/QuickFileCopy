# QuickFileCopy Version Information

[日本語版 about_jp.md](about_jp.md)

## Version

Ver. v1.1.0

## Supported Operating Systems

Windows 10 / 11 (64-bit)

## Display Languages

- GUI: Japanese / English, switched immediately with the QDB-style toggle in the upper-right corner
- Repository and distribution documentation: Japanese / English
- The selected language is stored in WebView2 local settings and retained after restart

## Technology Stack

- C++20 / MinGW-w64 / WinLibs MCF UCRT
- Win32 API
- Microsoft Edge WebView2
- HTML / CSS / vanilla JavaScript
- Python standard library (build scripts only)

Primary Windows APIs: `CopyFileExW`, `MoveFileExW`, `SetFileValidData`, `ReadEncryptedFileRaw`, `WriteEncryptedFileRaw`, `DeviceIoControl`, BCrypt SHA-256, and Windows Security APIs.

Python and third-party C++ runtimes do not need to be installed separately at runtime. The WebView2 Runtime is required.

## Author

GitHub: maktak-105
