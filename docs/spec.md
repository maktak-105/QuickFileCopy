# QuickFileCopy Specification

[日本語版 spec_jp.md](spec_jp.md)

## 1. Application Overview

- Name: QuickFileCopy
- Purpose: Copy files and folders quickly and safely on Windows with minimal configuration
- Supported OS: Windows 10 / 11 (64-bit)
- Implementation: C++20 (MinGW-w64) + Win32 + WebView2 + HTML/CSS/vanilla JavaScript
- Version: v1.1.0
- Distribution: Flat ZIP archive

## 2. Architecture

```text
[src/ui/index.html]
        ↓ bundle_html.py
[HTML embedded in EXE] ←WebMessage(JSON)→ [webview_main.cpp]
                                                ↓
                                         [copy_engine.cpp]
                                                ↑
                                           [main_cli.cpp]
```

- `copy_engine.cpp`: GUI-independent scanning, decision, copy, verification, and metadata preservation
- `webview_main.cpp`: Win32 window, WebView2, folder selection, UAC worker, and JSON conversion
- `main_cli.cpp`: CLI using the same copy engine
- `src/ui/index.html`: Framework-independent, self-contained UI embedded in the EXE at build time

## 3. Screen Layout

| Area | Contents |
| --- | --- |
| Header | Application name, mode, layout, conflict policy, and language toggle |
| Copy settings | Multiple sources, destination, history, start, and cancel |
| Transfer summary | Found, copied, resumed, skipped, and error counts |
| Progress | Byte percentage, file percentage, speed, elapsed time, and current path |

## 4. Copy Modes

| Mode | Behavior |
| --- | --- |
| Fast | Standard copy without requesting EAs; uses unbuffered I/O for eligible large local files |
| Verify | Standard copy followed by SHA-256 comparison of the source and temporary copy |
| Preserve | Uses an elevated worker to preserve ACL/SACL, EAs, hard links, and other metadata |
| Preserve + verify | Adds SHA-256 verification to Preserve mode |

## 5. Layout and Conflict Policies

- `contents`: Merge the contents of the source folder directly into the destination.
- `folder`: Create the source folder name under the destination.
- `skip`: Do not process an item when a destination with the same name exists.
- `overwrite`: Safely replace an existing destination using a temporary file.
- `newer`: Replace only when the source has a newer modification time.

## 6. WebMessage Protocol

Every message is a JSON object with `version: 1`.

### JavaScript → native

| command | Main values | Description |
| --- | --- | --- |
| `pickSource` | none | Opens a source-folder dialog with multiple selection |
| `pickDestination` | none | Opens the destination-folder dialog |
| `setSource` | `path` | Selects a source from history |
| `setDestination` | `path` | Selects a destination from history |
| `startCopy` | `policy`, `mode`, `layout` | Starts copying |
| `cancel` | none | Cancels the active session or privileged worker |
| `getState` | none | Requests the current selection state |

### native → JavaScript

| event | Description |
| --- | --- |
| `selection` | Current source and destination values |
| `started` | Copy started |
| `progress` | Bytes, counts, speed, elapsed time, and current path |
| `completed` | Completion, cancellation, and error details |
| `error` | Failure before copying or while starting the privileged worker |

## 7. Safety

- Regular files are written to `.qfc-<pid>-<id>.part` in the destination directory.
- `MoveFileExW` replaces the destination only after copy, optional verification, and metadata preservation complete.
- Incomplete temporary files are deleted after cancellation or failure.
- The resume journal reuses only committed files after validating their size and modification time.
- Until completion, a privileged preallocated temporary file has a DACL that permits only the current user and SYSTEM.

## 8. Metadata Preservation

- ADS: `CopyFileExW`
- EA: Preserve mode uses `NtQueryEaFile` / `NtSetEaFile`. If the source filesystem does not support EAs, copying continues as though the source has no EAs.
- EFS: `ReadEncryptedFileRaw` / `WriteEncryptedFileRaw`
- ACL/owner/group/SACL: Windows Security API
- Reparse points: `FSCTL_GET_REPARSE_POINT` / `FSCTL_SET_REPARSE_POINT`
- Sparse files: Queries allocated ranges and recreates unallocated regions
- Compression: `FSCTL_SET_COMPRESSION`
- Hard links: Recreates destination link relationships using file IDs

## 9. Performance Policy

- Scanning and copying run concurrently through a bounded queue.
- Automatic parallelism is up to 16 workers for local storage, 12 for cloud-attribute/reparse mounts, and 8 for UNC/SMB.
- Eligible regular local files of 512 MiB or larger request unbuffered copying.
- Privileged preallocation is used only for eligible regular local files when Preserve mode obtains the required privilege.
- Sparse, compressed, EFS, ADS, reparse-point, cloud, and network files are excluded from privileged preallocation.

## 10. Display Languages

- The `🌐 English` / `🌐 日本語` button in the upper-right corner switches immediately between Japanese and English.
- Static labels, progress, completion, cancellation, and application errors use the selected language.
- The selected language is stored in the `qfc.preferences` object in `localStorage`.
- Detailed Windows API errors remain in the text returned by the operating system so diagnostic information is retained.

## 11. Known Limitations

- The binaries are not code-signed.
- Privileged preallocation improves performance only when the destination is fast local storage.
- Because an external `index.html` is not distributed, replacing the UI requires rebuilding the executable.
