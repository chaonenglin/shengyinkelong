@echo off
chcp 65001 >nul 2>&1
title 打包为 EXE

echo 正在打包...
pyinstaller 音频转虚拟麦克风.spec --noconfirm --distpath bagpag

echo 复制 VB-Cable 安装包...
xcopy /E /I /Y "vbcable" "bagpag\vbcable" >nul

echo.
echo 打包完成！EXE 在 bagpag 目录下。
pause
