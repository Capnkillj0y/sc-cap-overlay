@echo off
REM Installs Tesseract OCR (required for sc_capacitor_ocr.py / the built exe)
REM using Windows' built-in winget package manager. Needs Windows 10 1809+ or
REM Windows 11 with "App Installer" (winget ships with these by default).

where winget >nul 2>nul
if errorlevel 1 (
    echo winget isn't available on this system.
    echo Please install Tesseract manually instead:
    echo https://github.com/UB-Mannheim/tesseract/wiki
    pause
    exit /b 1
)

echo Installing Tesseract OCR via winget...
winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements

echo.
echo Done. If this window shows any errors above, install manually from:
echo https://github.com/UB-Mannheim/tesseract/wiki
pause
