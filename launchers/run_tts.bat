@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PY=D:\mamba\envs\tts\python.exe
if not exist "%PY%" set PY=python

echo ============================================
echo   文本转语音（GENIE / GPT-SoVITS ONNX，CPU）
echo ============================================
echo.
echo [1/3] 检查环境...
"%PY%" tools\common\text\to_audio.py --check
if errorlevel 1 (
    echo.
    echo [错误] 环境检查未通过，请看上面的提示
    pause
    exit /b 1
)
echo.
echo [2/3] 可用音色...
"%PY%" tools\common\text\to_audio.py --list-voices
echo.
echo [3/3] 用法（把下面的输入换成自己的文件）:
echo.
echo     "%PY%" tools\common\text\to_audio.py 你的笔记.md --voice feibi -o output\笔记.mp3
echo.
echo   常用可选参数:
echo     --format wav          输出 wav 而不是 mp3
echo     --trim-tail-ms 60     结尾有杂音时裁掉这么多毫秒
echo     --tail-pad 0.7        收尾静音长度（秒）
echo     --dry-run             只看切句结果，不合成
echo     --engine sapi         零依赖冒烟测试（系统语音，音质老）
echo.
pause
