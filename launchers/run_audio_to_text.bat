@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo   Qwen3-ASR-0.6B 音频转文本工具
echo ============================================
echo.
echo [1/2] 检查依赖...
python -c "import funasr" 2>nul
if errorlevel 1 (
    echo [错误] 未安装 funasr，请先运行:
    echo   pip install funasr torch torchaudio
    echo.
    pause
    exit /b 1
)
echo [OK] 依赖检查通过
echo.
echo [2/2] 开始处理...
echo.
python tools\common\audio\to_md.py input --outdir ./output_text --model qwen3asr --recursive
pause
