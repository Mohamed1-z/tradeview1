@echo off
rem Launcher to run app.py and keep the window open for double-clicks
cd /d %~dp0
python app.py %*
pause
