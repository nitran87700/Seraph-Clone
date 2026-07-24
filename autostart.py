"""
Auto-start registration for NR Secure.

Goal: once someone installs the app (drags the .app to /Applications, or
just runs the .exe on Windows), it should start automatically every time
they log in, without them needing to remember to open it manually. That's
what makes it usable as something you distribute to other people.

macOS: installs a per-user LaunchAgent under ~/Library/LaunchAgents. This is
       the standard, no-admin-required mechanism for "run this at login" on
       macOS - it's what many menu-bar apps use under the hood.
Windows: adds a value under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
       Also no admin rights required - this is the standard per-user "run at
       startup" mechanism on Windows.

Registration only happens when running as a frozen/installed app (PyInstaller
sets sys.frozen = True). Running `python3 main.py` directly during
development never touches the user's login items.
"""
import os
import sys
import subprocess
import plistlib

APP_NAME = "NRSecure"
BUNDLE_ID = "com.nrsecure.app"


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def _executable_path():
    """Path to the actual installed binary. sys.executable correctly points
    at the real .exe / the binary inside the .app bundle once frozen (not at
    the temporary PyInstaller extraction directory)."""
    return sys.executable


# --------------------------------------------------------------------- macOS
def _launch_agent_path():
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{BUNDLE_ID}.plist")


def enable_autostart_macos():
    plist_path = _launch_agent_path()
    already_existed = os.path.exists(plist_path)

    plist = {
        "Label": BUNDLE_ID,
        "ProgramArguments": [_executable_path()],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
    }
    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    if not already_existed:
        subprocess.run(["launchctl", "load", "-w", plist_path], capture_output=True)


def disable_autostart_macos():
    plist_path = _launch_agent_path()
    if os.path.exists(plist_path):
        subprocess.run(["launchctl", "unload", "-w", plist_path], capture_output=True)
        os.remove(plist_path)


def is_autostart_enabled_macos():
    return os.path.exists(_launch_agent_path())


# ------------------------------------------------------------------- Windows
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def enable_autostart_windows():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _executable_path())


def disable_autostart_windows():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def is_autostart_enabled_windows():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except FileNotFoundError:
        return False


# --------------------------------------------------------------- cross-platform
def enable_autostart():
    """Best-effort - never raises, just returns whether it succeeded."""
    if not is_frozen():
        return False
    try:
        if sys.platform == "darwin":
            enable_autostart_macos()
        elif sys.platform == "win32":
            enable_autostart_windows()
        else:
            return False  # Linux desktop autostart varies too much by DE; skip
        return True
    except Exception as e:
        print(f"[autostart] could not enable: {e}", file=sys.stderr)
        return False


def disable_autostart():
    try:
        if sys.platform == "darwin":
            disable_autostart_macos()
        elif sys.platform == "win32":
            disable_autostart_windows()
    except Exception as e:
        print(f"[autostart] could not disable: {e}", file=sys.stderr)


def is_autostart_enabled():
    try:
        if sys.platform == "darwin":
            return is_autostart_enabled_macos()
        elif sys.platform == "win32":
            return is_autostart_enabled_windows()
    except Exception:
        pass
    return False
