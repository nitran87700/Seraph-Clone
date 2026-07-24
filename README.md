# NR Secure

A lightweight scam-protection app for Windows and macOS: it watches for remote-access
tools scammers commonly abuse (AnyDesk, TeamViewer, and similar), shows only what's
currently connected (no stale history), lets you kill flagged processes with one
click, and alerts a trusted "Guardian" contact (email or Slack/Discord webhook) once
risk crosses a threshold. It runs continuously in the background, pops up a native
notification the moment a new connection is detected, and starts automatically at
login once installed.

This is a functional prototype, not a hardened production security product — the
detection rules are a small sample set meant to prove the architecture, not
exhaustive real-world coverage.

## What's in this repo

```
main.py                 Flask risk/event server + remote-access monitor + native app window (pywebview)
guardian.py             Guardian alerting (email / Slack / Discord webhook)
autostart.py            Registers the app to launch at login (macOS LaunchAgent / Windows Registry Run key)
dashboard.html           Live dashboard UI (risk score, event log, Kill / Kill All, Guardian + App settings)
nrsecure.spec            PyInstaller build spec (produces the Windows .exe / macOS .app)
assets/                  App icons (.ico for Windows, iconset for macOS)
requirements.txt         Python dependencies
.github/workflows/build.yml   CI that builds the real .exe and .dmg on GitHub's own Windows/Mac runners
PACKAGING.md             How to get the built .exe/.dmg (push → GitHub Actions builds them for free)
```

All of it runs as a single combined process — no separate server/agent to start
manually.

## Getting the built app

See [`PACKAGING.md`](PACKAGING.md) — push this repo to GitHub and Actions builds
a Windows `.exe` and macOS `.dmg` automatically, no Windows or Mac machine of your
own required.

## Running from source (for development)

```bash
pip install -r requirements.txt --break-system-packages
python3 main.py
```
Opens the dashboard in a native app window (falls back to opening it in your
default browser if no native webview backend is available in your environment,
e.g. a minimal Linux setup without GTK/Qt installed).

## How it works

- **Remote-access monitor**: polls running processes every 5 seconds for known
  remote-access tool names. A first detection is medium severity and fires a
  native OS notification immediately; if the tool is still running 20+ seconds
  later it escalates to high severity (an active session, not just a quick
  launch).
- **Live risk score, not history**: the dashboard's "Active connections" table
  only ever shows what's *currently* running — nothing lingers after it closes
  or gets killed. The risk score is computed directly from that live list, so
  killing every active connection drops it straight to 0 ("system secure")
  instead of fading out over time.
- **Kill / Kill All**: each active connection has its own "Kill" button, plus a
  "Kill All Detected" button that live-scans and terminates every currently
  running flagged tool in one go.
- **Guardian alerts**: configure an email (via SMTP) and/or a webhook URL on the
  dashboard. Fires when the live risk score crosses 60/100 (5-minute cooldown so
  a burst of activity doesn't spam them). If neither channel is configured,
  alerts still get logged locally (`~/.nrsecure/guardian_alerts.log`) so the flow
  is visible even without setup.
- **Runs in the background**: closing the app window hides it rather than
  quitting — the server and monitor keep running. Reopening the app (e.g.
  double-clicking it again) detects it's already running and just brings the
  window back instead of starting a duplicate. Use "Quit NR Secure" in the
  dashboard's App settings to fully exit.
- **Auto-start**: on first launch of the installed app, it registers itself to
  start at login (macOS LaunchAgent / Windows Registry Run key) — no manual setup
  needed after installing. Toggle it off anytime from the dashboard's "App
  settings" card.
- **Native app window**: the dashboard renders in a real OS window via
  `pywebview` (WebView2 on Windows, WKWebView on macOS) instead of a browser
  tab — there's no separate tray/menu-bar icon, since a tray icon and a native
  window can't reliably share the main thread on macOS.

Runtime data (Guardian settings, the single-instance lock file) lives in
`~/.nrsecure/`.

## What's simplified vs. a hardened product

- Remote-access detection matches on process *names*, not behavior — a real
  system would also analyze session/connection patterns, not just whether a
  process is running (so legitimate IT use of the same tools will also get
  flagged).
- No authentication/accounts — single-user, local-only.
- Unsigned builds — Windows SmartScreen and macOS Gatekeeper show a one-time
  warning until you add paid code-signing certificates (see `PACKAGING.md`).

## Extending it

Natural next steps: paid code-signing/notarization to remove the OS warnings,
account/multi-device support, a mobile companion app for Guardian notifications,
and richer remote-access heuristics (session pattern analysis instead of just
process names).
