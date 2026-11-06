# Runs the dashboard (http://localhost:8800) at every login, hidden, and restarts it if it stops.
#   Install:   powershell -ExecutionPolicy Bypass -File install_task.ps1
#   Remove:    powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove
param([switch]$Remove)
$name = "Trading Dashboard"
if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed task '$name'."
    exit 0
}
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$(Join-Path $PSScriptRoot 'dashboard.py')`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Paper trading dashboard on http://localhost:8800" -Force | Out-Null
Write-Host "Installed task '$name'. Start it now with: Start-ScheduledTask -TaskName `"$name`""
