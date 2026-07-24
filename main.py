"""
NR Secure - Unified Desktop App
===================================
Packaged entry point that combines:
  - the risk/status server + dashboard
  - the remote-access-tool monitor
  - a native app window showing the dashboard (not a browser tab)
  - auto-start registration (runs at login once installed, no manual launch needed)

into a single process, so it can be frozen into one Windows .exe / macOS .app
with PyInstaller. The browser extension (if used) is unaffected and still
installs separately into Chrome (extensions can't be bundled into a native
binary).

The dashboard is shown via pywebview, which embeds the OS's native web
renderer (WebView2 on Windows, WKWebView on macOS) in a real window - no
separate system tray icon, since a tray icon (pystray) and a native window
(pywebview) both need exclusive control of the main thread on macOS and
can't reliably coexist in one process.

The app is designed to run continuously in the background: closing the
window hides it rather than quitting (the server + monitor keep running),
and relaunching the app while it's already running just brings the window
back instead of starting a second copy. A "Quit NR Secure" button in the
dashboard's settings fully exits it. A native OS notification fires whenever
a new remote-access connection is detected.

Runtime data (guardian config/log, single-instance lock) is written to a
per-user folder (~/.nrsecure) rather than next to the executable, since the
executable's own folder is read-only once frozen/signed.
"""
import os
import sys
import time
import threading
import webbrowser

from flask import Flask, request, jsonify, send_from_directory

import psutil
import requests

import guardian
import autostart


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def resource_path(relative_path):
    """Location of bundled read-only assets (dashboard.html, icons) - works
    both when run as a plain script and when frozen by PyInstaller."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def data_dir():
    """Writable per-user location for runtime data."""
    base = os.path.join(os.path.expanduser("~"), ".nrsecure")
    os.makedirs(base, exist_ok=True)
    return base


LOCK_FILE = os.path.join(data_dir(), "app.lock")

# Point the guardian module at the same writable data directory.
guardian.CONFIG_FILE = os.path.join(data_dir(), "guardian_config.json")
guardian.LOG_FILE = os.path.join(data_dir(), "guardian_alerts.log")


# ---------------------------------------------------------------------------
# Live connection tracking + risk score
# ---------------------------------------------------------------------------
# Unlike a history log, this only ever reflects what's running *right now*.
# As soon as a flagged tool is no longer running (closed by the user, or
# killed from the dashboard), it disappears from here on the very next scan
# (or immediately, for kills triggered through the dashboard) - so the risk
# score drops back down without waiting for anything to "decay".
POLL_INTERVAL_SECONDS = 5
ESCALATE_AFTER_SECONDS = 20
GUARDIAN_ALERT_THRESHOLD = 60
SEVERITY_WEIGHT = {"medium": 40, "high": 55}

KNOWN_REMOTE_ACCESS_TOOLS = [
    "anydesk", "teamviewer", "ultraviewer", "logmein", "screenconnect",
    "connectwise", "gotoassist", "splashtop", "remotepc", "supremo",
    "aeroadmin", "showmypc",
]

_tracked = {}  # key -> {"tool", "pid", "process_name", "first_seen"}
_tracked_lock = threading.Lock()


def _scan_processes():
    found = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for tool in KNOWN_REMOTE_ACCESS_TOOLS:
            if tool in name:
                found[f"{tool}:{proc.info['pid']}"] = {
                    "tool": tool, "pid": proc.info["pid"], "process_name": name,
                }
    return found


def _active_connections_locked():
    """Must be called while holding _tracked_lock. Returns the current list
    of active connections, each with a severity derived live from how long
    it's been running (not stored - recomputed every call)."""
    now = time.time()
    items = []
    for info in _tracked.values():
        duration = now - info["first_seen"]
        severity = "high" if duration >= ESCALATE_AFTER_SECONDS else "medium"
        items.append({
            "pid": info["pid"],
            "tool": info["tool"],
            "process_name": info["process_name"],
            "duration_seconds": int(duration),
            "severity": severity,
        })
    items.sort(key=lambda c: c["duration_seconds"], reverse=True)
    return items


def get_status():
    with _tracked_lock:
        connections = _active_connections_locked()
    score = min(100, sum(SEVERITY_WEIGHT[c["severity"]] for c in connections))
    return connections, score


