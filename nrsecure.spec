# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for NR Secure's unified desktop app.
# Run on the target OS (this same spec works for both, since sys.platform
# is evaluated at build time by whichever machine runs pyinstaller):
#
#   pyinstaller --noconfirm nrsecure.spec
#
# Windows  -> dist/NRSecure.exe        (single-file, windowed, native app window)
# macOS    -> dist/NRSecure.app        (bundle; CI wraps this in a .dmg)

import sys

block_cipher = None

# pywebview picks its backend dynamically at import time based on the OS,
# which PyInstaller's static analysis can miss - list the relevant one
# explicitly for whichever platform this spec is being run on.
if sys.platform == 'darwin':
    webview_hidden_imports = ['webview.platforms.cocoa']
elif sys.platform == 'win32':
    webview_hidden_imports = ['webview.platforms.edgechromium', 'webview.platforms.winforms', 'clr']
else:
    webview_hidden_imports = ['webview.platforms.gtk']

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('dashboard.html', '.'),
    ],
    hiddenimports=webview_hidden_imports,
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
        name='NRSecure',
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
        name='NRSecure.app',
        icon='assets/icon.icns',
        bundle_identifier='com.nrsecure.app',
        info_plist={
            'CFBundleName': 'NR Secure',
            'CFBundleDisplayName': 'NR Secure',
            'CFBundleShortVersionString': '0.1.0',
            # No LSUIElement here (unlike the old tray-only build) - this is
            # now a normal windowed app, so it should show a Dock icon and
            # work with Cmd+Tab like any other application.
            'NSHumanReadableCopyright': 'NR Secure',
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
        name='NRSecure',
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
