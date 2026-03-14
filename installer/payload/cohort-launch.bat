@echo off
REM =====================================================================
REM Cohort Launcher -- starts server + system tray
REM =====================================================================
REM This batch file is the entry point for the Start Menu shortcut.
REM It uses the embedded Python distribution bundled with the installer.
REM No system Python is required.

setlocal

REM Resolve install directory (where this .bat lives)
set "COHORT_HOME=%~dp0"
set "COHORT_HOME=%COHORT_HOME:~0,-1%"

REM Use embedded Python
set "PYTHON=%COHORT_HOME%\python\python.exe"
set "PYTHONPATH=%COHORT_HOME%\python\Lib\site-packages"

REM Set data directory to user's local AppData
if not defined COHORT_DATA_DIR (
    set "COHORT_DATA_DIR=%LOCALAPPDATA%\Cohort\data"
)

REM Set agents directory
if not defined COHORT_AGENTS_DIR (
    set "COHORT_AGENTS_DIR=%COHORT_HOME%\agents"
)

REM Create data directory if it doesn't exist
if not exist "%COHORT_DATA_DIR%" mkdir "%COHORT_DATA_DIR%"

REM Launch Cohort (server + tray icon)
REM Use pythonw.exe (no console window) if available, fall back to python.exe
if exist "%COHORT_HOME%\python\pythonw.exe" (
    start "" "%COHORT_HOME%\python\pythonw.exe" -m cohort launch %*
) else (
    start "" /min "%PYTHON%" -m cohort launch %*
)

endlocal
