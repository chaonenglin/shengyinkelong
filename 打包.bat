@echo off
chcp 65001 >nul 2>&1
title 打包为 EXE

echo 正在打包...
pyinstaller 音频转虚拟麦克风.spec --noconfirm --distpath bagpag

echo 复制 VB-Cable 安装程序...
copy /Y "VBCABLE_Setup_x64.exe" "bagpag\VBCABLE_Setup_x64.exe" >nul

echo.
echo 打包完成！EXE 在 bagpag 目录下。
pause
