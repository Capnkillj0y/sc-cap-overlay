## v1.2.3 — Prevent duplicate launches

Only one copy of the updated app can run per Windows login session, even when launched from different folders. A second launch displays an already-running message and exits before loading settings or starting OCR. The updater helper remains exempt. Windows releases the guard when the owning process ends, including after a crash.

Close older builds before installing this release: versions before v1.2.3 do not participate in this guard. Includes the v1.2.2 Tactical Clean alerts and v1.2.1 taskbar changes.

Windows verification: open the app, launch it again (also while minimized), confirm only the original remains. Close it and reopen. Test the regular update flow from this version on the next release.

## v1.2.2 — Tactical Clean alerts

- Bundled Orbitron alert font with crisp dark edging and subtle highlights.
- CAPACITOR LOW is yellow, CAPACITOR EMPTY is orange, and FLARE is red.
- Previous default alert colors upgrade automatically; custom colors, calibration, hotkeys, and positions are preserved.
- Live values, flashing, opacity, and preview controls remain available. Wide text windows expand to prevent clipping.
- Includes the v1.2.1 taskbar fix and existing installer/update support.

Before publishing the draft, check the three alerts over the game, calibration preview, and Windows minimize/restore. The Linux overlay checks passed; Windows behavior must be verified with the built EXE.

# Glass Cockpit · v1.2.2

## Taskbar lifecycle fix in 1.2.1

The main window now stays Windows-managed throughout startup, minimize and
restore. Its custom Glass Cockpit frame is drawn without switching Tk between
normal and override-redirect modes. Removed the repeated native hide/show calls
from Map events. Rounded corners, the Flight icon, settings and the installer
remain in place.

Validation: 23 local automated tests passed. A Windows-only regression test is
included in the existing GitHub test step; it checks native window styles,
three minimize/restore cycles, stable window identity and calibration hide/show.
The actual Explorer taskbar button still needs manual review on Windows:

1. Start v1.2.1 from the desktop shortcut; confirm its taskbar icon appears immediately.
2. Minimize using the app's minus button; confirm the taskbar icon remains.
3. Click the taskbar icon to restore, then repeat three times.
4. Minimize/restore with the taskbar and check calibration then cancel.
5. Confirm the Flight icon and rounded Glass Cockpit frame are preserved.

## Windows installer in 1.2.0

Download **SC-Capacitor-Setup.exe** from Releases. Setup lets you choose the
program folder, creates a Start menu shortcut, and offers a desktop shortcut
using the Flight icon. The app appears in Windows Installed apps for uninstall.
The default installation is per-user and does not request administrator access.

All calibration, hotkeys, preferences, logs and AI training data now live in:

```text
%LOCALAPPDATA%\SC Capacitor Overlay
```

Paste that path into File Explorer, or use **SC Capacitor Settings Folder** in
the Start menu. Program files default to `%LOCALAPPDATA%\Programs\SC Capacitor Overlay`.
Updates preserve the data folder. Uninstall removes the program and shortcuts
but keeps personal data for reinstall; delete that settings folder yourself
with the app closed if you want a full reset.

**Existing users:** close the old app and run Setup once. On the optional import
page, select the old app folder containing `config.json` and `missile_ai`. Import
runs on first launch, copies missing data, and leaves the originals unchanged.
If you previously updated to 1.2.0 using the in-app updater, your data is already
centralized. An in-app update alone does not create installation shortcuts; run
Setup once for those. Future in-app updates continue working normally.

Tesseract OCR is still a separate dependency; the app's existing OCR setup flow
handles it when required. This installer does not bundle another OCR engine.

## Flight icon in 1.1.4

Replaces the SC monogram with the selected Flight design: a silver futuristic
fighter on a frosted blue glass tile. The same artwork is bundled for the
Windows EXE, taskbar, and app windows, with transparent outer corners and
icon sizes from 16 to 256 pixels. Rounded window corners are retained.

## Window and icon polish in 1.1.3

The main Windows window now follows a rounded outline instead of leaving
square corner triangles outside the artwork. The region updates on resizing
and restore. A custom SC capacitor icon is included in the EXE, taskbar,
and window dialogs. The icon ships in sizes from 16 to 256 pixels.

## Fixes in 1.1.2

UPDATES now checks the configured release repository directly, with no repeated
repository or startup-preference prompts. Global shortcuts observe key state
instead of reserving keys, so single-letter bindings no longer block typing.
A single-letter binding still fires its action while typing; use a modifier
combination to avoid this. Windows keyboard behavior needs a manual check.

