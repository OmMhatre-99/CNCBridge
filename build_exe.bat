@echo off
rem Builds dist\CNCBridge.exe and dist\CNCBridgeDemo.exe. Each is a single file that runs without Python installed.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || exit /b 1
python -m pip install -r requirements.txt || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CNCBridge CNCBridge.pyw || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CNCBridgeDemo CNCBridgeDemo.pyw || exit /b 1
echo.
echo Built dist\CNCBridge.exe and dist\CNCBridgeDemo.exe
