@echo off
rem ============================================================
rem  PostNews NAS Reaper - show task status and recent log
rem ============================================================
setlocal
set "PS1=%~dp0install_task.ps1"
if not exist "%PS1%" (
    echo [ERROR] install_task.ps1 not found next to this bat.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Status
echo.
pause
endlocal
