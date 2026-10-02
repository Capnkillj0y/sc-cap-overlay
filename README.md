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
Using it below.

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

These use Windows' own global-hotkey system (`RegisterHotKey` — the same
mechanism screenshot tools and push-to-talk overlays use), so you never need
to alt-tab. Defaults:

| Hotkey | Does |
|---|---|
| `Ctrl+Alt+M` | Start / stop monitoring |
| `Ctrl+Alt+F` | FALSE ALARM (the last FLARE popup wasn't a real missile) |
| `Ctrl+Alt+T` | Show TEST FLARE |
| `Ctrl+Alt+G` | Toggle AI ASSIST (only once the model is `ready`) |

They're active any time the app is open — you don't need to be monitoring —
and the current bindings are always shown at the bottom of the window, with
a note if one couldn't be registered (usually another app already has it).

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

**Privacy / performance.** Everything lives in a `missile_ai` folder next to
the app and is never uploaded. It checks about 3 times a second at lower
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

Two files may appear next to the app if something goes wrong, for
troubleshooting:
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
  missile settings) is saved in a small `config.json` file next to the app
  (and any missile-AI data in a `missile_ai` folder beside it).
  Deleting it just means you'll be asked to calibrate again next time.
