@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0force_stop_capture.ps1"
echo.
pause
