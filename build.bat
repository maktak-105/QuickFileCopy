@echo off
setlocal
cd /d "%~dp0"
python build_native.py
exit /b %ERRORLEVEL%
