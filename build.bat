@echo off
setlocal
cd /d "%~dp0"
echo SC Capacitor Overlay - Windows release build
where python >nul 2>nul
if errorlevel 1 goto no_python
if not exist build_env\Scripts\python.exe python -m venv build_env
if errorlevel 1 goto failed
build_env\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6.9,<7"
if errorlevel 1 goto failed
build_env\Scripts\python.exe release_build.py
if errorlevel 1 goto failed
echo.
echo Build succeeded. Release assets:
echo   dist\SC_Capacitor_Overlay.exe
echo   dist\SC_Capacitor_Overlay.exe.sha256
echo Upload BOTH files when publishing a release manually.
echo GitHub Actions can build and attach both automatically instead.
pause
exit /b 0
:no_python
echo Python was not found. Install Python 3.12 and select Add Python to PATH.
pause
exit /b 1
:failed
echo BUILD FAILED. Do not distribute an older EXE still in dist.
echo Read the error above, correct it, then run this build again.
pause
exit /b 1
