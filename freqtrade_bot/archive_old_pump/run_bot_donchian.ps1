# Starts the Freqtrade crypto bot in PAPER (dry-run) mode. Restarts it if it stops.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
while ($true) {
    & "$here\.venv\Scripts\freqtrade.exe" trade -c user_data\config.json -c user_data\config-private.json `
        --userdir user_data --logfile user_data\logs\freqtrade.log --db-url sqlite:///user_data/tradesv3.dryrun.sqlite
    Start-Sleep -Seconds 60
}
