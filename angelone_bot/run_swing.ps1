# Watchdog for the paper momentum swing bot: restarts it 30 seconds after any crash.
# Exits for good when another copy is running or the bot stops normally.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

while ($true) {
    & $py -u swing_bot.py
    $code = $LASTEXITCODE
    if ($code -eq 3) { Write-Host "Another copy is already running. Watchdog exiting."; exit 0 }
    if ($code -eq 0) { Write-Host "Swing bot stopped normally. Watchdog exiting."; exit 0 }
    Write-Host "Swing bot exited with code $code. Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}
