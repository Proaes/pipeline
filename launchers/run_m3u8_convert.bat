@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo   M3U8 本地视频转换工具
echo ============================================
echo.
echo [1/2] 检查 ffmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 ffmpeg
    pause
    exit /b 1
)
echo [OK] ffmpeg 可用
echo.
echo [2/2] 开始转换...
echo.
python tools\common\url\fix_m3u8.py input --convert --outdir output
echo.
pause
