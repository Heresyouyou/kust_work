@echo off
REM GeoKB 状态检查脚本（5556 + 5554 + 5555）
REM 用法: status_geokb.bat

echo === GeoKB Status ===
echo.

echo [5556 AI API]
netstat -ano | findstr ":5556 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   [OK] Port 5556: LISTENING
    curl -s http://127.0.0.1:5556/health | findstr /C:"status" /C:"entries_total"
) else (
    echo   [DOWN] Port 5556: not listening
)

echo.
echo [5554 Admin]
netstat -ano | findstr ":5554 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   [OK] Port 5554: LISTENING
) else (
    echo   [DOWN] Port 5554: not listening
)

echo.
echo [5555 Portal]
netstat -ano | findstr ":5555 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   [OK] Port 5555: LISTENING
    curl -s http://127.0.0.1:5555/api/portal/health | findstr /C:"ok"
) else (
    echo   [DOWN] Port 5555: not listening
)

echo.
if exist D:\ZSK\geokb\index\geokb.db (
    for %%A in (D:\ZSK\geokb\index\geokb.db) do echo [DB] %%~zA bytes
) else (
    echo [WARN] Database not found
)
