@echo off
setlocal EnableExtensions
rem ===========================================================================
rem Builds dist\CNCBridgeSetup.exe: one file that installs CNCBridge on a PC (or removes it).
rem It carries dist\CNCBridge.exe (and CNCBridgeDemo.exe and README.md when they exist),
rem so build_exe.bat has to run first. release.bat runs both.
rem
rem   build_setup.bat          builds it, asking for administrator rights when it is run
rem   build_setup.bat noadmin  same without that request, only for trying a build
rem                            with  CNCBridgeSetup.exe /S --test-root <folder>
rem ===========================================================================
cd /d "%~dp0"
if not exist "dist\CNCBridge.exe" (
  echo ERROR: dist\CNCBridge.exe is missing. Run build_exe.bat first.
  exit /b 1
)
python -c "import PyInstaller" 2>nul && goto have_pyinstaller
echo Installing PyInstaller (first time only)...
python -m pip install pyinstaller || exit /b 1
:have_pyinstaller

set ADMIN=--uac-admin
if /i "%~1"=="noadmin" set ADMIN=
set PAYLOAD=--add-data "%~dp0dist\CNCBridge.exe;payload"
if exist "dist\CNCBridgeDemo.exe" set PAYLOAD=%PAYLOAD% --add-data "%~dp0dist\CNCBridgeDemo.exe;payload"
if exist "README.md" set PAYLOAD=%PAYLOAD% --add-data "%~dp0README.md;payload"

if not exist build mkdir build
echo Building dist\CNCBridgeSetup.exe ...
python -m PyInstaller --noconfirm --onefile --windowed %ADMIN% %PAYLOAD% --name CNCBridgeSetup --specpath build --workpath "build\CNCBridgeSetup" --distpath dist CNCBridgeSetup.pyw > "build\CNCBridgeSetup.log" 2>&1
if errorlevel 1 (
  echo ERROR: building CNCBridgeSetup.exe failed. Last lines of build\CNCBridgeSetup.log:
  powershell -NoProfile -Command "Get-Content 'build\CNCBridgeSetup.log' -Tail 15"
  exit /b 1
)
python -c "import sys;from PyInstaller.archive.readers import CArchiveReader as R;sys.exit(0 if sys.argv[2] in R(sys.argv[1]).open_embedded_archive('PYZ.pyz').toc else 1)" "dist\CNCBridgeSetup.exe" installer.core
if errorlevel 1 (
  echo ERROR: dist\CNCBridgeSetup.exe was built but does not contain installer.core, so it would fail at start.
  exit /b 1
)

rem Signing is optional, as for the other programs (see README, "Signing the programs").
if defined CNCBRIDGE_SIGN_CERT (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sign_exe.ps1" "dist\CNCBridgeSetup.exe"
  if errorlevel 1 (
    echo ERROR: signing failed. dist\CNCBridgeSetup.exe was built but is not signed.
    exit /b 1
  )
)
echo Built dist\CNCBridgeSetup.exe
exit /b 0