def _notify(title, message):
    """Best-effort OS notification. Never raises - a missing/broken
    notification backend should never take down monitoring."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="NR Secure", timeout=8)
    except Exception as e:
        print(f"[notify] could not show notification: {e}", file=sys.stderr)


def monitor_loop():
    while True:
        try:
            found = _scan_processes()
            now = time.time()

            with _tracked_lock:
                for key, info in found.items():
                    if key not in _tracked:
                        _tracked[key] = {
                            "tool": info["tool"], "pid": info["pid"],
                            "process_name": info["process_name"], "first_seen": now,
                        }
                        _notify(
                            "NR Secure - New connection detected",
                            f"{info['tool']} ({info['process_name']}, pid {info['pid']}) just started running.",
                        )
                for key in list(_tracked.keys()):
                    if key not in found:
                        del _tracked[key]
                connections = _active_connections_locked()

            score = min(100, sum(SEVERITY_WEIGHT[c["severity"]] for c in connections))
            if score >= GUARDIAN_ALERT_THRESHOLD:
                names = ", ".join(f"{c['tool']} (pid {c['pid']})" for c in connections)
                guardian.send_guardian_alert(
                    reason=f"{len(connections)} active remote-access connection(s): {names}",
                    score=score,
                )
        except Exception as e:
            print(f"[monitor_loop] error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)


def _terminate_pid(pid):
    """Tries a graceful terminate() first, escalates to kill() if the
    process is still alive after a few seconds.
    Returns (success: bool, message: str, name: str|None)."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        return True, f"Terminated {name} (pid {pid})", name
    except psutil.NoSuchProcess:
        return True, "Process was already gone", None
    except psutil.AccessDenied:
        return False, "Permission denied - needs admin/sudo privileges to terminate", None
    except Exception as e:
        return False, str(e), None


def _untrack_pid(pid):
    """Immediately removes a pid from the live-tracked set, so the very next
    /status call already reflects it as gone instead of waiting up to
    POLL_INTERVAL_SECONDS for the background scan to notice."""
    with _tracked_lock:
        for key in list(_tracked.keys()):
            if _tracked[key]["pid"] == pid:
                del _tracked[key]


# ---------------------------------------------------------------------------
# Flask app (server + dashboard)
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/status", methods=["GET"])
def status():
    connections, score = get_status()
    return jsonify({
        "active_connections": connections,
        "risk_score": score,
        "secure": score == 0,
    })


@app.route("/events", methods=["POST"])
def http_receive_event():
    """Kept for the browser extension: a blocked scam site is a one-off,
    already-mitigated event (not an ongoing "connection"), so it doesn't
    feed into the live active-connections risk score - it just notifies the
    Guardian directly."""
    payload = request.get_json(force=True, silent=True) or {}
    event_type = payload.get("type")
    detail = payload.get("detail", "")
    if event_type not in ("scam_site_blocked", "phishing_heuristic_match"):
        return jsonify({"error": f"unknown or unsupported event type '{event_type}'"}), 400

    _notify("NR Secure - Scam site blocked", detail or event_type)
    alert_sent = guardian.send_guardian_alert(reason=f"{event_type.replace('_', ' ')}: {detail}", score=100)
    return jsonify({"received": True, "guardian_alert_sent": alert_sent})


@app.route("/guardian/config", methods=["GET"])
def guardian_config_get():
    cfg = guardian.get_guardian_config()
    safe_cfg = {k: v for k, v in cfg.items() if "password" not in k.lower()}
    return jsonify(safe_cfg)


@app.route("/guardian/config", methods=["POST"])
def guardian_config_set():
    payload = request.get_json(force=True, silent=True) or {}
    guardian.set_guardian_config(payload)
    return jsonify({"status": "saved"})


@app.route("/guardian/test", methods=["POST"])
def guardian_test():
    sent = guardian.send_guardian_alert(reason="Test alert triggered manually from dashboard.", score=100)
    return jsonify({"sent": sent})


@app.route("/settings/autostart", methods=["GET"])
def autostart_get():
    return jsonify({
        "enabled": autostart.is_autostart_enabled(),
        "supported": autostart.is_frozen(),
    })


@app.route("/settings/autostart", methods=["POST"])
def autostart_set():
    payload = request.get_json(force=True, silent=True) or {}
    want_enabled = bool(payload.get("enabled"))
    if want_enabled:
        ok = autostart.enable_autostart()
    else:
        autostart.disable_autostart()
        ok = True
    return jsonify({"enabled": autostart.is_autostart_enabled(), "success": ok})


@app.route("/processes/kill", methods=["POST"])
def kill_process():
    """Terminate a single active connection by pid, triggered from the
    dashboard's "Kill" button."""
    payload = request.get_json(force=True, silent=True) or {}
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return jsonify({"success": False, "message": "missing or invalid 'pid'"}), 400

    success, message, name = _terminate_pid(pid)
    if success:
        _untrack_pid(pid)
    return jsonify({"success": success, "message": message}), (200 if success else 403)


