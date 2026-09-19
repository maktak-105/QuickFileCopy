@echo off
setlocal
cd /d "%~dp0\.."
echo [Build] Building QuickFileCopy...
python scripts\build.py %*

