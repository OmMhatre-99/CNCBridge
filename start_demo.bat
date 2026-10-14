@echo off
cd /d "%~dp0"
python -m cncbridge --demo %*
if errorlevel 1 pause
