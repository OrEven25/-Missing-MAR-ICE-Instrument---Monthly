@echo off
cd /d C:\Users\or.even\DQE
git add -A
git status --short
set /p msg="Commit message (or press Enter for 'Update dashboard'): "
if "%msg%"=="" set msg=Update dashboard
git commit -m "%msg%"
git push
echo.
echo Done! Press any key to close.
pause >nul
