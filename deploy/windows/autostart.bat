@echo off
rem Double-click = start the bot automatically (hidden) at every Windows logon. Remove: autostart.bat -Remove
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" %*
pause
