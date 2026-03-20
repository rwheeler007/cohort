# Start a Claude Code Channel session for Cohort agent response integration.
#
# Prerequisites:
#   - Claude Code installed and authenticated
#   - Bun installed (bun --version)
#   - Cohort server running on localhost:5100
#   - channel_mode enabled in Cohort settings
#   - Plugin deps: cd plugins\cohort-channel && bun install
#
# Usage:
#   .\tools\start_channel_session.ps1
#   .\tools\start_channel_session.ps1 opus     (override model)

param(
    [string]$Model = "sonnet"
)

$PluginDir = Join-Path $PSScriptRoot "..\plugins\cohort-channel"
$SystemPromptFile = Join-Path $PluginDir "system_prompt.md"

# Install deps if needed
if (-not (Test-Path (Join-Path $PluginDir "node_modules"))) {
    Write-Host "[*] Installing plugin dependencies..."
    Push-Location $PluginDir
    bun install
    Pop-Location
}

# Read system prompt from file
$SystemPrompt = Get-Content $SystemPromptFile -Raw

Write-Host "[*] Starting Cohort Channel session (model=$Model)"
Write-Host "[*] Plugin: $PluginDir"
Write-Host "[*] Cohort: http://localhost:5100"
Write-Host "[*] System prompt: $($SystemPrompt.Length) chars from $SystemPromptFile"
Write-Host ""
Write-Host "    Press Ctrl+C to stop the session."
Write-Host ""

Set-Location G:\cohort

claude --dangerously-load-development-channels server:cohort-wq `
       --permission-mode acceptEdits `
       --allowedTools "mcp__cohort-wq__cohort_respond,mcp__cohort-wq__cohort_error,mcp__cohort-wq__cohort_post" `
       --model $Model `
       --system-prompt $SystemPrompt
