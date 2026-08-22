# QuickFileCopy Changelog
[日本語版 HISTORY_jp.md](HISTORY_jp.md)

## Versioning rules

- First digit: new features
- Second digit: bug fixes
- Third digit: documentation and other changes

## 1.0.0 (2026-08-22)

### Distribution

- First public release of the native GUI and CLI.
- GitHub Actions builds `QuickFileCopy-binary.zip` from a `v*` tag. The ZIP is not stored in the repository.

## 0.1.0 (2026-08-18)

### Engine

- Added the native C++20 scanning and copy engine with adaptive parallelism.
- Added safe replacement, cancellation, SHA-256 verification, and resume journals.
- Added preservation paths for Windows security and filesystem metadata.
- Fixed normal copy failures on Google Drive and other filesystems without extended-attribute support.
- Added privileged preallocation for eligible local files of 512 MiB or larger.

### GUI and CLI

- Added the compact WebView2 GUI with byte and file progress.
- Added multiple source selection, history, copy layouts, and conflict policies.
- Added persistent English/Japanese switching with the QDB-style language button.
- Added the native command-line application.

### Distribution

- Adopted the common Quick application repository and distribution layout.
