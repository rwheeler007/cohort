@echo off
REM Start a Claude Code Channel session for Cohort agent response integration.
REM
REM Prerequisites:
REM   - Claude Code installed and authenticated
REM   - Bun installed (bun --version)
REM   - Cohort server running on localhost:5100
REM   - channel_mode enabled in Cohort settings
REM   - Plugin deps: cd plugins\cohort-channel && bun install
REM
REM Usage:
REM   tools\start_channel_session.bat
REM   tools\start_channel_session.bat opus     (override model)

setlocal

set MODEL=%1
if "%MODEL%"=="" set MODEL=sonnet

set PLUGIN_DIR=%~dp0..\plugins\cohort-channel

if not exist "%PLUGIN_DIR%\node_modules" (
    echo [*] Installing plugin dependencies...
    pushd "%PLUGIN_DIR%"
    call bun install
    popd
)

echo [*] Starting Cohort Channel session (model=%MODEL%)
echo [*] Plugin: %PLUGIN_DIR%
echo [*] Cohort: http://localhost:5100
echo.
echo     Press Ctrl+C to stop the session.
echo.

cd /d G:\cohort

claude --dangerously-load-development-channels server:cohort-wq ^
       --permission-mode acceptEdits ^
       --allowedTools "mcp__cohort-wq__cohort_respond,mcp__cohort-wq__cohort_error" ^
       --model %MODEL% ^
       --system-prompt "You are an agent in the Cohort team chat system. Wait for requests from the cohort-wq channel and respond in character as the specified agent."

endlocal
