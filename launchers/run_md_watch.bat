@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if "%~1"=="" (
  echo 用法: 把 .md 文件拖到本 bat 上，或命令行传入路径
  echo 监听模式: 保存 md 后自动编译 PDF，Ctrl+C 退出
  pause
  exit /b
)
if exist "D:\mamba\envs\proaes\python.exe" (
  set PY=D:\mamba\envs\proaes\python.exe
) else (
  set PY=python
)
"%PY%" tools\common\md\watch.py "%~1" --open
pause
