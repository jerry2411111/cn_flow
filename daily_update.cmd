@echo off
rem cn_flow daily update, for a machine that collects the tick history itself (run by the Windows task "cn_flow daily
rem update" after the Beijing close, or by hand): reference data, any trading day not collected yet, then the rankings
rem and the regression (collect.py update).  Not needed for the program alone: it downloads the results the GitHub
rem workflow publishes.  Runs at below-normal priority; the log is logs\update_YYYYMMDD.log.  A few minutes a day.
cd /d "%~dp0"
".venv\Scripts\python.exe" -I -X utf8 collect.py update >> "logs\update_console.log" 2>&1
