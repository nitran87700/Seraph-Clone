# Seraph Clone — Scam Protection MVP

A working prototype inspired by [Seraph Secure](https://www.seraphsecure.com), covering its three core ideas:

1. **Scam site blocking** — a browser extension checks every page you visit against a known-scam list plus lightweight heuristics (suspicious keywords, brand typosquatting), and blocks matches.
2. **Remote-access tool detection** — a desktop agent watches for processes like AnyDesk, TeamViewer, and other tools scammers commonly abuse to take control of a victim's computer.
3. **Guardian alerts** — a trusted contact (email or Slack/Discord webhook) gets notified automatically once your risk score crosses a threshold.

Everything reports into one **central risk engine** (`server/`) that scores recent activity and decides when to alert your Guardian. A live dashboard shows the event feed and current risk score.

This is a functional demo, not a production security product — the blocklist and detection rules are small samples meant to prove the architecture, not real-world coverage.

**Want a Windows `.exe` / macOS `.dmg` instead of running Python scripts?** See
[`desktop-app/PACKAGING.md`](desktop-app/PACKAGING.md) — push this repo to
GitHub and a workflow builds both automatically on Microsoft's/Apple's own
cloud machines (no Windows or Mac of your own needed).

## Architecture

```
extension/         Chrome extension (Manifest V3) — blocks scam sites in the browser
desktop-agent/      Standalone Python script version of the remote-access monitor (for dev/manual runs)
server/             Standalone Flask app version of the risk server (for dev/manual runs)
desktop-app/        Packaged version — server + monitor + tray icon combined into one app,
                    built into a Windows .exe and macOS .dmg via GitHub Actions (see desktop-app/PACKAGING.md)
simulate_scam.py    Fires a fake attack sequence at the server so you can see it work without installing anything
```

**Two ways to run the server + monitor side:** the plain `server/` + `desktop-agent/`
scripts (run with `python3`, good for local dev/testing — used below), or the
packaged `desktop-app/` (same logic, combined into one binary with a tray icon —
see `desktop-app/PACKAGING.md` for how to get a real `.exe`/`.dmg` built via CI).

Data flow: extension/agent → POST `/events` → risk score updated → if score ≥ 60, Guardian alert fires (email and/or webhook) → dashboard shows it all live.

## Running it

**1. Start the server (required first — everything else reports to it):**
```bash
cd server
pip install -r requirements.txt --break-system-packages
python3 app.py
```
Open **http://localhost:5000/dashboard** — set your Guardian's email/webhook there.

**2. Try it instantly with the simulator (no browser or install needed):**
```bash
python3 simulate_scam.py
```
Watch the dashboard risk score climb and a Guardian alert fire.

**3. Load the real browser extension:**
- Chrome → `chrome://extensions` → enable Developer Mode → "Load unpacked" → select the `extension/` folder.
- Visit any URL containing one of the demo scam domains in `extension/blocklist.json` (e.g. `http://irs-refund-verify.org`) to see it get blocked and reported.

**4. Run the real desktop agent:**
```bash
cd desktop-agent
pip install -r requirements.txt --break-system-packages
python3 monitor.py
```
It polls running processes every 5 seconds for known remote-access tools. If you have TeamViewer/AnyDesk installed, opening them will trigger a detection; if one stays open past 20 seconds it escalates to "active session" severity.

## Guardian alerts

Configure on the dashboard (or via `POST /guardian/config`):
- `guardian_email` + SMTP settings → sends real email
- `guardian_webhook_url` → posts to a Slack/Discord incoming webhook
- If neither is configured, alerts are still recorded to `server/guardian_alerts.log` so the flow is visible in the demo.

There's a 5-minute cooldown between alerts so a burst of events doesn't spam the Guardian.

## What's simplified vs. a real product

- Blocklist and heuristics are tiny samples — a real version would sync a hosted, continuously updated feed (Seraph's is 250k+ domains, refreshed daily).
- Typosquat detection is a basic edit-distance check on the domain label, not the more sophisticated matching a production system would use.
- No authentication/accounts — this is single-user, local-only.
- Desktop agent flags known remote-access *tool names*, not behavior — a real system would also look at session initiation patterns, not just whether the process is running.

## Extending it

Natural next steps: add real-time blocklist sync from a hosted feed, add account/multi-device support, add a mobile companion app for Guardian notifications, replace the simple keyword/typosquat heuristics with an actual phishing-classification model, and add evidence-gathering/reporting workflows like Seraph's victim-support features.
