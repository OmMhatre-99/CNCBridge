@echo off
setlocal EnableExtensions
rem ===========================================================================
rem Builds single-file programs into dist\ (Python is not needed to run them).
rem
rem   build_exe.bat          CNCBridge.exe, and CNCBridgeDemo.exe when this
rem                          branch has a demo\ folder. Both build at once.
rem   build_exe.bat app      only CNCBridge.exe
rem   build_exe.bat demo     only CNCBridgeDemo.exe
rem   build_exe.bat clean    also discard PyInstaller's cache (if a build looks stale)
rem
rem Nothing changed since the last build: a few seconds. Something changed:
rem about 30 seconds, because PyInstaller then analyses the program again.
rem Details of each build are written to build\<name>.log.
rem ===========================================================================
cd /d "%~dp0"
if "%~1"==":build" goto build_one

set ARG_APP=0
set ARG_DEMO=0
set CLEAN=
for %%A in (%*) do (
  if /i "%%A"=="app" set ARG_APP=1
  if /i "%%A"=="demo" set ARG_DEMO=1
  if /i "%%A"=="clean" set CLEAN=--clean
)
set WANT_APP=1
set WANT_DEMO=1
if "%ARG_APP%%ARG_DEMO%"=="10" set WANT_DEMO=0
if "%ARG_APP%%ARG_DEMO%"=="01" set WANT_APP=0

rem The demo exe is only useful when the demo code is in it. PyInstaller does not
rem complain when demo\ is missing, it just builds an exe that fails at start.
set HAVE_DEMO=0
if exist "demo\control.py" set HAVE_DEMO=1
rem (An exit inside two nested blocks loses its exit code in cmd, so the blocks stay one level deep.)
if "%HAVE_DEMO%%ARG_DEMO%"=="01" (
  echo ERROR: this branch has no demo\ folder, so CNCBridgeDemo.exe cannot be built here.
  echo        Switch to a branch that has it, for example the demo branch.
  exit /b 1
)
if "%HAVE_DEMO%%WANT_DEMO%"=="01" (
  echo Note: this branch has no demo\ folder, so CNCBridgeDemo.exe is skipped.
  set WANT_DEMO=0
)
if "%WANT_DEMO%"=="1" if not exist "CNCBridgeDemo.pyw" (
  echo ERROR: CNCBridgeDemo.pyw is missing.
  exit /b 1
)
if "%WANT_APP%%WANT_DEMO%"=="00" (
  echo Nothing to build.
  exit /b 1
)

python -c "import PyInstaller" 2>nul && goto have_pyinstaller
echo Installing PyInstaller (first time only)...
python -m pip install pyinstaller || exit /b 1
:have_pyinstaller

rem Windows will not replace a program that is running.
if "%WANT_APP%"=="1" call :unlocked CNCBridge || exit /b 1
if "%WANT_DEMO%"=="1" call :unlocked CNCBridgeDemo || exit /b 1

set FONTS=
if exist "assets\fonts" set FONTS=--add-data "%~dp0assets\fonts;assets/fonts"
if not exist build mkdir build
del "build\*.done" 2>nul

rem Two builds at once take about as long as one. --clean shares one cache folder,
rem so a clean build runs them one after the other.
set PARALLEL=0
if "%WANT_APP%%WANT_DEMO%"=="11" if "%CLEAN%"=="" set PARALLEL=1

if "%WANT_APP%"=="1" echo Building dist\CNCBridge.exe ...
if "%WANT_DEMO%"=="1" echo Building dist\CNCBridgeDemo.exe ...
if "%PARALLEL%"=="1" (
  start "" /b cmd /c call "%~f0" :build CNCBridgeDemo
  call "%~f0" :build CNCBridge
) else (
  if "%WANT_APP%"=="1" call "%~f0" :build CNCBridge
  if "%WANT_DEMO%"=="1" call "%~f0" :build CNCBridgeDemo
)
if "%WANT_APP%"=="1" call :await CNCBridge || exit /b 1
if "%WANT_DEMO%"=="1" call :await CNCBridgeDemo || exit /b 1

rem Check that the code really is inside each exe.
if "%WANT_APP%"=="1" call :verify CNCBridge cncbridge.app || exit /b 1
if "%WANT_DEMO%"=="1" call :verify CNCBridgeDemo demo.control || exit /b 1

rem Signing is optional: set CNCBRIDGE_SIGN_CERT (see README, "Signing the programs").
call :sign || exit /b 1
if defined CNCBRIDGE_SIGN_CERT if "%WANT_APP%"=="1" call :verify CNCBridge cncbridge.app || exit /b 1
if defined CNCBRIDGE_SIGN_CERT if "%WANT_DEMO%"=="1" call :verify CNCBridgeDemo demo.control || exit /b 1

echo.
if "%WANT_APP%"=="1" echo Built dist\CNCBridge.exe
if "%WANT_DEMO%"=="1" echo Built dist\CNCBridgeDemo.exe
if not defined CNCBRIDGE_SIGN_CERT echo Not signed. To sign the programs, set CNCBRIDGE_SIGN_CERT to a code-signing certificate ^(see README^).
exit /b 0

:build_one
rem Runs on its own so that two can run side by side. %2 is the program name.
python -m PyInstaller --noconfirm %CLEAN% --onefile --windowed %FONTS% --name %~2 --specpath build --workpath "build\%~2" --distpath dist %~2.pyw > "build\%~2.log" 2>&1
rem The status goes first on the line: a digit right before > would be read as a file handle.
>"build\%~2.done" echo %errorlevel%
exit /b 0

:await
if exist "build\%~1.done" goto await_done
ping -n 2 127.0.0.1 >nul
goto await
:await_done
set /p RC=<"build\%~1.done"
if not "%RC%"=="0" (
  echo.
  echo ERROR: building %~1.exe failed. Last lines of build\%~1.log:
  powershell -NoProfile -Command "Get-Content 'build\%~1.log' -Tail 15"
  exit /b 1
)
exit /b 0

:verify
python -c "import sys;from PyInstaller.archive.readers import CArchiveReader as R;sys.exit(0 if sys.argv[2] in R(sys.argv[1]).open_embedded_archive('PYZ.pyz').toc else 1)" "dist\%~1.exe" %~2
if errorlevel 1 (
  echo ERROR: dist\%~1.exe was built but does not contain %~2, so it would fail at start.
  exit /b 1
)
exit /b 0

:unlocked
rem Appending nothing to the file fails while the program is running.
if not exist "dist\%~1.exe" exit /b 0
( type nul >> "dist\%~1.exe" ) 2>nul && exit /b 0
echo ERROR: dist\%~1.exe is in use. Close it first, because Windows will not replace a running program.
exit /b 1

:sign
if not defined CNCBRIDGE_SIGN_CERT exit /b 0
set SIGN_FILES=
if "%WANT_APP%"=="1" set SIGN_FILES="dist\CNCBridge.exe"
if "%WANT_DEMO%"=="1" set SIGN_FILES=%SIGN_FILES% "dist\CNCBridgeDemo.exe"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sign_exe.ps1" %SIGN_FILES%
if errorlevel 1 (
  echo ERROR: signing failed. The programs in dist were built but are not signed.
  exit /b 1
)
exit /b 0

