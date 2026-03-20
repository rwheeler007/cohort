@echo off
REM Launcher for the PowerShell channel session script.
REM Usage: start_channel_session.bat [model]
REM   model: sonnet (default), opus, haiku

setlocal
set MODEL=%1
if "%MODEL%"=="" set MODEL=sonnet

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_channel_session.ps1" -Model %MODEL%

endlocal
