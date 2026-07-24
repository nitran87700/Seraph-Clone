"""
NR Secure - Unified Desktop App
===================================
Packaged entry point that combines:
  - the risk/event server + dashboard
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
can't reliably coexist in one process. Closing the window quits the app.

Runtime data (events, guardian config/log) is written to a per-user folder
(~/.nrsecure) rather than next to the executable, since the executable's
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


DATA_FILE = os.path.join(data_dir(), "events.json")
LOCK = threading.Lock()

# Point the guardian module at the same writable data directory.
guardian.CONFIG_FILE = os.path.join(data_dir(), "guardian_config.json")
guardian.LOG_FILE = os.path.join(data_dir(), "guardian_alerts.log")


# ---------------------------------------------------------------------------
# Risk engine
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


def record_event(event_type, source, detail, severity="medium", pid=None):
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
        "pid": pid,  # present for remote_access_tool_* events; lets the dashboard offer Kill/Kill All
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
            pid=payload.get("pid"),
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


def _terminate_pid(pid):
    """Shared termination logic used by both /processes/kill and
    /processes/kill_all. Tries a graceful terminate() first, escalates to
    kill() if the process is still alive after a few seconds.
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


@app.route("/processes/kill", methods=["POST"])
def kill_process():
    """Terminate a single flagged remote-access-tool process by pid,
    triggered from the dashboard's "Kill process" button."""
    payload = request.get_json(force=True, silent=True) or {}
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return jsonify({"success": False, "message": "missing or invalid 'pid'"}), 400

    success, message, name = _terminate_pid(pid)
    if success and name:
        record_event(
            "remote_access_tool_detected", "desktop_agent",
            f"Killed {name} (pid {pid}) from dashboard", "medium", pid=pid,
        )
    return jsonify({"success": success, "message": message}), (200 if success else 403)


@app.route("/processes/kill_all", methods=["POST"])
def kill_all_processes():
    """Live-scans for every currently running flagged remote-access tool
    (not just ones already in the event log, in case one just appeared) and
    terminates all of them in one go - triggered from the dashboard's
    "Kill All Detected" button."""
    found = _scan_processes()
    killed = []
    failed = []
    for info in found.values():
        pid = info["pid"]
        success, message, name = _terminate_pid(pid)
        if success and name:
            killed.append(f"{name} (pid {pid})")
        elif not success:
            failed.append(f"pid {pid}: {message}")

    if killed:
        record_event(
            "remote_access_tool_detected", "desktop_agent",
            f"Killed all flagged tools from dashboard: {', '.join(killed)}", "medium",
        )

    return jsonify({
        "success": True,
        "killed": killed,
        "failed": failed,
        "message": (
            f"Killed {len(killed)} process(es)." if killed else "No flagged tools were currently running."
        ) + (f" {len(failed)} failed." if failed else ""),
    })


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return send_from_directory(resource_path("."), "dashboard.html")


# ---------------------------------------------------------------------------
# Remote-access-tool monitor
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
                        "medium", pid=info["pid"],
                    )
                else:
                    track = _tracked[key]
                    running_for = now - track["first_seen"]
                    if not track["escalated"] and running_for >= ESCALATE_AFTER_SECONDS:
                        track["escalated"] = True
                        record_event(
                            "remote_access_tool_new_connection", "desktop_agent",
                            f"{info['tool']} still active after {int(running_for)}s (pid {info['pid']})",
                            "high", pid=info["pid"],
                        )

            for key in list(_tracked.keys()):
                if key not in found:
                    del _tracked[key]
        except Exception as e:
            print(f"[monitor_loop] error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Native app window
# ---------------------------------------------------------------------------
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
    macOS, and the simplest cross-platform-safe choice overall. Closing the
    window ends this call, which ends the process (the Flask/monitor
    threads are daemons)."""
    try:
        import webview
    except Exception as e:
        _headless_fallback(str(e))
        return

    try:
        webview.create_window(
            "NR Secure",
            "http://localhost:5000/dashboard",
            width=1040,
            height=720,
            min_size=(720, 480),
        )
        webview.start()
    except Exception as e:
        # e.g. no WebView2 runtime on an old Windows install, no WebKit
        # available, no display on a headless Linux session, etc.
        _headless_fallback(str(e))


if __name__ == "__main__":
    # Best-effort: register to launch at login automatically. Only actually
    # does anything when running as a frozen/installed app (no-op in dev).
    autostart.enable_autostart()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    time.sleep(1.2)
    run_window()
