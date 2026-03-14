@echo off
:: Cohort Launcher -- starts the tray app with no console window
:: Uses pythonw.exe so no black window appears

set "COHORT_DIR=%~dp0"
set "PYTHON=%COHORT_DIR%python\pythonw.exe"
set "AGENTS_DIR=%COHORT_DIR%agents"

:: Set agents directory for Cohort to find
set "COHORT_AGENTS_DIR=%AGENTS_DIR%"

:: Launch Cohort tray app (pythonw = no console window)
start "" "%PYTHON%" -m cohort launch %*
