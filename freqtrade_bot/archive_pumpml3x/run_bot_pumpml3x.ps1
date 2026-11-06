# Starts the PumpML machine-learning crypto bot at 3x LEVERAGE in PAPER (dry-run) mode on Binance USDT futures
# (long and short, isolated margin). Same model and exits as run_pumpml.ps1 (the 1x bot); this one uses 3x so the two
# can be compared. Replaced the Donchian spot bot on 3 Nov 2026 (its old script: archive_old_pump\run_bot_donchian.ps1).
# Restarts it if it stops.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
while ($true) {
    & "$here\.venv\Scripts\freqtrade.exe" trade -c user_data\config-pump.json -c user_data\config-pump-dynamic.json `
        -c user_data\config-pumpml.json -c user_data\config-pumpml-3x.json -c user_data\config-private.json `
        --userdir user_data --logfile user_data\logs\pumpml3x.log `
        --db-url sqlite:///user_data/tradesv3.pumpml3x.dryrun.sqlite
    Start-Sleep -Seconds 60
}