## UI polish in 1.1.1

The Updates button now has a frosted blue border, the version is larger and
matches the dashboard typography, selected missile switches have a blue track,
and learner status messages use matching uppercase lettering. Selected switches
remain visibly blue when monitoring temporarily locks their controls.

The controller now uses the frosted Glass Cockpit artwork, cyan frame lighting,
glass buttons, and an illuminated live capacitor gauge. All labels, values,
switches and actions are live controls. The background is a bundled texture;
it does not require an internet connection.

## GitHub updates (new in 1.1.0)

See **[GITHUB_WALKTHROUGH.md](GITHUB_WALKTHROUGH.md)** for the complete first-time
GitHub Desktop setup and future release steps. The UPDATES button checks for a release directly. Startup checks offer new releases, verify their checksum, and install
with your approval. The Windows release workflow builds the EXE, embeds the
repository automatically, performs a startup smoke check, and creates a draft
release for you to review and publish. The workflow uses a public repository.

## Startup crash fixed in 1.0.6

Fixed `height and width must be > 0` during Windows startup. Drawing now waits
until Tk reports a real canvas size, and gauge/switch image sizes are bounded
to at least one pixel. The exact 1x1 startup condition reproduced the error in
1.0.5 and passed with 1.0.6, including normal rendering after geometry is ready.

## Use this version

Extract the whole ZIP into a new folder. Run `build.bat` on Windows to create
`dist\SC_Capacitor_Overlay.exe`, then launch that newly built EXE. An older EXE
will still show the previous UI. This package contains source, not a prebuilt EXE.
The build script includes the new skin artwork automatically.

To run from Python instead:

```bat
python -m pip install -r requirements.txt
python sc_capacitor_ocr.py
```

Keep `glass_skin.py` and `glass_cockpit_skin.png` beside the main script when
running from source. On Windows, settings use the central folder described above.
A first launch imports missing data found beside an older EXE or source copy.

Drag the top bar to move the window. Its top-right controls minimize, maximize,
and close it. Double-click the top bar to maximize/restore. Drag the bottom-right
corner to resize; the entire interface scales together.

The gauge uses the existing automatically learned full-capacitor value.
Cached artwork and batched redraws keep its live reading visible during updates.
Screen capture, OCR, capacitor scaling, calibration and missile detection logic
are retained from the supplied application.

## Verification

UI checks covered setup/ready/monitoring states, position buttons through the
canvas click handler, toggle clicks, hotkey editor, gauge updates from empty to
full, maximize/restore and smaller-window scaling. Python compilation and ZIP
integrity checks passed. Live game OCR, Windows taskbar/minimize behavior and
Windows global hotkeys still need verification on a Windows PC.

`glass-cockpit-preview.png` shows the implemented interface using simulated
telemetry in the test environment. Its AI-unavailable and Windows-hotkey notices
reflect the test environment, not removal of those application features.

---

# SC Capacitor Overlay

A little on-screen alert that flashes when your Star Citizen weapon
capacitor is running low, so you're not caught firing on empty. Optionally
also watches for the in-game "INBOUND MISSILE" warning and throws up a big
**FLARE** prompt.

It reads everything straight off your screen (no game hacking, no memory
reading) and shows a clean warning right where you want it, in the color and
size you pick, for as long as you want it to stay up.

**Windows only.** Works while Star Citizen is in borderless or windowed mode
(not exclusive fullscreen).

---

## If you downloaded this to use it

You just need the one file: **`SC_Capacitor_Overlay.exe`**. Double-click it
— that's the whole install.

A couple of things you'll likely see the first time, both normal:

- **"Windows protected your PC"** — this is Windows SmartScreen warning
  about any app that isn't code-signed, not a sign of a problem. Click
  **More info** → **Run anyway**.
- **Antivirus flags it** — PyInstaller-built apps (the tool used to package
  this) are a known false-positive trigger for some antivirus software.
  If yours quarantines it, you may need to allow/restore it.
- **Tesseract OCR prompt** — the first time the app needs to read your
  screen, if it doesn't find Tesseract OCR (the engine that does that), it
  offers to install it for you automatically. One click, takes about a
  minute, needs an internet connection.

