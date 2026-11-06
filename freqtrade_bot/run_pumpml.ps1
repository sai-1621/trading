# Starts the PumpML machine-learning crypto bot in PAPER (dry-run) mode on Binance USDT futures (long and short, 1x).
# Restarts it if it stops.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
while ($true) {
    & "$here\.venv\Scripts\freqtrade.exe" trade -c user_data\config-pump.json -c user_data\config-pump-dynamic.json `
        -c user_data\config-pumpml.json --userdir user_data --logfile user_data\logs\pumpml.log `
        --db-url sqlite:///user_data/tradesv3.pumpml.dryrun.sqlite
    Start-Sleep -Seconds 60
}
