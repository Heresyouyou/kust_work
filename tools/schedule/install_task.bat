@echo off
rem ============================================================
rem  PostNews NAS Reaper - install Windows scheduled task
rem  Runs tools\nas_reaper.py twice a day (09:00 / 21:00):
rem  moves local PDFs older than 48h to the NAS, then deletes
rem  the local copies. No external service required.
rem  If launched as Administrator, registers in silent mode.
rem ============================================================
setlocal
set "PS1=%~dp0install_task.ps1"
if not exist "%PS1%" (
    echo [ERROR] install_task.ps1 not found next to this bat.
    pause
    exit /b 1
)

net session >nul 2>&1
if "%ERRORLEVEL%"=="0" (
    echo [INFO] Administrator detected - registering in silent mode.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -RunAlways %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
)
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo [ERROR] exit code = %RC%
pause
endlocal & exit /b %RC%
