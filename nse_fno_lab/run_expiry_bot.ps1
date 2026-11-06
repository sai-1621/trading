# Watchdog for the expiry-day PAPER bot (strategy 2 iron fly tracker). Places no orders.
# Restarts it 30 seconds after a crash; exits if another copy is already running.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
while ($true) {
    & $py -u expiry_bot.py
    $code = $LASTEXITCODE
    if ($code -eq 3) { Write-Host "Expiry bot already running. Watchdog exiting."; exit 0 }
    if ($code -eq 0) { Write-Host "Expiry bot stopped normally. Watchdog exiting."; exit 0 }
    Write-Host "Expiry bot exited with code $code. Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}
