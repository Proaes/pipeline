@echo off
cd /d "%~dp0.."
echo ============================================
echo   Video Downloader (Bilibili / YouTube)
echo ============================================
echo.
echo Enter full video URL (https://...):
echo Example: https://www.bilibili.com/video/BVxxxxxxxxxx
echo.
set /p URL="URL: "
if "%URL%"=="" (
    echo [ERROR] No URL entered.
    pause
    exit /b 1
)
echo.
echo Select action:
echo   1. Download video (best quality)
echo   2. Download video + auto transcribe
echo   3. Download audio only
echo   4. Download audio only + auto transcribe
echo.
set /p CHOICE="Choice [1-4]: "
echo.

if "%CHOICE%"=="1" goto c1
if "%CHOICE%"=="2" goto c2
if "%CHOICE%"=="3" goto c3
if "%CHOICE%"=="4" goto c4
echo [ERROR] Invalid choice.
goto end

:c1
python tools\common\url\to_mp4.py "%URL%" --output ./download --cookies cookies.txt
goto end

:c2
python tools\common\url\to_mp4.py "%URL%" --output ./download --cookies cookies.txt --transcribe --model qwen3asr
goto end

:c3
python tools\common\url\to_mp4.py "%URL%" --output ./download --cookies cookies.txt --audio-only
goto end

:c4
python tools\common\url\to_mp4.py "%URL%" --output ./download --cookies cookies.txt --audio-only --transcribe --model qwen3asr
goto end

:end
cd launchers
pause