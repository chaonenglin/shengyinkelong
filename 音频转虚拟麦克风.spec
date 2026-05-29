# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['mp3_to_mic.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ffmpeg.exe', '.'),
        ('图标', '图标'),
    ],
    hiddenimports=['pyaudiowpatch', 'pydub', 'sounddevice', 'numpy', 'pycaw', 'comtypes', 'PIL', 'PyQt6'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='音频转虚拟麦克风',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['图标\\图标.ico'],
)