Once it's open, click **CALIBRATE** and follow the steps — see
[Using it](#using-it) below.

---

## Building it yourself (only needed if you're the one distributing this)

**1. Build the app**

This is the only step that needs Python — and only on *your* machine, only
this once, only so `build.bat` can run PyInstaller to produce the exe.
Nobody who receives the finished exe (including future-you) will ever need
Python for anything.

Double-click **`build.bat`**. If you don't have Python yet, grab it from
[python.org](https://www.python.org/downloads/) (tick "Add to PATH" during
install) and run `build.bat` again. The script handles everything else
itself and takes a minute or two.

When it's done, you'll have a file called **`SC_Capacitor_Overlay.exe`**
inside a new `dist` folder. Python, these scripts, and the template image
files are all baked into that one exe — you won't need any of them again.

**2. Share it**

Upload or send **just that one exe** — not this folder, not the `.py` file,
not `build.bat`. That single file is the entire app for anyone downloading
it; see the section above for what they'll experience.

If you're posting it somewhere public (Discord, Nexus, itch.io, etc.), it's
worth pasting a short heads-up about the SmartScreen/antivirus prompts
above so people aren't caught off guard — feel free to copy that section
as-is.

---

## Using it

**First time:** open the app and click **CALIBRATE**. It walks you through
everything:

1. A message asks you to get Star Citizen showing your capacitor number(s),
   then takes a screenshot.
2. Drag a box around the number(s) on your HUD, click **CONTINUE**.
3. It shows you what it read and lets you set the number that should
   trigger the alert (e.g. "flash when it drops below 25").
4. Optional missile-warning step — draw a box around `INBOUND MISSILE`,
   choose **Full Screen** (recommended, follows the warning as you look
   around the cockpit), or **Skip / Disable**.
5. Last step — a sample alert appears on your screen. Pick one of the five
   quick positions (**High / Low / Left / Center / Right**), or drag the
   preview anywhere for a custom spot, and adjust:
   - how see-through it is
   - how big the text is
   - the LOW and EMPTY alert colors
   - whether it stays up the whole time you're low, or flashes briefly and
     gets out of your way

Click **SAVE** and you're done.

**Every time after that:** open the app and click **START MONITORING**.
The five quick-position buttons and the **MISSILE OCR** toggle both work
live from the main window too — no need to recalibrate just to nudge the
overlay or turn missile detection on/off. **TEST FLARE** shows the missile
popup on demand so you can check it without waiting for an actual warning.

Click **STOP MONITORING** any time to pause; the app stays open so you can
start again without redoing anything. Closing the window shuts it down.

If your HUD ever moves — different ship, different resolution, a game
update — just run CALIBRATE again.

---

## Hotkeys (work even while Star Citizen has focus)

These observe Windows key state without reserving or suppressing keys, so
you can still type normally while the app is open. Defaults:

| Hotkey | Does |
|---|---|
| `Ctrl+Alt+M` | Start / stop monitoring |
| `Ctrl+Alt+F` | FALSE ALARM (the last FLARE popup wasn't a real missile) |
| `Ctrl+Alt+T` | Show TEST FLARE |
| `Ctrl+Alt+G` | Toggle AI ASSIST (only once the model is `ready`) |

They're active any time the app is open — you don't need to be monitoring —
and the current bindings are always shown at the bottom of the window.
Other apps receive the same keys. Single-letter bindings also trigger while
typing; use a modifier combination if that is unwanted.

**Remapping:** click **EDIT** next to the hotkey line in the main window
(shows up once you've calibrated). Click **RECORD** next to any action,
press the combo you want, and it's captured instantly -- letters, digits,
`F1`-`F12`, with `Ctrl`/`Alt`/`Shift`/`Win` in any combination. **CLEAR**
unbinds an action entirely. **Esc** cancels a recording in progress. It
warns you if two actions end up sharing the same combo, and **SAVE** applies
the new bindings immediately -- no restart needed.

You can also edit `config.json` directly if you prefer (same `hotkey_*`
keys, e.g. `"hotkey_false_alarm": "ctrl+alt+f"`), and the toggle at the top
of the settings dialog (or `"hotkeys_enabled": false` in the file) turns all
of them off.

---

## Switching ships

The alert threshold is remembered as a fraction of a ship's **full**
capacitor, and the app learns each ship's full value from the HUD itself: a
recharged capacitor stops moving, so a reading that holds still for about 5
seconds *is* that ship's full. Set the alert to 25 on a ship whose full is 75
and it becomes about 8 on a ship whose full is 25 — it no longer flashes LOW
at full charge just because the numbers are smaller.

- **Calibrate with a full capacitor** (the calibration screen reminds you) —
  that records the full value of the ship you set the threshold on.
- When the capacitor numbers disappear for about 8 seconds (leaving or
  entering a ship), it forgets the old ship and re-learns. For the first few
  seconds it uses the highest reading seen so far as a stand-in for "full".
- The **ALERT BELOW** card and the ring gauge show the limit and full value
  actually in force for the ship you're in.
- One ship, or several ships with the same capacitor size: nothing changes —
  the limit is exactly the threshold you set.
- Older config with no saved full value? It learns it from the first steady
  reading and saves it. If that first ship wasn't your usual one, just
  recalibrate.
- Don't want this? Set `"capacitor_autoscale": false` in `config.json` and it
  goes back to a plain fixed threshold. (Recalibrating keeps your choice.)

---

## Missile AI (optional, teaches itself)

Under the **MISSILE OCR** switch there's a small self-training model for the
`INBOUND MISSILE` warning. It runs entirely on your PC, is on by default
whenever MISSILE OCR is on, and never replaces the existing detector.

**What happens on its own**
- While you're monitoring, it keeps tiny black-and-white silhouettes of red
  on-screen text (never screenshots) and labels them from the detector's
  confident calls.
- When you press **STOP MONITORING** (or open the app), it trains itself in
  the background. It never trains *while* monitoring, so it can't slow down
  your alerts.
- A new model starts in **shadow mode**: it only watches, and is scored
  against the live detector. The status line shows progress, e.g.
  `v2 testing - 1/3 warnings seen`.
- Once it has caught at least 90% of 3+ real warnings without inventing any
  of its own, it's marked `ready`, and only then can you switch on
  **AI ASSIST**. Assist can only *add* a FLARE popup when the model is very
  confident — it never suppresses or overrides the detector.

**Your one job: FALSE ALARM.** If a FLARE popup wasn't a real missile,
alt-tab back afterwards and press **FALSE ALARM**. It corrects the most
recent detection, however long ago it was. That correction is the only real
ground truth the model gets, and it always outranks the detector's own guess.

**Honest limits.** It learns from the detector's confident calls plus your
corrections, so it can't discover a warning the detector never flags — its
value is adapting to *your* screen and giving a second opinion. It starts
from synthetic examples built from the bundled templates and improves as real
data arrives.

**Privacy / performance.** Training data lives in `missile_ai` inside the central
settings folder and is never uploaded. It checks about 3 times a second at lower
thread priority (roughly 5% of one core). Delete the folder to reset it, or
flip **MISSILE AI** off to stop collecting.

**Disk use is capped.** It stores at most 8,000 tiny samples (about 6 MB on
disk, measured), near-duplicates are skipped, and once full the oldest
routine samples are dropped to make room. The model file is ~0.3 MB. Your
FALSE ALARM corrections are the last thing it discards. Training time is
capped too, so it doesn't grow as you keep playing. `app_log.txt` rotates
itself at ~1 MB, so the whole footprint stays around 10 MB.

---

## If something's not reading right

Open the app and click CALIBRATE again — the box-and-threshold step shows
you exactly what number(s) it's picking up before you save, so you'll know
right away if the box needs adjusting.

Two files may appear in the central settings folder for troubleshooting:
- **`app_log.txt`** — general activity log
- **`error_log.txt`** — only appears if something actually crashed

You won't see a black command-prompt window — this app runs quietly in the
background with just its own window.

---

## Good to know

- This never touches Star Citizen itself — no memory reading, no file
  edits, no hooking into the game. It just looks at your screen the same
  way a screenshot tool would, which is why it needs you to show it where
  the number (and, if used, the missile warning) is.
- It won't work over exclusive fullscreen — switch Star Citizen to
  borderless or windowed mode in the graphics settings.
- Capacitor OCR runs at up to 8 checks/sec; missile detection (if enabled)
  runs independently at up to 16 checks/sec, since it needs to react faster.
- Everything (position, size, colors, opacity, alert duration, threshold,
  missile settings) is saved in `config.json` in the central settings folder
  (and missile-AI data in its `missile_ai` subfolder).
  Deleting it just means you'll be asked to calibrate again next time.

---

## Releases & updates (for the maintainer)

Follow [GITHUB_WALKTHROUGH.md](GITHUB_WALKTHROUGH.md). Change `__version__`, commit
and push, then run **Build Windows release** in GitHub Actions. The workflow
builds the EXE and installer, tests installation/migration/uninstallation on
Windows, and creates a draft release with four assets. Review and test it before
publishing. New users download Setup; existing app versions use the separate
EXE/checksum assets for automatic updates, so keep all four release assets.

To build locally, install Python 3.12 and Inno Setup 6, then run `build.bat`.
Windows installation and icon appearance still need your manual review of the
first installer release; Linux checks cannot validate Windows shell behavior.
