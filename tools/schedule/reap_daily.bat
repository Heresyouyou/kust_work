@echo off
chcp 65001 >nul 2>&1
rem ============================================================
rem  PostNews NAS Reaper - run once (manual / fallback entry)
rem  Moves local PDFs older than 48h to the NAS archive, then
rem  deletes the local copies after verifying the NAS copies.
rem  Usage: reap_daily.bat [--dry-run] [--hours N] [--status] [--force]
rem ============================================================
setlocal

rem Locate project root (this bat lives in <root>\tools\schedule\)
pushd "%~dp0..\.."
if errorlevel 1 (
    echo [ERROR] Cannot locate project root.
    exit /b 1
)
set "PROJ=%CD%"

rem ---- pick a python interpreter ----
set "PY="
if defined POSTNEWS_PYTHON if exist "%POSTNEWS_PYTHON%" set "PY=%POSTNEWS_PYTHON%"
if not defined PY if exist "D:\Python 3.12.4\python.exe" set "PY=D:\Python 3.12.4\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do set "PY=%%i" & goto :gotpython
)
:gotpython
if not defined PY (
    echo [ERROR] Python not found. Set POSTNEWS_PYTHON to the full python.exe path.
    popd
    exit /b 1
)

echo [%date% %time%] PostNews NAS reaper - start
echo   project : %PROJ%
echo   python  : %PY%
echo.

"%PY%" "%PROJ%\tools\nas_reaper.py" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo [WARN] reaper exit code = %RC%
echo [%date% %time%] done  exit=%RC%
echo   log     : %PROJ%\logs\nas_reaper.log
popd
endlocal & exit /b %RC%
