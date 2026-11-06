# Starts PumpML15 (the "1.5% bar, strongest first" copy of the ML bot) in PAPER (dry-run) mode on Binance USDT
# futures, separate wallet, database and log from PumpML. Restarts it if it stops.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
while ($true) {
    & "$here\.venv\Scripts\freqtrade.exe" trade -c user_data\config-pump.json -c user_data\config-pump-dynamic.json `
        -c user_data\config-pumpml15.json --userdir user_data --logfile user_data\logs\pumpml15.log `
        --db-url sqlite:///user_data/tradesv3.pumpml15.dryrun.sqlite
    Start-Sleep -Seconds 60
}
