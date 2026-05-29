"""
音频 → 虚拟麦克风 播放工具
支持 MP3 播放和 Loopback 截取，通过虚拟音频设备（VB-Audio Virtual Cable）发送。

依赖：pip install PyQt6 sounddevice numpy pydub PyAudioWPatch pycaw comtypes
系统依赖：ffmpeg、VB-Audio Virtual Cable（首次运行自动安装）
"""

import os, sys, struct, threading, subprocess
from pathlib import Path

import numpy as np
import sounddevice as sd
from pydub import AudioSegment
import pyaudiowpatch as pyaudio
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSlider, QListWidget, QFrame,
    QFileDialog, QMessageBox, QListWidgetItem
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QPixmap, QFont, QColor, QPainter, QLinearGradient

# === 常量 ===
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
VBAUDIO_KEYWORDS = ["cable", "virtual", "vb-audio", "virtualaudio", "voicemeeter", "虚拟"]
SPEAKER_KEYWORDS = ["realtek", "扬声器", "speakers", "headphones", "耳机"]
EXCLUDE_KEYWORDS = ["steam", "nvidia", "hdmi", "microsoft sound mapper", "16ch"]
CHUNK_SIZE = 4096

# === 配色 ===
BG = "#0a171d"
BG2 = "#0f1f2d"
BG3 = "#1a2a3a"
ACCENT = "#c43a3a"
GOLD = "#c9a96e"
TEXT = "#e8d5c4"
TEXT2 = "#7a8a9a"


def find_ffmpeg():
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    home = os.path.expanduser("~")
    winget_pkg = os.path.join(home, r"AppData\Local\Microsoft\WinGet\Packages")
    if os.path.isdir(winget_pkg):
        for d in os.listdir(winget_pkg):
            if "ffmpeg" in d.lower():
                for root, dirs, files in os.walk(os.path.join(winget_pkg, d)):
                    if "ffmpeg.exe" in files:
                        return os.path.join(root, "ffmpeg.exe")
    return None


def get_wasapi_devices():
    p = pyaudio.PyAudio()
    devices = []
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxOutputChannels"] > 0:
                host = p.get_host_api_info_by_index(info["hostApi"])["name"]
                if "WASAPI" in host:
                    name = info["name"]
                    is_virtual = any(kw in name.lower() for kw in VBAUDIO_KEYWORDS)
                    is_speaker = any(kw in name.lower() for kw in SPEAKER_KEYWORDS)
                    is_excluded = any(kw in name.lower() for kw in EXCLUDE_KEYWORDS)
                    devices.append({
                        "index": i, "name": name,
                        "is_virtual": is_virtual,
                        "is_speaker": is_speaker and not is_excluded,
                    })
    finally:
        p.terminate()
    return devices


def get_loopback_devices():
    p = pyaudio.PyAudio()
    devices = []
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                host = p.get_host_api_info_by_index(info["hostApi"])["name"]
                name = info["name"]
                if "WASAPI" in host and ("loopback" in name.lower() or "立体声混音" in name):
                    devices.append({"index": i, "name": name})
    finally:
        p.terminate()
    return devices


def find_cable_input(devices):
    for d in devices:
        if "cable input" in d["name"].lower() and "16ch" not in d["name"].lower():
            return d
    return None


def find_real_speaker(devices):
    for d in devices:
        if d.get("is_speaker") and "realtek" in d["name"].lower():
            return d
    for d in devices:
        if d.get("is_speaker"):
            return d
    return None


