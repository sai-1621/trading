# Watchdog for the paper crypto trend bot (futures_lab/trend_bot.py): restarts it 30 seconds after a crash.
# Exits when another copy is running or the bot stops normally.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot "..\freqtrade_bot\.venv\Scripts\python.exe"
while ($true) {
    & $py -u trend_bot.py
    $code = $LASTEXITCODE
    if ($code -eq 3) { Write-Host "Another copy is already running. Watchdog exiting."; exit 0 }
    if ($code -eq 0) { Write-Host "Trend bot stopped normally. Watchdog exiting."; exit 0 }
    Write-Host "Trend bot exited with code $code. Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}
