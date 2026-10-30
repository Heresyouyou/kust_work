@echo off
rem ============================================================
rem  History manifest crawler - background scheduler
rem  Double-click to start. One article at a time, waiting 3 minutes
rem  between articles (download only; ingestion waits for manual batch).
rem  Runs 24/7 while ingest is off (no DeepSeek calls).
rem  Only if ingest is enabled it falls back to 18:05-07:00,
rem  07:00-09:00 yielding to the daily new-arrival pipeline.
rem
rem  Status:  venv\Scripts\python.exe scripts\history_queue.py --status
rem  Log:     logs\history_queue_loop.log
rem ============================================================
set "PY=D:\ZSK\geokb\venv\Scripts\pythonw.exe"
set "SCRIPT=D:\ZSK\geokb\scripts\history_queue_loop.py"

if not exist "%PY%" (
  echo [ERROR] python not found: %PY%
  pause
  exit /b 1
)
if not exist "%SCRIPT%" (
  echo [ERROR] script not found: %SCRIPT%
  pause
  exit /b 1
)

cd /d D:\ZSK\geokb
start "history_queue_loop" /B "%PY%" "%SCRIPT%"
echo Started (background, no window). Check logs\history_queue_loop.log