def decode_audio(file_path, ffmpeg_path=None):
    file_path = str(file_path)
    ext = Path(file_path).suffix.lower()
    if ext == ".mp3" and ffmpeg_path:
        cmd = [ffmpeg_path, "-i", file_path, "-f", "wav", "-acodec", "pcm_s16le",
               "-ar", "48000", "-ac", "2", "pipe:1"]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 解码失败")
        data = result.stdout
        idx = data.find(b"data")
        if idx == -1:
            raise RuntimeError("ffmpeg 输出格式错误")
        idx += 4
        if len(data) >= idx + 4:
            size = struct.unpack("<I", data[idx:idx+4])[0]
            pcm_data = data[idx+4:idx+4+size]
        else:
            pcm_data = data[idx:]
        audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
        return audio.reshape(-1, 2), 48000
    else:
        seg = AudioSegment.from_file(file_path).set_frame_rate(48000).set_channels(2)
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32).reshape(-1, 2)
        mx = np.max(np.abs(samples))
        if mx > 0:
            samples /= mx
        return samples, 48000


_ffmpeg = find_ffmpeg()
if _ffmpeg:
    AudioSegment.converter = _ffmpeg


# === WASAPI 播放流 ===
class WASAPIStream:
    def __init__(self, device_index, sample_rate, channels):
        self.p = pyaudio.PyAudio()
        self.idx = device_index
        self.sr = sample_rate
        self.ch = channels
        self.stream = None
        self._frame = 0
        self._data = None
        self._stop = False

    def start(self, audio_data, volume=1.0):
        self._data = audio_data
        self._frame = 0
        self._vol = volume
        self._stop = False

        def cb(in_data, frame_count, time_info, status):
            if self._stop:
                return (b'', pyaudio.paComplete)
            s = self._frame
            e = min(s + frame_count, len(self._data))
            chunk = self._data[s:e] * self._vol
            raw = (chunk * 32767).astype(np.int16).tobytes()
            if len(chunk) < frame_count:
                raw += b'\x00' * (frame_count - len(chunk)) * self.ch * 2
                self._frame = len(self._data)
                return (raw, pyaudio.paComplete)
            self._frame = e
            return (raw, pyaudio.paContinue)

        self.stream = self.p.open(format=pyaudio.paInt16, channels=self.ch,
                                   rate=self.sr, output=True,
                                   output_device_index=self.idx,
                                   frames_per_buffer=CHUNK_SIZE, stream_callback=cb)
        self.stream.start_stream()

    def is_active(self):
        return self.stream and self.stream.is_active()

    def stop(self):
        self._stop = True
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
        self.p.terminate()


# === WASAPI Loopback 捕获 ===
class WASAPICapture:
    def __init__(self, capture_idx, output_idx, monitor_idx=None, sr=48000, ch=2):
        self.p = pyaudio.PyAudio()
        self.cap_idx = capture_idx
        self.out_idx = output_idx
        self.mon_idx = monitor_idx
        self.sr = sr
        self.ch = ch
        self.cap_stream = None
        self.out_stream = None
        self.mon_stream = None
        self._stop = False
        self._vol = 1.0
        self._buf_out = bytearray()
        self._buf_mon = bytearray()
        self._lock = threading.Lock()

    def start(self, volume=1.0):
        self._vol = volume
        self._stop = False

        def _read(buf):
            need = CHUNK_SIZE * self.ch * 2
            with self._lock:
                if len(buf) >= need:
                    d = bytes(buf[:need])
                    del buf[:need]
                else:
                    d = bytes(buf) + b'\x00' * (need - len(buf))
                    buf.clear()
            return d

        def out_cb(in_data, frame_count, time_info, status):
            return (_read(self._buf_out), pyaudio.paContinue)

        self.out_stream = self.p.open(format=pyaudio.paInt16, channels=self.ch,
                                       rate=self.sr, output=True,
                                       output_device_index=self.out_idx,
                                       frames_per_buffer=CHUNK_SIZE, stream_callback=out_cb)

        if self.mon_idx is not None and self.mon_idx != self.out_idx:
            def mon_cb(in_data, frame_count, time_info, status):
                return (_read(self._buf_mon), pyaudio.paContinue)
            self.mon_stream = self.p.open(format=pyaudio.paInt16, channels=self.ch,
                                           rate=self.sr, output=True,
                                           output_device_index=self.mon_idx,
                                           frames_per_buffer=CHUNK_SIZE, stream_callback=mon_cb)

        def cap_cb(in_data, frame_count, time_info, status):
            if self._stop:
                return (b'', pyaudio.paComplete)
            if self._vol != 1.0:
                a = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
                a = (a * self._vol).clip(-32768, 32767).astype(np.int16).tobytes()
            else:
                a = in_data
            with self._lock:
                self._buf_out.extend(a)
                self._buf_mon.extend(a)
            return (b'', pyaudio.paContinue)

        self.cap_stream = self.p.open(format=pyaudio.paInt16, channels=self.ch,
                                       rate=self.sr, input=True,
                                       input_device_index=self.cap_idx,
                                       frames_per_buffer=CHUNK_SIZE, stream_callback=cap_cb)

        self.cap_stream.start_stream()
        self.out_stream.start_stream()
        if self.mon_stream:
            self.mon_stream.start_stream()

    def is_active(self):
        return self.cap_stream and self.cap_stream.is_active()

    def set_volume(self, v):
        self._vol = v

    def stop(self):
        self._stop = True
        for s in [self.cap_stream, self.out_stream, self.mon_stream]:
            if s:
                try:
                    s.stop_stream()
                    s.close()
                except:
                    pass
        self.p.terminate()


