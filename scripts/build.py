from __future__ import annotations

import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
SRC = ROOT / "src"
APP = SRC / "app"
CLI = SRC / "cli"
ENGINE = SRC / "engine"
INTERMEDIATE = ROOT / "build" / "intermediate"
DIST = ROOT / "dist"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import bundle_html


def find_tool(names: tuple[str, ...]) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        pattern = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages", "BrechtSanders.WinLibs.MCF.UCRT_*", "mingw64", "bin")
        for directory in glob.glob(pattern):
            for name in names:
                candidate = Path(directory) / name
                if candidate.exists():
                    return candidate
    return None


def run(command: list[str], label: str, cwd: Path | None = None) -> None:
    print(f"\n[{label}] {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd or ROOT, text=True)
    if result.returncode:
        raise SystemExit(result.returncode)


def build() -> None:
    compiler = find_tool(("g++.exe", "g++"))
    windres = find_tool(("windres.exe", "llvm-windres.exe", "windres"))
    if not compiler or not windres:
        raise SystemExit("MinGW-w64 g++/windres was not found")

    webview_root = Path(os.environ.get("WEBVIEW2_ROOT", r"C:\tools\webview2\build\native"))
    webview_include = Path(os.environ.get("WEBVIEW2_INCLUDE", str(webview_root / "include")))
    webview_loader = Path(os.environ.get("WEBVIEW2_LOADER", str(webview_root / "x64" / "WebView2Loader.dll")))
    if not (webview_include / "WebView2.h").exists():
        raise SystemExit(f"WebView2.h was not found: {webview_include}")
    if not webview_loader.exists():
        raise SystemExit(f"WebView2Loader.dll was not found: {webview_loader}")

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    bundled_html = bundle_html.bundle(INTERMEDIATE / "QuickFileCopy.html")
    resource_object = INTERMEDIATE / "QuickFileCopy_res.o"
    common = [str(compiler), "-O3", "-std=c++20", "-DUNICODE", "-D_UNICODE", "-DWINVER=0x0A00", "-D_WIN32_WINNT=0x0A00", "-ffunction-sections", "-fdata-sections", "-static", "-static-libgcc", "-static-libstdc++", "-Wl,--gc-sections", f"-I{SRC}", f"-I{ENGINE}", f"-I{APP}"]

    run(common + ["-municode", str(ENGINE / "copy_engine.cpp"), str(CLI / "main_cli.cpp"), "-o", str(DIST / "QuickFileCopy_cli.exe"), "-lkernel32", "-ladvapi32", "-lbcrypt"], "cli")
    run([str(windres), f"-I{APP}", f"-I{INTERMEDIATE}", str(APP / "QuickFileCopy.rc"), "-O", "coff", "-o", str(resource_object)], "resource", cwd=APP)
    run(common + ["-mwindows", f"-I{webview_include}", str(ENGINE / "copy_engine.cpp"), str(APP / "main_gui.cpp"), str(resource_object), "-o", str(DIST / "QuickFileCopy.exe"), "-lkernel32", "-luser32", "-ldwmapi", "-lole32", "-luuid", "-lshell32", "-ladvapi32", "-lbcrypt"], "gui")
    shutil.copy2(webview_loader, DIST / "WebView2Loader.dll")
    resource_object.unlink(missing_ok=True)
    for path in (DIST / "QuickFileCopy.exe", DIST / "QuickFileCopy_cli.exe", DIST / "WebView2Loader.dll"):
        print(f"[ok] {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
