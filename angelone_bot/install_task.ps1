# Registers a Windows scheduled task that starts the bot's watchdog at login, hidden, and restarts it if it stops.
#   Install:   powershell -ExecutionPolicy Bypass -File install_task.ps1
#   Remove:    powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove
#   Start now: Start-ScheduledTask -TaskName "AngelOne Bot"
param([switch]$Remove)
$name = "AngelOne Bot"
if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed task '$name'."
    exit 0
}
$script = Join-Path $PSScriptRoot "run_bot.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Angel One NSE day-trader bot watchdog" -Force | Out-Null
Write-Host "Installed task '$name'. It starts at every login. Start it now with: Start-ScheduledTask -TaskName `"$name`""