@app.route("/processes/kill_all", methods=["POST"])
def kill_all_processes():
    """Live-scans for every currently running flagged remote-access tool and
    terminates all of them in one go - triggered from the dashboard's
    "Kill All Detected" button. Only untracks pids that were actually
    killed - if one fails (e.g. permission denied), it stays listed as
    active, since the system genuinely isn't secure from that one yet."""
    found = _scan_processes()
    killed = []
    failed = []
    for info in found.values():
        pid = info["pid"]
        success, message, name = _terminate_pid(pid)
        if success and name:
            killed.append(f"{name} (pid {pid})")
            _untrack_pid(pid)
        elif not success:
            failed.append(f"pid {pid}: {message}")

    return jsonify({
        "success": True,
        "killed": killed,
        "failed": failed,
        "message": (
            f"Killed {len(killed)} process(es)." if killed else "No active connections were running."
        ) + (f" {len(failed)} failed." if failed else ""),
    })


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return send_from_directory(resource_path("."), "dashboard.html")


@app.route("/app/show", methods=["POST"])
def app_show():
    """Called either by the dashboard, or by a second launch of the app
    detecting this instance is already running (see main_entrypoint()) -
    brings the (possibly hidden) window back to the front."""
    if _window is not None:
        try:
            _window.show()
            _window.restore()
        except Exception as e:
            print(f"[app_show] could not show window: {e}", file=sys.stderr)
    return jsonify({"success": True})


@app.route("/app/quit", methods=["POST"])
def app_quit():
    """Fully exits the app (unlike closing the window, which just hides
    it) - triggered from the "Quit NR Secure" button in dashboard settings."""
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass
    threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0))).start()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Native app window
# ---------------------------------------------------------------------------
_window = None


def run_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def _headless_fallback(reason):
    # Covers any environment where a native window can't be created - missing
    # webview backend, no display/desktop session, etc. The server and
    # monitor threads are already running, so the app still works; the user
    # just reaches it via a regular browser tab instead of an app window.
    print(f"App window unavailable ({reason}); running headless.", flush=True)
    print("Dashboard: http://localhost:5000/dashboard", flush=True)
    webbrowser.open("http://localhost:5000/dashboard")
    while True:
        time.sleep(3600)


def run_window():
    """Opens the dashboard in a real OS window (WebView2 on Windows,
    WKWebView on macOS) via pywebview, on the main thread - required on
    macOS, and the simplest cross-platform-safe choice overall.

    Closing the window hides it instead of quitting (see on_closing below),
    so the server + monitor keep running in the background. The app only
    fully exits via /app/quit."""
    global _window
    try:
        import webview
    except Exception as e:
        _headless_fallback(str(e))
        return

    def on_closing():
        _window.hide()
        return False  # cancels the actual close/destroy

    try:
        _window = webview.create_window(
            "NR Secure",
            "http://localhost:5000/dashboard",
            width=1040,
            height=720,
            min_size=(720, 480),
        )
        _window.events.closing += on_closing
        webview.start()
    except Exception as e:
        # e.g. no WebView2 runtime on an old Windows install, no WebKit
        # available, no display on a headless Linux session, etc.
        _headless_fallback(str(e))


# ---------------------------------------------------------------------------
# Single-instance handling
# ---------------------------------------------------------------------------
def _already_running_pid():
    """Returns the pid recorded in the lock file if that process is still
    alive and looks like our own app, else None (stale/missing lock)."""
    if not os.path.exists(LOCK_FILE):
        return None
    try:
        with open(LOCK_FILE, "r") as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        return None

    if not psutil.pid_exists(pid):
        return None
    try:
        name = psutil.Process(pid).name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    if "nrsecure" not in name and "python" not in name:
        # pid was reused by an unrelated process since the lock was written
        return None
    return pid


def _write_lock_file():
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def main_entrypoint():
    existing_pid = _already_running_pid()
    if existing_pid is not None:
        print(f"NR Secure is already running (pid {existing_pid}) - showing its window instead.", flush=True)
        try:
            requests.post("http://localhost:5000/app/show", timeout=3)
        except requests.exceptions.RequestException:
            pass
        return

    _write_lock_file()

    # Best-effort: register to launch at login automatically. Only actually
    # does anything when running as a frozen/installed app (no-op in dev).
    autostart.enable_autostart()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    time.sleep(1.2)
    run_window()


if __name__ == "__main__":
    main_entrypoint()
