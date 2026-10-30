@echo off
REM GeoKB 停止脚本（5556 + 5554）
REM 用法: stop_geokb.bat

echo === GeoKB Stop ===

REM 查找并终止监听 5556 的 python 进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5556 " ^| findstr "LISTENING"') do (
    echo Killing PID %%a (5556) ...
    taskkill /PID %%a /F >nul 2>&1
)

REM 查找并终止监听 5554 的 python 进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5554 " ^| findstr "LISTENING"') do (
    echo Killing PID %%a (5554) ...
    taskkill /PID %%a /F >nul 2>&1
)

REM 验证
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":5556 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [FAIL] Port 5556 still in use.
) else (
    echo [OK] Port 5556 is free.
)
netstat -ano | findstr ":5554 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [FAIL] Port 5554 still in use.
) else (
    echo [OK] Port 5554 is free.
)
