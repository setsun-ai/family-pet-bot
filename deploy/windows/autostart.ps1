<#
family-pet-bot: start automatically (hidden) when you log in to Windows.

The bot runs as long as you are logged in and the PC is awake. It restarts
itself up to 3 times after a crash. Log: logs\bot.log.

    powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1           enable
    powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1 -Remove   disable
(or double-click autostart.bat next to this file)

For a bot that must answer 24/7, use an always-on machine (Raspberry Pi) instead.
#>
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$TaskName = "family-pet-bot"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Remove) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Autostart removed. (If the bot is still running, stop it in Task Manager: pythonw.exe)"
    exit 0
}

$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pythonw)) { throw "No .venv yet - start the bot once with run.bat first." }
& (Join-Path $Root ".venv\Scripts\python.exe") -m petbot check-config
if ($LASTEXITCODE -ne 0) { throw "Fix .env first (run.bat --setup)." }

$Log = Join-Path $Root "logs\bot.log"
$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m petbot --log-file `"$Log`"" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "family-pet-bot Telegram bot (starts at logon)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Done: the bot starts hidden at every logon and is starting now. Log: $Log"
Write-Host "First start? The one-time owner link is in the log."
