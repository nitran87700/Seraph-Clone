# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Seraph Clone's unified desktop app.
# Run on the target OS (this same spec works for both, since sys.platform
# is evaluated at build time by whichever machine runs pyinstaller):
#
#   pyinstaller --noconfirm seraph.spec
#
# Windows  -> dist/SeraphClone.exe        (single-file, windowed, tray icon)
# macOS    -> dist/SeraphClone.app        (bundle; CI wraps this in a .dmg)

import sys

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('dashboard.html', '.'),
        ('assets/icon.png', 'assets'),
    ],
    hiddenimports=['pystray._base', 'PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == 'darwin':
    # macOS: build a windowed .app bundle (no console window), icon.icns is
    # produced by the CI step just before this spec runs (see workflow).
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='SeraphClone',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon='assets/icon.icns',
    )
    app = BUNDLE(
        exe,
        name='SeraphClone.app',
        icon='assets/icon.icns',
        bundle_identifier='com.seraphclone.app',
        info_plist={
            'CFBundleName': 'Seraph Clone',
            'CFBundleDisplayName': 'Seraph Clone',
            'CFBundleShortVersionString': '0.1.0',
            'LSUIElement': True,  # tray-only app, no Dock icon needed
            'NSHumanReadableCopyright': 'Demo project - not affiliated with Seraph Secure',
        },
    )
else:
    # Windows (and Linux, for local dev testing): single-file onefile exe.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='SeraphClone',
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
        icon='assets/icon.ico' if sys.platform == 'win32' else None,
    )
