@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if "%~1"=="" (
  echo 用法: 把 .md 文件拖到本 bat 上，或命令行传入路径
  echo 示例: run_md_to_pdf.bat D:\path\to\note.md
  pause
  exit /b
)
if exist "D:\mamba\envs\proaes\python.exe" (
  set PY=D:\mamba\envs\proaes\python.exe
) else (
  set PY=python
)
"%PY%" tools\common\md\to_pdf.py "%~1" --open
pause
