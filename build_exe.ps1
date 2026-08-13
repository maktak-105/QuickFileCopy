# Builds FastestCopy.exe as a standalone, single-file executable via Nuitka.
#
# Nuitka compiles Python to C and links a real executable, rather than just
# bundling the interpreter (PyInstaller's approach) - faster startup, and
# closer to "compiled" per the project's requirement to ship an .exe.
#
# Note: file-copy throughput itself does not come from this compilation
# step (copying is I/O-bound - see src/fastestcopy/engine/winio.py) - this
# step is about distribution and startup time, not copy speed.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& ".\.venv\Scripts\python.exe" -m nuitka `
    --standalone `
    --onefile `
    --enable-plugin=pyside6 `
    --windows-console-mode=disable `
    --windows-icon-from-ico=assets\icon.ico `
    --assume-yes-for-downloads `
    --output-dir=dist `
    --output-filename=FastestCopy.exe `
    --company-name="FastestCopy" `
    --product-name="FastestCopy" `
    --file-version=0.1.0 `
    --product-version=0.1.0 `
    run_gui.py

Write-Host "Build complete: dist\FastestCopy.exe"
