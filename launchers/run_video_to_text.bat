@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python tools\common\video\to_md.py input --outdir ./output_text --model sensevoice --recursive
pause
