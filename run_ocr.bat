@echo off
chcp 65001 >nul
cd /d "%~dp0"
python batch_ocr.py -i ./input -o ./output
pause
