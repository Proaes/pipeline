@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python tools\common\images\to_md.py -i ./input -o ./output
pause
