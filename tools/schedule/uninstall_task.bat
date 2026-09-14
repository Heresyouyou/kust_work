@echo off
rem ============================================================
rem  PostNews NAS Reaper - uninstall Windows scheduled task
rem ============================================================
setlocal
set "PS1=%~dp0install_task.ps1"
if not exist "%PS1%" (
    echo [ERROR] install_task.ps1 not found next to this bat.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Uninstall
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ERROR] exit code = %RC%
pause
endlocal & exit /b %RC%
