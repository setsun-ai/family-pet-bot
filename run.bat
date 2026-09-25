@echo off
rem family-pet-bot: start (first run = setup). Double-click. / Start (first run = setup).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 launcher.py %*
) else (
    python launcher.py %*
)
echo.
echo Bot stopped. Read any error above. / Bot ostanovlen.
pause
