"""
Seraph Clone - Unified Desktop App
===================================
Packaged entry point that combines:
  - the risk/event server + dashboard (from server/app.py)
  - the remote-access-tool monitor (from desktop-agent/monitor.py)
  - a system tray icon so it runs quietly in the background

into a single process, so it can be frozen into one Windows .exe / macOS .app
with PyInstaller. The browser extension is unaffected and still installs
separately into Chrome (extensions can't be bundled into a native binary).

Runtime data (events, guardian config/log) is written to a per-user folder
(~/.seraph-clone) rather than next to the executable, since the executable's
own folder is read-only once frozen/signed.
"""
import json
import os
import sys
import time
import threading
import webbrowser
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory

import psutil
import requests

import guardian


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
    base = os.path.join(os.path.expanduser("~"), ".seraph-clone")
    os.makedirs(base, exist_ok=True)
    return base


DATA_FILE = os.path.join(data_dir(), "events.json")
LOCK = threading.Lock()

# Point the guardian module at the same writable data directory.
guardian.CONFIG_FILE = os.path.join(data_dir(), "guardian_config.json")
guardian.LOG_FILE = os.path.join(data_dir(), "guardian_alerts.log")


# ---------------------------------------------------------------------------
# Risk engine (same model as server/app.py)
# ---------------------------------------------------------------------------
EVENT_WEIGHTS = {
    "scam_site_blocked": 35,
    "phishing_heuristic_match": 25,
    "remote_access_tool_detected": 40,
    "remote_access_tool_new_connection": 55,
}
GUARDIAN_ALERT_THRESHOLD = 60
RISK_WINDOW_SECONDS = 30 * 60


def _load_events():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save_events(events):
    with open(DATA_FILE, "w") as f:
        json.dump(events, f, indent=2)


def _current_risk_score(events):
    now = time.time()
    score = 0
    for e in events:
        age = now - e["timestamp"]
        if age > RISK_WINDOW_SECONDS:
            continue
        weight = EVENT_WEIGHTS.get(e["type"], 10)
        decay = max(0.0, 1 - (age / RISK_WINDOW_SECONDS))
        score += weight * decay
    return round(min(score, 100), 1)


def record_event(event_type, source, detail, severity="medium"):
    """Shared path used both by the /events HTTP route (browser extension)
    and the in-process remote-access monitor thread (no HTTP round trip
    needed since they now live in the same process)."""
    if event_type not in EVENT_WEIGHTS:
        raise ValueError(f"unknown event type '{event_type}'")

    event = {
        "type": event_type,
        "source": source,
        "detail": detail,
        "severity": severity,
        "timestamp": time.time(),
        "time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    with LOCK:
        events = _load_events()
        events.append(event)
        events = events[-500:]
        _save_events(events)
        score = _current_risk_score(events)

    alert_sent = False
    if score >= GUARDIAN_ALERT_THRESHOLD:
        alert_sent = guardian.send_guardian_alert(
            reason=f"Risk score reached {score}/100 after event: {event['type']} ({event['detail']})",
            score=score,
        )

    return event, score, alert_sent


# ---------------------------------------------------------------------------
# Flask app (server + dashboard)
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/events", methods=["POST"])
def http_receive_event():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        event, score, alert_sent = record_event(
            event_type=payload.get("type"),
            source=payload.get("source", "unknown"),
            detail=payload.get("detail", ""),
            severity=payload.get("severity", "medium"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"received": event, "risk_score": score, "guardian_alert_sent": alert_sent})


@app.route("/events", methods=["GET"])
def list_events():
    with LOCK:
        events = _load_events()
        score = _current_risk_score(events)
    return jsonify({"events": list(reversed(events)), "risk_score": score})


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


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return send_from_directory(resource_path("."), "dashboard.html")


# ---------------------------------------------------------------------------
# Remote-access-tool monitor (same detection logic as desktop-agent/monitor.py)
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS = 5
ESCALATE_AFTER_SECONDS = 20

KNOWN_REMOTE_ACCESS_TOOLS = [
    "anydesk", "teamviewer", "ultraviewer", "logmein", "screenconnect",
    "connectwise", "gotoassist", "splashtop", "remotepc", "supremo",
    "aeroadmin", "showmypc",
]

_tracked = {}


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


def monitor_loop():
    while True:
        try:
            found = _scan_processes()
            now = time.time()

            for key, info in found.items():
                if key not in _tracked:
                    _tracked[key] = {"first_seen": now, "escalated": False}
                    record_event(
                        "remote_access_tool_detected", "desktop_agent",
                        f"{info['tool']} (pid {info['pid']}, process '{info['process_name']}')",
                        "medium",
                    )
                else:
                    track = _tracked[key]
                    running_for = now - track["first_seen"]
                    if not track["escalated"] and running_for >= ESCALATE_AFTER_SECONDS:
                        track["escalated"] = True
                        record_event(
                            "remote_access_tool_new_connection", "desktop_agent",
                            f"{info['tool']} still active after {int(running_for)}s (pid {info['pid']})",
                            "high",
                        )

            for key in list(_tracked.keys()):
                if key not in found:
                    del _tracked[key]
        except Exception as e:
            print(f"[monitor_loop] error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# System tray icon
# ---------------------------------------------------------------------------
def run_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def _headless_fallback(reason):
    # Covers any environment where a tray icon can't be created - missing
    # library, no display/desktop session, unsupported backend, etc. The
    # server and monitor threads are already running, so the app still
    # works; the user just reaches it via the browser instead of the tray.
    print(f"Tray icon unavailable ({reason}); running headless.")
    print("Dashboard: http://localhost:5000/dashboard")
    while True:
        time.sleep(3600)


def run_tray():
    try:
        import pystray
        from PIL import Image
    except Exception as e:
        # pystray raises whatever error its chosen backend raises at import
        # time (ImportError, ValueError for a missing Gtk/AppIndicator
        # namespace on Linux, etc.) - treat any of them as "no tray here".
        _headless_fallback(str(e))
        return

    def open_dashboard(icon=None, item=None):
        webbrowser.open("http://localhost:5000/dashboard")

    def quit_app(icon=None, item=None):
        icon.stop()
        os._exit(0)

    try:
        image = Image.open(resource_path(os.path.join("assets", "icon.png")))
        menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", open_dashboard, default=True),
            pystray.MenuItem("Quit Seraph Clone", quit_app),
        )
        icon = pystray.Icon("SeraphClone", image, "Seraph Clone - protection active", menu)
        icon.run()
    except Exception as e:
        # e.g. no Gtk/AppIndicator on this Linux session, no display, etc.
        # Real Windows/macOS builds use pystray's native win32/Cocoa backends
        # and won't hit this, but we never want a tray failure to take down
        # the server + monitor that are already running.
        _headless_fallback(str(e))


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    time.sleep(1.2)
    webbrowser.open("http://localhost:5000/dashboard")
    run_tray()
