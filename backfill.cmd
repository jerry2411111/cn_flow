@echo off
rem cn_flow: the last 245 trading days (about one year) of tick history for every Shenzhen and Shanghai A share,
rem summarised per stock and day into data\days\YYYYMMDD.parquet (collect.py), newest day first.  6 worker processes
rem with 96 connections in all.  Resumable: close this window any time and run it again; days already collected are
rem skipped.  Not needed for the program alone: it downloads the results the GitHub workflow publishes.
title cn_flow backfill (tick history, last 245 trading days)
cd /d "%~dp0"
".venv\Scripts\python.exe" -I -X utf8 collect.py backfill --days 245 --workers 6 --threads 96
echo.
echo Finished: data\days.  You can close this window.
pause >nul
