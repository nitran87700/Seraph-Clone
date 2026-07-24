# Packaging NR Secure as a Windows .exe and macOS .dmg

This repo is a single unified app — the risk/event server, the remote-access-tool
monitor, a native app window showing the dashboard, and auto-start-at-login
registration, all combined into one process — built so it can be frozen into
one native binary per OS.

A GitHub Actions workflow (`.github/workflows/build.yml`) builds real `.exe` and
`.dmg` files automatically on Microsoft's and Apple's own cloud machines, for
free, every time you push.

## Getting the built files

1. Push changes to `main` (or trigger manually from the **Actions** tab → "Run
   workflow").
2. Open your repo → **Actions** tab → the latest "Build NR Secure desktop app" run.
3. Once it finishes (a few minutes), scroll to **Artifacts** — download
   `NRSecure-Windows` (contains the `.exe`) and `NRSecure-macOS` (contains the `.dmg`).

**To get them on a proper GitHub Release page instead** (nicer for sharing with
others):
```bash
git tag v0.1.0
git push origin v0.1.0
```
This triggers the same build, then automatically attaches both files to a
Release at `https://github.com/<you>/<repo>/releases`.

## Running the built app

- **Windows**: double-click `NRSecure-Windows.exe`. Since it isn't signed with a
  paid Microsoft code-signing certificate, SmartScreen will show "Windows
  protected your PC" the first time — click **More info → Run anyway**.
- **macOS**: open `NRSecure-macOS.dmg`, drag `NRSecure.app` to Applications.
  Since it isn't signed/notarized with a paid Apple Developer account, Gatekeeper
  will block it on first launch — right-click the app → **Open** → **Open** again
  in the dialog (only needed once).

On first launch, the app:
- registers itself to start automatically at login (a macOS LaunchAgent, or a
  Windows Registry Run key — both are the standard, no-admin-required way apps
  do this on each OS), so people you distribute it to don't need to remember to
  open it themselves after installing;
- opens the dashboard in a real app window (not a browser tab) — it uses the
  OS's built-in web renderer (WebView2 on Windows, WKWebView on macOS), so no
  extra runtime to install. There's no system tray/menu-bar icon — a tray icon
  and a native window can't reliably share the main thread on macOS, so this
  build keeps things simple with just the one window. The "Start at Login"
  toggle lives in the dashboard itself, under "App settings".

**Closing the window doesn't quit the app** — it keeps running in the
background (server + monitor keep going, and you'll get a notification if a
new remote-access connection shows up). Opening the app again (double-click it
a second time) detects it's already running and just brings the window back.
Use "Quit NR Secure" in the dashboard's App settings to fully exit.

Runtime data (event log, Guardian contact settings) is stored per-user in
`~/.nrsecure/`.

⚠️ **macOS port conflict**: macOS reserves port 5000 for its own AirPlay
Receiver by default. If the dashboard won't load, check System Settings →
General → AirDrop & Handoff → turn off **AirPlay Receiver**.

## Why the unsigned-app warnings happen, and how to remove them

Both OSes flag unsigned software by default. Removing the warnings permanently
requires paid developer accounts and certificates:
- **Windows**: an EV or standard code-signing certificate (~$100–400/yr from a CA),
  then signing the `.exe` with `signtool` as an extra CI step.
- **macOS**: an Apple Developer Program membership ($99/yr), then codesigning +
  notarizing the `.app` with `codesign`/`notarytool` as extra CI steps.

Both are addable to `build.yml` later if you want to distribute this more widely
without the warning prompts — just say the word once you have the
certificates/accounts and I'll wire in the signing steps.

## Rebuilding locally instead of using CI

If you have direct access to a Windows PC and/or a Mac and would rather build
without GitHub:

```bash
pip install -r requirements.txt
# macOS only, before the pyinstaller step:
iconutil -c icns assets/Icon.iconset -o assets/icon.icns
pyinstaller --noconfirm nrsecure.spec
# Windows -> dist/NRSecure.exe
# macOS   -> dist/NRSecure.app  (wrap in a .dmg with Disk Utility or `hdiutil create ...`,
#            and re-sign first: codesign --force --deep --sign - dist/NRSecure.app)
```
