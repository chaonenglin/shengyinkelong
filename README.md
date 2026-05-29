# 音频 → 虚拟麦克风 播放工具

将本地音频文件通过虚拟麦克风发送，适用于在线会议、直播、语音通话等场景。

## 功能

- **MP3 播放** — 支持 MP3、WAV、FLAC、OGG、M4A、AAC 等常见音频格式
- **Loopback 截取** — 实时截取系统音频输出，转发到虚拟麦克风
- **虚拟麦克风** — 通过 VB-Audio Virtual Cable 将音频发送为麦克风输入
- **实时监听** — 可同时输出到扬声器，听到音频播放内容
- **播放列表** — 支持文件夹批量加载，顺序/随机播放
- **拖拽支持** — 直接拖拽音频文件或文件夹到窗口

## 环境要求

- Windows 10/11
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)（首次运行会自动提示安装）
- FFmpeg（用于 MP3 解码）

## 使用方法

### 方式一：下载打包好的 EXE

前往 [Releases](https://github.com/chaonenglin/shengyinkelong/releases) 下载最新版本，解压后直接运行。

### 方式二：从源码运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装 VB-Audio Virtual Cable
# 运行 VBCABLE_Setup_x64.exe 安装虚拟音频设备

# 3. 启动
python mp3_to_mic.py
```

### 打包为 EXE

双击 `打包.bat`，打包完成后在 `bagpag` 文件夹中找到 exe 文件。

## 使用步骤

1. 启动程序后，选择「虚拟麦克风」设备（通常为 CABLE Input）
2. 选择「扬声器」设备用于监听（可选）
3. 选择播放模式：
   - **MP3 播放**：选择音频文件或拖拽文件夹，点击播放
   - **Loopback 截取**：选择要截取的音频设备，点击开始截取
4. 在会议/直播软件中选择「CABLE Output」作为麦克风输入

## 项目结构

```
├── mp3_to_mic.py          # 主程序
├── 图标/
│   ├── 图标.ico           # 程序图标
│   ├── 图标.png
│   └── 背景.jpg           # 界面背景
├── 启动.bat               # 从源码启动
├── 打包.bat               # 打包为 EXE
├── requirements.txt       # Python 依赖
└── 音频转虚拟麦克风.spec   # PyInstaller 打包配置
```
