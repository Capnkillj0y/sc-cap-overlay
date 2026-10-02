@echo off
cd /d "%~dp0"
python sc_capacitor_ocr.py
if errorlevel 1 pause
