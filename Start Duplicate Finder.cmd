@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "" pythonw.exe "%~dp0duplicate_finder.pyw"
    exit /b
)
where pyw.exe >nul 2>&1
if not errorlevel 1 (
    start "" pyw.exe -3 "%~dp0duplicate_finder.pyw"
    exit /b
)
where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe "%~dp0duplicate_finder.pyw"
    if errorlevel 1 pause
    exit /b
)
echo Python 3.10 or newer with Tkinter is required.
echo Install Python from https://www.python.org/downloads/windows/
pause
