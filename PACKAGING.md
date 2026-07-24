# Packaging Seraph Clone as a Windows .exe and macOS .dmg

This folder (`desktop-app/`) is a single unified app — the risk/event server, the
remote-access-tool monitor, and a system tray icon all combined into one process —
built so it can be frozen into one native binary per OS. The browser extension is
separate and still gets loaded into Chrome by hand; browser extensions can't be
bundled into a native `.exe`/`.dmg`.

I can't compile a real Windows `.exe` or macOS `.dmg` from this Linux sandbox —
Windows binaries need a Windows toolchain, and `.dmg`/`.app` bundles need macOS's
own `iconutil`/`hdiutil`, neither of which exist on Linux. What's set up here is a
**GitHub Actions workflow** (`.github/workflows/build.yml`) that does the actual
compiling for you, for free, on Microsoft's and Apple's own cloud machines.

## One-time setup

1. Create a new GitHub repository (or use an existing one) and push everything in
   this `seraph-clone/` folder to it — including the `.github/workflows/build.yml`
   file at the top level.
   ```bash
   cd seraph-clone
   git init
   git add .
   git commit -m "Seraph Clone MVP"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
2. That push alone triggers the workflow, since it watches `desktop-app/**`.

## Getting the built files

1. On GitHub, open your repo → **Actions** tab → the latest "Build Seraph Clone
   desktop app" run.
2. Once it finishes (a few minutes), scroll to **Artifacts** at the bottom of the
   run page — download `SeraphClone-Windows` (contains the `.exe`) and
   `SeraphClone-macOS` (contains the `.dmg`).

**To get them on a proper GitHub Release page instead** (nicer for sharing with
others): tag a version and push the tag —
```bash
git tag v0.1.0
git push origin v0.1.0
```
This triggers the same build, then automatically attaches both files to a
Release at `https://github.com/<you>/<repo>/releases`.

## Running the built app

- **Windows**: double-click `SeraphClone-Windows.exe`. Since it isn't signed with
  a paid Microsoft code-signing certificate, SmartScreen will show "Windows
  protected your PC" the first time — click **More info → Run anyway**.
- **macOS**: open `SeraphClone-macOS.dmg`, drag `SeraphClone.app` to Applications.
  Since it isn't signed/notarized with a paid Apple Developer account, Gatekeeper
  will block it on first launch — right-click the app → **Open** → **Open** again
  in the dialog (only needed once).

Either way, the app puts an icon in the system tray/menu bar, opens the dashboard
in your browser automatically on first launch, and keeps running quietly in the
background. Runtime data (event log, Guardian contact settings) is stored in
`~/.seraph-clone/` in your home folder.

## Why these warnings happen, and how to remove them

Both OSes flag unsigned software by default. Removing the warnings permanently
requires paid developer accounts and certificates:
- **Windows**: an EV or standard code-signing certificate (~$100–400/yr from a CA),
  then signing the `.exe` with `signtool` as an extra CI step.
- **macOS**: an Apple Developer Program membership ($99/yr), then codesigning +
  notarizing the `.app` with `codesign`/`notarytool` as extra CI steps.

Both are addable to `build.yml` later if you want to distribute this beyond your
own machine without the warning prompts — just say the word and I can wire in the
signing steps once you have the certificates/accounts.

## Rebuilding locally instead of using CI

If you do have direct access to a Windows PC and/or a Mac and would rather build
without GitHub:

```bash
# On the target OS, inside desktop-app/
pip install -r requirements.txt
# macOS only, before the pyinstaller step:
iconutil -c icns assets/Icon.iconset -o assets/icon.icns
pyinstaller --noconfirm seraph.spec
# Windows -> dist/SeraphClone.exe
# macOS   -> dist/SeraphClone.app  (wrap in a .dmg with Disk Utility or `hdiutil create ...`)
```
