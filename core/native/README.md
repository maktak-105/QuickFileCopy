# QuickFileCopy native implementation

This directory contains the production C++20 implementation. It does not link to or embed the archived Python prototype.

## Files

```text
include/qfc/copy_engine.h  public copy-engine API
src/copy_engine.cpp        scanner, queue, copy paths, verification, metadata
src/webview_main.cpp       Win32 window, WebView2 host, UAC worker, WebMessage
src/main_cli.cpp            command-line application
resources/                  resource script, VERSIONINFO, generated embedded HTML
```

Python smoke tests live in `python/tests/`. The standard build entry point is the repository-root `build.bat` or `build_native.py`.

```powershell
build.bat
python python\tests\native_smoke.py
```

Outputs:

```text
dist/binary/QuickFileCopy.exe
dist/binary/QuickFileCopy_cli.exe
dist/binary/WebView2Loader.dll
```

The GUI HTML is generated from `templates/index.html` and embedded into the executable. It is not a separate runtime file.

See `document/spec.md` for behavior and `document/environment.md` for the complete build environment.
