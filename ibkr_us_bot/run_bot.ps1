# Watchdog: runs the bot and restarts it 30 seconds after any crash.
# Exits for good when the bot stops normally, another copy is running, or it is not ready for real money.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

while ($true) {
    & $py -u bot.py
    $code = $LASTEXITCODE
    # exit 0 in these cases so Task Scheduler does not keep restarting us
    if ($code -eq 3) { Write-Host "Another copy is already running. Watchdog exiting."; exit 0 }
    if ($code -eq 4) { Write-Host "Not ready for real money (see bot.log). Watchdog exiting."; exit 0 }
    if ($code -eq 0) { Write-Host "Bot stopped normally. Watchdog exiting."; exit 0 }
    Write-Host "Bot exited with code $code. Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}
