@echo off
chcp 65001 >nul
cd /d "%~dp0"
python video_to_text.py input --outdir ./output_text --model sensevoice --recursive
pause
