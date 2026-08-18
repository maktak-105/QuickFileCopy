# Development Environment

[日本語版 environment_jp.md](environment_jp.md)

## Runtime Environment

- Windows 10 / 11 (64-bit)
- Microsoft Edge WebView2 Runtime

## Build Environment

- Python 3.11 or later (build scripts only)
- MinGW-w64 with C++20; verified with WinLibs MCF/UCRT
- Microsoft WebView2 SDK

### MinGW-w64

```powershell
winget install --id BrechtSanders.WinLibs.MCF.UCRT --exact --source winget
```

In addition to `PATH`, `build_native.py` searches the standard WinGet package location.

### WebView2 SDK

Default paths:

```text
C:\tools\webview2\build\native\include\WebView2.h
C:\tools\webview2\build\native\x64\WebView2Loader.dll
```

Set the following environment variables when necessary:

- `WEBVIEW2_ROOT`
- `WEBVIEW2_INCLUDE`
- `WEBVIEW2_LOADER`

## Build

```powershell
cd C:\path\to\QuickFileCopy
build.bat
```

Internal steps:

1. `bundle_html.py` copies `templates/index.html` into the generated resource file
2. `windres` compiles the icon, HTML, and VERSIONINFO into a resource object
3. Compile the CLI application
4. Compile the GUI application
5. Copy `WebView2Loader.dll` to the output directory
6. Delete the intermediate resource object

## Build Outputs

| File | Description |
| --- | --- |
| `dist/binary/QuickFileCopy.exe` | GUI application with embedded HTML |
| `dist/binary/QuickFileCopy_cli.exe` | CLI application |
| `dist/binary/WebView2Loader.dll` | WebView2 loader |

An external `index.html` or engine DLL is not required.

## Tests

```powershell
python core\native\tests\native_smoke.py
```

Benchmark:

```powershell
python core\native\tests\benchmark_native.py --profile small --destination-root D:\qfc-bench --workers 1,4,8,16
```

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `g++/windres was not found` | Install WinLibs or add the MinGW `bin` directory to `PATH` |
| `WebView2.h was not found` | Check the SDK location or `WEBVIEW2_INCLUDE` |
| `WebView2Loader.dll was not found` | Set `WEBVIEW2_LOADER` to the actual file |
| Runtime error at startup | Install the WebView2 Evergreen Runtime |
| No UAC prompt in Preserve mode | Check whether policy prohibits elevation |
| A network drive is unavailable | Drive mappings may be separated in the elevated UAC context; use a UNC path |

## Directory Layout

```text
core/native/include/qfc/  Public engine headers
core/native/src/          Engine, WebView2 host, and CLI
core/native/resources/    RC files and icon/HTML resource definitions
core/native/tests/        Smoke tests and benchmarks
templates/                WebView2 UI used during development
static/                   Future location for separated CSS/JS/images
assets/                   Original icons and public images
document/                 Developer documentation
plans/                    Plans and implementation results
prototype/python/         Legacy Python prototype
dist/binary/              Generated binaries
dist/documents/           Distribution documentation
```
