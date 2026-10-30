@echo off
REM GeoKB 启动脚本（5556 AI API + 5554 本地管理端）
REM 用法: start_geokb.bat

echo === GeoKB Start ===
cd /d D:\ZSK\geokb

REM 检查端口占用
netstat -ano | findstr ":5556 " >nul 2>&1
if %errorlevel%==0 (
    echo [WARN] Port 5556 is already in use.
    netstat -ano | findstr ":5556 "
) else (
    echo Starting GeoKB AI API on port 5556 ...
    start /B "" D:\ZSK\geokb\venv\Scripts\python.exe D:\ZSK\geokb\server\app.py > D:\ZSK\geokb\logs\geokb_5556.log 2>&1
)

netstat -ano | findstr ":5554 " >nul 2>&1
if %errorlevel%==0 (
    echo [WARN] Port 5554 is already in use.
    netstat -ano | findstr ":5554 "
) else (
    echo Starting GeoKB Admin on port 5554 ...
    start /B "" D:\ZSK\geokb\venv\Scripts\python.exe D:\ZSK\geokb\server\admin\app.py > D:\ZSK\geokb\logs\geokb_5554.log 2>&1
)

echo === GeoKB services started ===
