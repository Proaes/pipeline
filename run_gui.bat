@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\mamba\envs\proaes\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting pipeline GUI... browser will open automatically.
echo Keep this window open; closing it stops the server.
"%PY%" gui\server.py
pause
