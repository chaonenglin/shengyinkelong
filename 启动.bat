@echo off
chcp 65001 >nul 2>&1
title 音频 → 虚拟麦克风

echo 检查依赖...
pip install -q sounddevice numpy pydub PyAudioWPatch pycaw comtypes 2>nul

echo 检查 ffmpeg...
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在安装 ffmpeg...
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements >nul 2>&1
)

echo 启动工具...
python mp3_to_mic.py
pause
