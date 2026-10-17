@echo off
rem Builds the programs, then packs them into release\ with checksums (see release.py).
rem Options are passed on to build_exe.bat, for example: release.bat clean
cd /d "%~dp0"
call "%~dp0build_exe.bat" %* || exit /b 1
python "%~dp0release.py"