# === 解码线程 ===
class DecodeThread(QThread):
    done = pyqtSignal(object, int)  # audio, sr
    error = pyqtSignal(str)

    def __init__(self, path, ffmpeg):
        super().__init__()
        self.path = path
        self.ffmpeg = ffmpeg

    def run(self):
        try:
            audio, sr = decode_audio(self.path, self.ffmpeg)
            self.done.emit(audio, sr)
        except Exception as e:
            self.error.emit(str(e))


# === 主窗口 ===
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("音频 → 虚拟麦克风")
        self.setFixedSize(500, 620)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.audio_data = None
        self.sample_rate = 48000
        self.current_file = None
        self.wasapi_streams = []
        self.capture = None
        self.volume = 0.8
        self.auto_play = True
        self.play_mode = "seq"
        self._audio_files = []
        self._drag_pos = None
        self._ffmpeg = find_ffmpeg()

        self._build_ui()
        self._refresh_devices()

    def _build_ui(self):
        # 中心 widget
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)
        self._layout = QVBoxLayout(central)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # 背景画布
        self._bg_label = QLabel(central)
        self._bg_label.setGeometry(0, 0, 500, 620)
        self._bg_label.lower()

        bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "图标", "背景.jpg")
        if os.path.isfile(bg_path):
            pixmap = QPixmap(bg_path).scaled(500, 620, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation)
            # 半透明遮罩
            overlay = QPixmap(500, 620)
            overlay.fill(QColor(10, 23, 29, 130))
            result = QPixmap(500, 620)
            result.fill(Qt.GlobalColor.transparent)
            painter = QPainter(result)
            painter.drawPixmap(0, 0, pixmap)
            painter.drawPixmap(0, 0, overlay)
            painter.end()
            self._bg_label.setPixmap(result)
        else:
            self._bg_label.setStyleSheet(f"background-color: {BG};")

        # 内容容器（透明背景）
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(15, 8, 15, 8)
        layout.setSpacing(6)
        self._layout.addWidget(content)

        # === 标题栏（可拖动） ===
        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet("background: transparent;")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("音频 → 虚拟麦克风  by：超能力是超能吃")
        title.setStyleSheet(f"color: {GOLD}; font: bold 14px 'Microsoft YaHei'; background: transparent;")
        title_layout.addWidget(title)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet(f"color: {TEXT2}; background: transparent; border: none; font: 14px;")
        close_btn.clicked.connect(self.close)
        title_layout.addWidget(close_btn)
        layout.addWidget(title_bar)

        # 分隔线
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {ACCENT};")
        layout.addWidget(line)

        # === 设备选择区 ===
        dev_widget = QWidget()
        dev_widget.setStyleSheet("background: transparent;")
        dev_layout = QVBoxLayout(dev_widget)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.setSpacing(4)

        # 虚拟麦克风
        mic_row = self._make_device_row("虚拟麦克风", "mic")
        dev_layout.addWidget(mic_row)

        # 扬声器
        spk_row = self._make_device_row("扬声器（监听）", "spk")
        dev_layout.addWidget(spk_row)

        layout.addWidget(dev_widget)

        # === 模式切换 ===
        mode_widget = QWidget()
        mode_widget.setStyleSheet("background: transparent;")
        mode_layout = QHBoxLayout(mode_widget)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(8)

        self.mp3_btn = QPushButton("MP3 播放")
        self.mp3_btn.setFixedHeight(36)
        self.mp3_btn.setStyleSheet(self._btn_style(ACCENT, "white", bold=True))
        self.mp3_btn.clicked.connect(lambda: self._switch_mode("mp3"))
        mode_layout.addWidget(self.mp3_btn)

        self.cap_btn = QPushButton("Loopback 截取")
        self.cap_btn.setFixedHeight(36)
        self.cap_btn.setStyleSheet(self._btn_style(BG3, TEXT2))
        self.cap_btn.clicked.connect(lambda: self._switch_mode("capture"))
        mode_layout.addWidget(self.cap_btn)

        layout.addWidget(mode_widget)

        # === MP3 面板 ===
        self.mp3_panel = QWidget()
        self.mp3_panel.setStyleSheet("background: transparent;")
        mp3_layout = QVBoxLayout(self.mp3_panel)
        mp3_layout.setContentsMargins(0, 0, 0, 0)
        mp3_layout.setSpacing(5)

        # 文件夹选择
        folder_row = QWidget()
        folder_row.setStyleSheet("background: transparent;")
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_btn = QPushButton("选择文件夹")
        folder_btn.setStyleSheet(self._btn_style(BG3, GOLD))
        folder_btn.clicked.connect(self._select_folder)
        folder_layout.addWidget(folder_btn)
        self.folder_label = QLabel("未选择")
        self.folder_label.setStyleSheet(f"color: {TEXT2}; background: transparent; font: 10px 'Microsoft YaHei';")
        folder_layout.addWidget(self.folder_label)
        folder_layout.addStretch()
        mp3_layout.addWidget(folder_row)

        # 拖拽区
        drop_frame = QFrame()
        drop_frame.setFixedHeight(45)
        drop_frame.setStyleSheet(f"border: 1px solid {BG3}; background: transparent;")
        drop_layout = QVBoxLayout(drop_frame)
        drop_label = QLabel("拖拽音频文件夹 / 音频文件到此处")
        drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_label.setStyleSheet(f"color: {TEXT2}; font: 10px 'Microsoft YaHei'; background: transparent;")
        drop_layout.addWidget(drop_label)
        self.setAcceptDrops(True)
        mp3_layout.addWidget(drop_frame)

        # 文件列表
        self.file_list = QListWidget()
        self.file_list.setStyleSheet(f"""
            QListWidget {{
                background: transparent; color: {TEXT}; border: none;
                font: 10px 'Consolas'; selection-background-color: {ACCENT};
                selection-color: white; outline: none;
            }}
            QListWidget::item {{
                padding: 3px 6px; border: none;
            }}
            QListWidget::item:selected {{
                background: {ACCENT}; color: white; border-radius: 3px;
            }}
            QScrollBar:vertical {{
                background: transparent; width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {BG3}; border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self.file_list.itemDoubleClicked.connect(self._play_selected)
        mp3_layout.addWidget(self.file_list, 1)

        # 状态
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color: #4a9; background: transparent; font: 10px 'Microsoft YaHei';")
        mp3_layout.addWidget(self.status_label)

        # 控制区
        ctrl_widget = QWidget()
        ctrl_widget.setStyleSheet("background: transparent;")
        ctrl_layout = QHBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        self.play_btn = QPushButton("播放")
        self.play_btn.setFixedSize(80, 36)
        self.play_btn.setStyleSheet(self._btn_style("#2d5a3d", "white", bold=True))
        self.play_btn.clicked.connect(self._play_selected)
        ctrl_layout.addWidget(self.play_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedSize(80, 36)
        self.stop_btn.setStyleSheet(self._btn_style("#8b2020", "white"))
        self.stop_btn.clicked.connect(self._stop)
        ctrl_layout.addWidget(self.stop_btn)

        self.auto_btn = QPushButton("顺序播放")
        self.auto_btn.setFixedSize(80, 36)
        self.auto_btn.setStyleSheet(self._btn_style(ACCENT, "white"))
        self.auto_btn.clicked.connect(self._toggle_auto)
        ctrl_layout.addWidget(self.auto_btn)

        vol_label = QLabel("音量")
        vol_label.setStyleSheet(f"color: {TEXT2}; background: transparent; font: 10px 'Microsoft YaHei';")
        ctrl_layout.addWidget(vol_label)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ background: {BG3}; height: 4px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {GOLD}; width: 14px; margin: -5px 0; border-radius: 7px; }}
            QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        """)
        self.vol_slider.valueChanged.connect(lambda v: setattr(self, 'volume', v / 100))
        ctrl_layout.addWidget(self.vol_slider)
        ctrl_layout.addStretch()

        mp3_layout.addWidget(ctrl_widget)
        layout.addWidget(self.mp3_panel, 1)

        # === Loopback 截取面板 ===
        self.cap_panel = QWidget()
        self.cap_panel.setStyleSheet("background: transparent;")
        cap_layout = QVBoxLayout(self.cap_panel)
        cap_layout.setContentsMargins(0, 0, 0, 0)
        cap_layout.setSpacing(6)

        cap_info = QLabel("截取音频设备输出 → 虚拟麦克风\n不能截取扬声器自身（会回音）")
        cap_info.setStyleSheet(f"color: {TEXT2}; background: transparent; font: 10px 'Microsoft YaHei';")
        cap_layout.addWidget(cap_info)

        # 截取设备
        cap_dev_row = QWidget()
        cap_dev_row.setStyleSheet("background: transparent;")
        cap_dev_layout = QHBoxLayout(cap_dev_row)
        cap_dev_layout.setContentsMargins(0, 0, 0, 0)
        cap_dev_label = QLabel("截取设备")
        cap_dev_label.setStyleSheet(f"color: {GOLD}; background: transparent; font: 10px 'Microsoft YaHei';")
        cap_dev_layout.addWidget(cap_dev_label)
        self.cap_combo = QComboBox()
        self.cap_combo.setStyleSheet(self._combo_style())
        cap_dev_layout.addWidget(self.cap_combo, 1)
        cap_layout.addWidget(cap_dev_row)

        # 控制按钮
        cap_btn_row = QWidget()
        cap_btn_row.setStyleSheet("background: transparent;")
        cap_btn_layout = QHBoxLayout(cap_btn_row)
        cap_btn_layout.setContentsMargins(0, 0, 0, 0)

        self.cap_start_btn = QPushButton("开始截取")
        self.cap_start_btn.setFixedSize(100, 36)
        self.cap_start_btn.setStyleSheet(self._btn_style("#1a5a8a", "white", bold=True))
        self.cap_start_btn.clicked.connect(self._start_capture)
        cap_btn_layout.addWidget(self.cap_start_btn)

        self.cap_stop_btn = QPushButton("停止")
        self.cap_stop_btn.setFixedSize(100, 36)
        self.cap_stop_btn.setStyleSheet(self._btn_style("#8b2020", "white"))
        self.cap_stop_btn.clicked.connect(self._stop_capture)
        cap_btn_layout.addWidget(self.cap_stop_btn)
        cap_btn_layout.addStretch()
        cap_layout.addWidget(cap_btn_row)

        # 音量
        cap_vol_row = QWidget()
        cap_vol_row.setStyleSheet("background: transparent;")
        cap_vol_layout = QHBoxLayout(cap_vol_row)
        cap_vol_layout.setContentsMargins(0, 0, 0, 0)
        cap_vol_label = QLabel("截取音量")
        cap_vol_label.setStyleSheet(f"color: {TEXT2}; background: transparent; font: 10px 'Microsoft YaHei';")
        cap_vol_layout.addWidget(cap_vol_label)
        self.cap_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.cap_vol_slider.setRange(0, 200)
        self.cap_vol_slider.setValue(100)
        self.cap_vol_slider.setFixedWidth(180)
        self.cap_vol_slider.setStyleSheet(self.vol_slider.styleSheet())
        cap_vol_layout.addWidget(self.cap_vol_slider)
        cap_vol_layout.addStretch()
        cap_layout.addWidget(cap_vol_row)

        self.cap_status = QLabel("未截取")
        self.cap_status.setStyleSheet(f"color: {TEXT2}; background: transparent; font: 10px 'Microsoft YaHei';")
        cap_layout.addWidget(self.cap_status)

        cap_layout.addStretch()
        layout.addWidget(self.cap_panel)

        self.cap_panel.hide()

        # 底部提示
        hints = []
        if not self._ffmpeg:
            hints.append("需要 ffmpeg")
        if hints:
            hint = QLabel(" | ".join(hints))
            hint.setStyleSheet(f"color: {TEXT2}; background: transparent; font: 8px 'Microsoft YaHei';")
            layout.addWidget(hint)

    def _make_device_row(self, label_text, key):
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(label_text)
        label.setStyleSheet(f"color: {GOLD}; background: transparent; font: 10px 'Microsoft YaHei';")
        label.setFixedWidth(90)
        layout.addWidget(label)
        combo = QComboBox()
        combo.setStyleSheet(self._combo_style())
        setattr(self, f"{key}_combo", combo)
        layout.addWidget(combo, 1)
        if key == "mic":
            refresh_btn = QPushButton("刷新")
            refresh_btn.setStyleSheet(self._btn_style(BG3, TEXT))
            refresh_btn.setFixedSize(40, 26)
            refresh_btn.clicked.connect(self._refresh_devices)
            layout.addWidget(refresh_btn)
        return widget

    def _btn_style(self, bg, fg, bold=False):
        weight = "bold" if bold else "normal"
        return f"""
            QPushButton {{
                background-color: {bg}; color: {fg}; border: none;
                font: {weight} 11px 'Microsoft YaHei'; border-radius: 4px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{ background-color: {ACCENT}; color: white; }}
            QPushButton:pressed {{ background-color: #a02020; }}
        """

    def _combo_style(self):
        return f"""
            QComboBox {{
                background: rgba(15,31,45,150); color: {TEXT}; border: 1px solid {BG3};
                border-radius: 3px; padding: 3px 8px; font: 10px 'Microsoft YaHei';
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {BG2}; color: {TEXT}; selection-background-color: {ACCENT};
                border: 1px solid {BG3};
            }}
        """

    def _refresh_devices(self):
        self._devices = get_wasapi_devices()
        mic_devs = [d for d in self._devices if d["is_virtual"]]
        spk_devs = [d for d in self._devices if not d["is_virtual"]]

        self.mic_combo.clear()
        self.mic_devs = mic_devs
        for d in mic_devs:
            self.mic_combo.addItem(d["name"])

        self.spk_combo.clear()
        self.spk_devs = spk_devs
        for d in spk_devs:
            self.spk_combo.addItem(d["name"])

        cable = find_cable_input(self._devices)
        if cable:
            idx = next((i for i, d in enumerate(mic_devs) if d["index"] == cable["index"]), 0)
            self.mic_combo.setCurrentIndex(idx)

        speaker = find_real_speaker(self._devices)
        if speaker:
            idx = next((i for i, d in enumerate(spk_devs) if d["index"] == speaker["index"]), 0)
            self.spk_combo.setCurrentIndex(idx)

        # Loopback 设备
        self._loopback_devices = get_loopback_devices()
        self.cap_combo.clear()
        for d in self._loopback_devices:
            self.cap_combo.addItem(d["name"])

    def _switch_mode(self, mode):
        if mode == "capture":
            self._stop()
        if mode == "mp3":
            self._stop_capture()

        self.mp3_panel.setVisible(mode == "mp3")
        self.cap_panel.setVisible(mode == "capture")

        if mode == "mp3":
            self.mp3_btn.setStyleSheet(self._btn_style(ACCENT, "white", bold=True))
            self.cap_btn.setStyleSheet(self._btn_style(BG3, TEXT2))
        else:
            self.cap_btn.setStyleSheet(self._btn_style(ACCENT, "white", bold=True))
            self.mp3_btn.setStyleSheet(self._btn_style(BG3, TEXT2))

    def _select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder):
        self._audio_files = []
        self.file_list.clear()
        for f in sorted(os.listdir(folder)):
            if Path(f).suffix.lower() in AUDIO_EXTS:
                self._audio_files.append(os.path.join(folder, f))
                self.file_list.addItem(f)
        if self._audio_files:
            self.folder_label.setText(f"{Path(folder).name}  ({len(self._audio_files)} 个文件)")
            self.folder_label.setStyleSheet(f"color: {TEXT}; background: transparent; font: 10px 'Microsoft YaHei';")
            self.file_list.setCurrentRow(0)
        else:
            self.folder_label.setText(f"{Path(folder).name}  (无音频文件)")
            self.folder_label.setStyleSheet(f"color: #f44; background: transparent; font: 10px 'Microsoft YaHei';")

    def _play_selected(self):
        items = self.file_list.selectedItems()
        if not items:
            row = self.file_list.currentRow()
            if 0 <= row < len(self._audio_files):
                self._load_and_play(self._audio_files[row])
            return
        row = self.file_list.row(items[0])
        if row < len(self._audio_files):
            self._load_and_play(self._audio_files[row])

    def _load_and_play(self, path):
        self._stop()
        self.status_label.setText(f"加载: {Path(path).name}...")
        self.status_label.setStyleSheet(f"color: #f90; background: transparent; font: 10px 'Microsoft YaHei';")

        self._decode_thread = DecodeThread(path, self._ffmpeg)
        self._decode_thread.done.connect(lambda a, sr: self._on_decoded(path, a, sr))
        self._decode_thread.error.connect(lambda e: self.status_label.setText(f"解码失败: {e}"))
        self._decode_thread.start()

    def _on_decoded(self, path, audio, sr):
        self.audio_data = audio
        self.sample_rate = sr
        self.current_file = path
        dur = len(audio) / sr
        self.status_label.setText(f"播放: {Path(path).name} ({dur:.1f}s)")
        self.status_label.setStyleSheet(f"color: #4a9; background: transparent; font: 10px 'Microsoft YaHei';")
        self._do_play()

    def _do_play(self):
        if self.audio_data is None:
            return
        mic_idx = self._get_dev_idx(self.mic_combo, self.mic_devs)
        spk_idx = self._get_dev_idx(self.spk_combo, self.spk_devs)

        if mic_idx is None and spk_idx is None:
            return

        self.wasapi_streams = []
        audio = self.audio_data
        sr = self.sample_rate
        ch = audio.shape[1] if audio.ndim > 1 else 2

        if mic_idx is not None:
            s = WASAPIStream(mic_idx, sr, ch)
            s.start(audio, self.volume)
            self.wasapi_streams.append(s)
        if spk_idx is not None:
            s = WASAPIStream(spk_idx, sr, ch)
            s.start(audio, self.volume)
            self.wasapi_streams.append(s)

        self.status_label.setText("播放中...")
        self.status_label.setStyleSheet(f"color: #4a9; background: transparent; font: 10px 'Microsoft YaHei';")

        self._play_timer = QTimer()
        self._play_timer.timeout.connect(self._check_play_done)
        self._play_timer.start(100)

    def _check_play_done(self):
        if not any(s.is_active() for s in self.wasapi_streams):
            self._play_timer.stop()
            for s in self.wasapi_streams:
                s.stop()
            self.wasapi_streams = []
            if self.auto_play:
                QTimer.singleShot(100, self._play_next)
            else:
                self.status_label.setText("播放完成")
                self.status_label.setStyleSheet(f"color: #4a9; background: transparent; font: 10px 'Microsoft YaHei';")

    def _stop(self):
        for s in self.wasapi_streams:
            s.stop()
        self.wasapi_streams = []
        if hasattr(self, '_play_timer'):
            self._play_timer.stop()
        self.status_label.setText("已停止")
        self.status_label.setStyleSheet(f"color: #f90; background: transparent; font: 10px 'Microsoft YaHei';")

    def _play_next(self):
        if not self.auto_play or not self._audio_files:
            return
        cur = self.file_list.currentRow()
        if self.play_mode == "random":
            import random
            nxt = random.randint(0, len(self._audio_files) - 1)
            if len(self._audio_files) > 1:
                while nxt == cur:
                    nxt = random.randint(0, len(self._audio_files) - 1)
        else:
            nxt = (cur + 1) % len(self._audio_files)
        self.file_list.setCurrentRow(nxt)
        self._load_and_play(self._audio_files[nxt])

    def _toggle_auto(self):
        if self.play_mode == "seq":
            self.play_mode = "random"
            self.auto_btn.setText("随机播放")
            self.auto_btn.setStyleSheet(self._btn_style("#9C27B0", "white"))
        else:
            self.play_mode = "seq"
            self.auto_btn.setText("顺序播放")
            self.auto_btn.setStyleSheet(self._btn_style(ACCENT, "white"))

    def _get_dev_idx(self, combo, devs):
        idx = combo.currentIndex()
        if 0 <= idx < len(devs):
            return devs[idx]["index"]
        return None

    def _start_capture(self):
        cap_idx = self._get_dev_idx(self.cap_combo, self._loopback_devices)
        mic_idx = self._get_dev_idx(self.mic_combo, self.mic_devs)
        spk_idx = self._get_dev_idx(self.spk_combo, self.spk_devs)

        if cap_idx is None or mic_idx is None:
            return

        # 检测回音
        cap_name = self._loopback_devices[self.cap_combo.currentIndex()]["name"]
        mon_idx = spk_idx
        if spk_idx is not None:
            spk_name = self.spk_devs[self.spk_combo.currentIndex()]["name"]
            import re
            def _base(n):
                return re.sub(r'\[.*?\]|立体声混音|Loopback', '', n).lower().strip()
            if _base(cap_name) == _base(spk_name):
                mon_idx = None

        self.capture = WASAPICapture(cap_idx, mic_idx, mon_idx)
        self.capture.start(volume=self.cap_vol_slider.value() / 100)
        self.cap_start_btn.setEnabled(False)
        self.cap_stop_btn.setEnabled(True)
        self.cap_status.setText("截取中...")
        self.cap_status.setStyleSheet(f"color: #4a9; background: transparent; font: 10px 'Microsoft YaHei';")

        self._cap_timer = QTimer()
        self._cap_timer.timeout.connect(self._update_cap_vol)
        self._cap_timer.start(200)

    def _update_cap_vol(self):
        if self.capture and self.capture.is_active():
            self.capture.set_volume(self.cap_vol_slider.value() / 100)
        else:
            self._cap_timer.stop()

    def _stop_capture(self):
        if self.capture:
            self.capture.stop()
            self.capture = None
        self.cap_start_btn.setEnabled(True)
        self.cap_stop_btn.setEnabled(False)
        self.cap_status.setText("已停止")
        self.cap_status.setStyleSheet(f"color: #f90; background: transparent; font: 10px 'Microsoft YaHei';")
        if hasattr(self, '_cap_timer'):
            self._cap_timer.stop()

    # === 拖拽支持 ===
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        if not paths:
            return
        if os.path.isdir(paths[0]):
            self._load_folder(paths[0])
        else:
            self._load_folder(os.path.dirname(paths[0]))

    # === 窗口拖动 ===
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def closeEvent(self, event):
        self._stop()
        self._stop_capture()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
