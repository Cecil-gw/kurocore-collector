@echo off
cd /d "%~dp0"
set PYTHON="C:\Program Files\Python313\python.exe"
echo Starting Kurocore Collector...
echo.
echo Browser: http://127.0.0.1:8888
echo Press Ctrl+C to stop
echo.
%PYTHON% -X utf8 webapp.py
pause
