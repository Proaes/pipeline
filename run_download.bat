@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   视频下载工具 (B站/YouTube)
echo ============================================
echo.
echo 请输入完整视频链接 (https://...):
echo (例如: https://www.bilibili.com/video/BVxxxxxxxxxx)
echo.
set /p URL="> "
if "%URL%"=="" (
    echo [错误] 未输入链接
    pause
    exit /b 1
)
echo.
echo 选择操作:
echo   1. 下载视频 (默认最高画质)
echo   2. 下载视频 + 自动转文字
echo   3. 仅下载音频
echo   4. 仅下载音频 + 自动转文字
echo.
set /p CHOICE="请选择 [1-4]: "
echo.

if "%CHOICE%"=="1" (
    python download_video.py "%URL%" --output ./download --cookies cookies.txt
) else if "%CHOICE%"=="2" (
    python download_video.py "%URL%" --output ./download --cookies cookies.txt --transcribe --model qwen3asr
) else if "%CHOICE%"=="3" (
    python download_video.py "%URL%" --output ./download --cookies cookies.txt --audio-only
) else if "%CHOICE%"=="4" (
    python download_video.py "%URL%" --output ./download --cookies cookies.txt --audio-only --transcribe --model qwen3asr
) else (
    echo [错误] 无效选择
)
pause
