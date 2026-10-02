"""
Star Citizen Laser Capacitor Overlay (OCR version)
----------------------------------------------------
Reads the numeric capacitor value straight off your HUD and shows a
click-through, always-on-top alert when it's low or depleted.

Windows only. Works over borderless/windowed games (not exclusive fullscreen).

SETUP
  1. Install Tesseract OCR (the actual OCR engine, separate from Python):
     https://github.com/UB-Mannheim/tesseract/wiki  (Windows installer)
     Default install path is fine: C:\\Program Files\\Tesseract-OCR\\tesseract.exe
  2. pip install -r requirements.txt

CALIBRATE (one time)
  python sc_capacitor_ocr.py --calibrate

RUN
  python sc_capacitor_ocr.py
"""

__version__ = "1.1.1"  # bump this before publishing each GitHub release

import sys
# Run the separate replacement helper before loading OCR/Tk or the application.
if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
    from updater import apply_update
    raise SystemExit(apply_update(sys.argv[2]))
import os
import re
import json
import time
import threading
import argparse
import ctypes
import math
import subprocess

# Must happen before any window is created or any screen is captured, so mss
# (screen capture) and Tkinter (window positioning) agree on physical vs.
# scaled pixels. Without this, on any display with scaling other than 100%
# (125%/150% is extremely common on 1440p/4K monitors), the calibration box
# you draw and the overlay's actual position can end up subtly, silently off.
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # older fallback (Vista+)
        except Exception:
            pass
from difflib import SequenceMatcher
from collections import deque

import numpy as np
import mss
from PIL import Image, ImageOps, ImageTk, ImageEnhance, ImageFilter, ImageDraw
import pytesseract
import tkinter as tk
from tkinter import messagebox, colorchooser

try:
    import missile_ai          # optional on-device learner; app runs fine without it
except Exception:
    missile_ai = None

def app_dir():
    """Folder to read/write config & logs -- the exe's own folder when frozen,
    otherwise the folder this script lives in."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundled_resource(name):
    """Path to read-only assets in source runs and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


CONFIG_PATH = os.path.join(app_dir(), "config.json")

# A windowed (no-console) build has no stdout/stderr at all -- print() would
# crash the whole app the first time it's called. Redirect logging to a file
# instead so nothing breaks, and there's still somewhere to look for details.
def _rotate_log(path, limit=1_000_000):
    """Keep app_log.txt from growing forever: once it passes ~1 MB, move it
    aside (one old copy is kept), so the two files together stay ~2 MB."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > limit:
            os.replace(path, path + ".old")
    except Exception:
        pass


if sys.stdout is None or sys.stderr is None:
    _rotate_log(os.path.join(app_dir(), "app_log.txt"))
    _log = open(os.path.join(app_dir(), "app_log.txt"), "a", buffering=1)
    sys.stdout = _log
    sys.stderr = _log

# If Tesseract isn't on PATH, set the path here (uncomment and edit):
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ---------------------------------------------------------------------------
# Win32 click-through helpers
# ---------------------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080


def make_clickthrough(root: tk.Tk):
    if os.name != "nt":
        return
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


# ---------------------------------------------------------------------------
# Global hotkeys (work even while Star Citizen has focus)
# ---------------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
_MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_NAMED_KEYS = {f"f{i}": 0x6F + i for i in range(1, 13)}  # f1..f12 -> VK_F1..VK_F12


def parse_hotkey(spec):
    """'ctrl+alt+m' -> (modifier_flags, virtual_key_code), or None if it
    doesn't parse (an unsupported name, or no actual key given)."""
    if not spec or not isinstance(spec, str):
        return None
    mods, vk = 0, None
    for part in (p.strip().lower() for p in spec.split("+")):
        if not part:
            continue
        if part in _MOD_NAMES:
            mods |= _MOD_NAMES[part]
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            return None
    return (mods, vk) if vk is not None else None


class HotkeyManager:
    """Registers global hotkeys via RegisterHotKey -- the same Windows API
    screenshot tools and push-to-talk overlays use, rather than a low-level
    keyboard hook, so it needs no extra pip dependency and isn't the kind of
    thing that tends to draw anti-cheat attention. Windows only; a no-op
    everywhere else. Callbacks fire on a dedicated background thread, so
    every callback here just hands off to the Tk thread via root.after(0, ..)."""

    def __init__(self, bindings, log=print):
        """bindings: {id: (mods, vk, callback)}"""
        self.bindings = bindings
        self.log = log
        self.thread = None
        self._win_thread_id = None
        self.registered_ids = []
        self.failed_ids = []

    def start(self):
        if os.name != "nt" or not self.bindings:
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            import ctypes.wintypes as wintypes
        except Exception:
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._win_thread_id = kernel32.GetCurrentThreadId()
        for hotkey_id, (mods, vk, _cb) in self.bindings.items():
            ok = user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk)
            (self.registered_ids if ok else self.failed_ids).append(hotkey_id)
            if not ok:
                self.log(f"Hotkey id={hotkey_id} could not be registered "
                         f"(likely already bound by another app)")
        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_HOTKEY:
                cb = self.bindings.get(msg.wParam, (None, None, None))[2]
                if cb:
                    try:
                        cb()
                    except Exception as e:
                        self.log("Hotkey callback error:", e)
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for hotkey_id in self.registered_ids:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self):
        if self.thread is not None and self.thread.is_alive() and self._win_thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._win_thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
            self.thread.join(timeout=2)
        self.thread = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "region": {"left": 100, "top": 100, "width": 120, "height": 40},
    "low_threshold_value": 25,  # flash whenever the HUD number reads below this
    "poll_hz": 8,
    "overlay_corner": "center-low",  # legacy fallback, used only if overlay_pos is unset
    "overlay_anchor": "center-low",  # named snap point; "custom" uses overlay_pos
    "overlay_pos": None,            # {"x":.., "y":..} absolute screen position, set by the wizard
    "overlay_opacity": 100,         # 10-100 (%)
    "overlay_text_size": "medium",  # small, medium, large
    "missile_enabled": False,
    "missile_region": None,
    "missile_poll_hz": 16,
    "missile_ai_enabled": True,      # collect data + self-train (only while missile OCR is on)
    "missile_ai_assist": False,      # let a proven model ADD detections; off until you opt in
    "capacitor_autoscale": True,     # scale the alert to each ship's own full capacitor
    "reference_full": None,          # full capacitor on the ship the threshold was set on (learned/recorded)
    "hotkeys_enabled": True,
    "hotkey_toggle_monitoring": "ctrl+alt+m",
    "hotkey_false_alarm": "ctrl+alt+f",
    "hotkey_test_flare": "ctrl+alt+t",
    "hotkey_ai_assist": "ctrl+alt+g",
    "alert_duration": 0,            # 0 = stays visible the whole time it's low; >0 = seconds then auto-hides
    "alert_color_low": "#ffb238",    # amber -- shown when the reading is below threshold but not empty
    "alert_color_empty": "#ff4438",  # red -- shown when the reading hits 0
}

SIZE_MAP = {
    "small":  {"label": 15, "number": 17},
    "medium": {"label": 20, "number": 22},
    "large":  {"label": 26, "number": 30},
}


def friendly_error(e):
    """A short, human-readable explanation for common exception types --
    shown in dialogs/logs alongside (not instead of) the real traceback,
    which still goes to error_log.txt for actual debugging."""
    if isinstance(e, json.JSONDecodeError):
        return "A settings file was corrupted or wasn't valid JSON."
    if isinstance(e, FileNotFoundError):
        return f"A file couldn't be found: {getattr(e, 'filename', None) or e}"
    if isinstance(e, PermissionError):
        return ("Permission denied. Try moving the app out of Program Files or a similarly "
                "protected folder, or check your antivirus settings.")
    if isinstance(e, OSError) and getattr(e, "winerror", None) == 5:
        return "Windows denied access to a file or resource."
    if isinstance(e, MemoryError):
        return "Ran out of memory."
    if "tesseract" in str(e).lower():
        return "Tesseract OCR isn't installed, or couldn't be found."
    return str(e) or type(e).__name__


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print("No config.json found. Run with --calibrate first.")
        sys.exit(1)
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except Exception as e:
        # A corrupted settings file (e.g. from a crash mid-write, a full disk,
        # antivirus interference) used to crash the whole app on every launch
        # with no way to recover. Back it up and start fresh instead.
        backup_path = CONFIG_PATH + ".corrupted"
        try:
            import shutil
            shutil.copy2(CONFIG_PATH, backup_path)
        except Exception:
            backup_path = None
        msg = (
            f"Your settings file was unreadable ({friendly_error(e)}) and has been "
            + (f"backed up as {os.path.basename(backup_path)}.\n\n" if backup_path else "couldn't be backed up.\n\n")
            + "Please run CALIBRATE again."
        )
        print("Config file corrupted:", e)
        print(msg)
        try:
            if tk._default_root is not None:
                messagebox.showerror("Settings file corrupted", msg)
        except Exception:
            pass
        sys.exit(1)

    # migrate older configs (percentage-based) to the simpler raw-number threshold
    if "low_threshold_value" not in cfg:
        max_value = cfg.get("max_value", 100)
        is_percentage = cfg.get("is_percentage", True)
        low_pct = cfg.get("low_threshold_pct", 30)
        if is_percentage:
            cfg["low_threshold_value"] = low_pct
        else:
            cfg["low_threshold_value"] = round(max_value * low_pct / 100)
        save_config(cfg)
        print(f"(migrated old config -- low_threshold_value set to {cfg['low_threshold_value']})")

    # bump the old 4Hz capacitor poll default up to the new 8Hz default --
    # only if it's still at the old default, so anyone who never touched it
    # gets the faster rate without needing to recalibrate
    if cfg.get("poll_hz") == 4:
        cfg["poll_hz"] = 8
        save_config(cfg)
        print("(migrated old config -- capacitor poll_hz bumped from 4 to 8)")

    return cfg


def save_config(cfg):
    # Write to a temp file and rename over the real one -- os.replace is
    # atomic on both Windows and POSIX, so a crash or power loss mid-save
    # can never leave a half-written, corrupted config.json behind.
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
    print(f"Saved config to {CONFIG_PATH}")


# ---------------------------------------------------------------------------
# Calibration -- fully GUI, no console needed
# ---------------------------------------------------------------------------
COLORS = {
    # Glass Cockpit: navy glass surfaces, fine blue borders and a bright
    # cyan signal color. These colors are shared by the controller and
    # calibration wizard so the application feels like one product.
    "bg": "#071522",
    "bg2": "#0b2032",
    "panel": "#112e44",
    "panel2": "#183e57",
    "line": "#28516a",
    "cyan": "#32e4f4",
    "cyan_dim": "#258ba6",
    "amber": "#ffb238",
    "red": "#ff6868",
    "text": "#edf5f7",
    "text_dim": "#9fbdd2",
    "disabled_bg": "#122737",
    "disabled_fg": "#638095",
}


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def _shade(hex_color, factor):
    """factor > 1 lightens, < 1 darkens."""
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex((r * factor, g * factor, b * factor))


def _rounded_points(w, h, r):
    r = min(r, w / 2, h / 2)
    return [
        r, 0,  w - r, 0,  w, 0,  w, r,
        w, h - r,  w, h,  w - r, h,  r, h,
        0, h,  0, h - r,  0, r,  0, 0,
    ]


class RoundedButton(tk.Canvas):
    """A flat, rounded-rect button with hover/press feedback -- tkinter's
    stock Button can't do rounded corners, so this draws its own."""

    def __init__(self, parent, text, command=None, bg=COLORS["panel"], fg=COLORS["text"],
                 width=280, height=46, radius=10, font=("Segoe UI", 11, "bold"),
                 outline=None, **kw):
        parent_bg = kw.pop("parent_bg", None) or parent["bg"]
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                          highlightthickness=0, cursor="hand2", **kw)
        self.command = command
        self.base_bg = bg
        self.fg = fg
        self.radius = radius
        self.w, self.h = width, height
        self.text_str = text
        self.font = font
        self.outline = outline or _shade(bg, 1.16)
        self.disabled = False
        self._hovering = False
        self._draw(bg)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, color):
        self.delete("all")
        # inset by 1px so the outline stroke (centered on the path) doesn't
        # get clipped by the canvas edge -- the shape touching the boundary
        # exactly means half the stroke width falls outside and is cut off
        inset = 1
        raw = _rounded_points(self.w - inset * 2, self.h - inset * 2, max(1, self.radius - inset))
        pts = [v + inset for v in raw]
        # Rasterized gradient and rounded edge give every button a glass surface.
        scale = 2
        w, h = self.w * scale, self.h * scale
        surface = Image.new("RGBA", (w, h))
        brush = ImageDraw.Draw(surface)
        rgb = _hex_to_rgb(color)
        for y in range(h):
            factor = 1.24 - .32 * y / max(1, h - 1)
            shade = tuple(min(255, int(v * factor)) for v in rgb)
            brush.line((0, y, w, y), fill=shade+(255,))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((2, 2, w-3, h-3), radius=self.radius*scale, fill=255)
        surface.putalpha(mask)
        brush.rounded_rectangle((2, 2, w-3, h-3), radius=self.radius*scale,
            outline=_hex_to_rgb(self.outline)+(255,), width=scale)
        if not self.disabled:
            brush.line((14*scale, 3*scale, w-14*scale, 3*scale),
                       fill=_hex_to_rgb(_shade(color, 1.7))+(160,), width=scale)
        self._glass_photo = ImageTk.PhotoImage(surface.resize((self.w,self.h), Image.Resampling.LANCZOS))
        self.create_image(0, 0, anchor="nw", image=self._glass_photo)
        fg = COLORS["disabled_fg"] if self.disabled else self.fg
        self.create_text(self.w // 2, self.h // 2, text=self.text_str, fill=fg, font=self.font)

    def _on_enter(self, _e):
        if not self.disabled:
            self._hovering = True
            self._draw(_shade(self.base_bg, 1.15))

    def _on_leave(self, _e):
        self._hovering = False
        if not self.disabled:
            self._draw(self.base_bg)

    def _on_press(self, _e):
        if not self.disabled:
            self._draw(_shade(self.base_bg, 0.85))

    def _on_release(self, _e):
        if self.disabled:
            return
        self._draw(_shade(self.base_bg, 1.15) if self._hovering else self.base_bg)
        if self.command:
            self.command()

    def set_text(self, text):
        self.text_str = text
        self._draw(self.base_bg)

    def set_colors(self, bg=None, fg=None):
        if bg:
            self.base_bg = bg
            self.outline = _shade(bg, 1.16)
        if fg:
            self.fg = fg
        self._draw(self.base_bg)

    def set_disabled(self, disabled):
        self.disabled = disabled
        self._draw(COLORS["disabled_bg"] if disabled else self.base_bg)


class ToggleSwitch(tk.Canvas):
    """A small pill-shaped on/off switch with a label -- matches the app's
    hand-drawn widget style, unlike a native tk.Checkbutton which renders
    with the OS's default (visually inconsistent) checkbox chrome."""

    def __init__(self, parent, text, command=None, bg=None, initial=False, large=False, **kw):
        parent_bg = kw.pop("parent_bg", None) or bg or parent["bg"]
        self.text_str = text
        self.command = command
        self.value = initial
        self.disabled = False
        w, h = (260, 28) if large else (210, 22)
        super().__init__(parent, width=w, height=h, bg=parent_bg,
                          highlightthickness=0, cursor="hand2", **kw)
        self.w, self.h = w, h
        self.sw_w, self.sw_h = (46, 24) if large else (30, 16)
        self.knob_r = 10 if large else 6
        inset = 1
        raw = _rounded_points(self.sw_w - inset * 2, self.sw_h - inset * 2, max(1, self.sw_h // 2 - inset))
        pts = [v + inset for v in raw]
        self.track = self.create_polygon(pts, smooth=True, fill=COLORS["panel"], outline=COLORS["line"])
        self.knob = self.create_oval(2, 2, 2 + self.knob_r * 2, 2 + self.knob_r * 2, fill=COLORS["text_dim"], outline="")
        self.label = self.create_text(self.sw_w + 10, self.h // 2, anchor="w", text=_tracked(text),
                                       fill=COLORS["text_dim"], font=("Segoe UI", 7, "bold"))
        self.bind("<ButtonRelease-1>", self._on_click)
        self._redraw()

    def _redraw(self):
        on = self.value
        track_fill = COLORS["cyan_dim"] if on else COLORS["panel"]
        knob_fill = COLORS["cyan"] if on else COLORS["text_dim"]
        label_fill = COLORS["cyan"] if on else COLORS["text_dim"]
        if self.disabled:
            track_fill = COLORS["disabled_bg"]
            knob_fill = COLORS["disabled_fg"]
            label_fill = COLORS["disabled_fg"]
        self.itemconfig(self.track, fill=track_fill)
        self.itemconfig(self.knob, fill=knob_fill)
        self.itemconfig(self.label, fill=label_fill, text=_tracked(self.text_str))
        knob_x = self.sw_w - self.knob_r * 2 - 2 if on else 2
        self.coords(self.knob, knob_x, 2, knob_x + self.knob_r * 2, 2 + self.knob_r * 2)

    def _on_click(self, _e):
        if self.disabled:
            return
        self.value = not self.value
        self._redraw()
        if self.command:
            self.command()

    def get(self):
        return self.value

    def set(self, value):
        self.value = bool(value)
        self._redraw()

    def set_disabled(self, disabled):
        self.disabled = disabled
        self._redraw()


_MODIFIER_KEYSYMS = {
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
    "Super_L": "win", "Super_R": "win", "Win_L": "win", "Win_R": "win",
}
_MOD_ORDER = ["ctrl", "alt", "shift", "win"]


def _normalize_captured_key(keysym):
    """A tkinter keysym from a real keypress -> the same short key name
    parse_hotkey() expects ('m', '1', 'f5', ...), or None if it's a key we
    don't support as a hotkey (arrows, media keys, etc.)."""
    ks = keysym.lower()
    if len(ks) == 1 and ks.isalnum():
        return ks
    return ks if ks in _NAMED_KEYS else None


class HotkeyCapture(tk.Frame):
    """One row: an action label, a pill showing the current binding, a
    RECORD button (click, then press the combo -- modifiers are tracked
    directly from press/release, not from the platform's own state bitmask,
    so it behaves the same on every system), and a small clear button."""

    def __init__(self, parent, label_text, initial_spec, bg=None, **kw):
        parent_bg = bg or parent["bg"]
        super().__init__(parent, bg=parent_bg, **kw)
        self.spec = initial_spec or ""
        self.recording = False
        self.held_mods = set()

        tk.Label(self, text=label_text, bg=parent_bg, fg=COLORS["text"],
                  font=("Segoe UI", 9), width=15, anchor="w").pack(side="left")

        self.display = tk.Label(self, text=self._display_text(), bg=COLORS["panel"], fg=COLORS["cyan"],
                                font=("Segoe UI", 9, "bold"), width=16, anchor="center", pady=4)
        self.display.pack(side="left", padx=(0, 6))

        self.record_btn = RoundedButton(self, text="RECORD", command=self.start_recording,
                                        bg=COLORS["panel"], fg=COLORS["text"], width=70, height=26,
                                        radius=6, font=("Segoe UI", 8, "bold"), parent_bg=parent_bg,
                                        takefocus=1)
        self.record_btn.pack(side="left", padx=(0, 4))
        # Bind directly on the RECORD button -- it's already a real, mapped,
        # visible widget, so giving it keyboard focus actually works. (An
        # earlier version used a separate invisible widget for this that was
        # never packed, so it could never really receive focus, and no key
        # presses were ever seen while "recording".)
        self.record_btn.bind("<KeyPress>", self._on_key_press)
        self.record_btn.bind("<KeyRelease>", self._on_key_release)
        self.record_btn.bind("<FocusOut>", lambda e: self._stop_recording(commit=False))

        RoundedButton(self, text="CLEAR", command=self.clear, bg=parent_bg, fg=COLORS["text_dim"],
                     width=56, height=26, radius=6, font=("Segoe UI", 8, "bold"), parent_bg=parent_bg,
                     outline=COLORS["line"]).pack(side="left")

    def _display_text(self):
        return self.spec if self.spec else "(unset)"

    def start_recording(self):
        if self.recording:
            return
        self.recording = True
        self.held_mods = set()
        self.display.config(text="press keys...", fg=COLORS["amber"])
        self.record_btn.set_text("...")
        self.record_btn.focus_set()

    def _stop_recording(self, commit):
        self.recording = False
        self.record_btn.set_text("RECORD")
        if not commit:
            self.display.config(text=self._display_text(), fg=COLORS["cyan"])

    def _on_key_press(self, event):
        if not self.recording:
            return
        ks = event.keysym
        if ks == "Escape":
            self._stop_recording(commit=False)
            return "break"
        if ks in _MODIFIER_KEYSYMS:
            self.held_mods.add(_MODIFIER_KEYSYMS[ks])
            mods = [m for m in _MOD_ORDER if m in self.held_mods]
            self.display.config(text="+".join(mods) + "+..." if mods else "press keys...")
            return "break"
        key = _normalize_captured_key(ks)
        if key is None:
            return "break"  # not a supported key -- keep waiting
        mods = [m for m in _MOD_ORDER if m in self.held_mods]
        self.spec = "+".join(mods + [key])
        self.display.config(text=self._display_text(), fg=COLORS["cyan"])
        self._stop_recording(commit=True)
        return "break"

    def _on_key_release(self, event):
        if not self.recording:
            return
        ks = event.keysym
        if ks in _MODIFIER_KEYSYMS:
            self.held_mods.discard(_MODIFIER_KEYSYMS[ks])
        return "break"

    def clear(self):
        self.spec = ""
        self.display.config(text=self._display_text(), fg=COLORS["text_dim"])

    def get(self):
        return self.spec


class RoundedPanel(tk.Canvas):
    """Rounded container with a normal Frame inside it.

    Tkinter Frames are always square.  Drawing the shell on a Canvas gives the
    controller the large rounded cards from the Clean Tactical concept while
    still allowing ordinary widgets and layout managers inside each card.
    """

    def __init__(self, parent, width, height, fill=None, outline=None,
                 radius=18, inset=3, **kw):
        parent_bg = kw.pop("parent_bg", None) or parent["bg"]
        fill = fill or COLORS["bg2"]
        outline = outline or COLORS["line"]
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, **kw)
        self.fill = fill
        # shift the polygon in by 1px on each side so its outline stroke
        # (centered on the path) doesn't get clipped by the canvas edge
        raw = _rounded_points(width - 2, height - 2, max(1, radius - 1))
        pts = [v + 1 for v in raw]
        self.create_polygon(pts, smooth=True, fill=fill, outline=outline, width=1)
        self.body = tk.Frame(self, bg=fill)
        self.create_window(inset, inset, anchor="nw", window=self.body,
                           width=width - inset * 2, height=height - inset * 2)


def _tracked(text):
    """Light letter-spacing for short caps labels/buttons -- part of the
    'quantum nav console' look, distinct from the in-game overlay text
    (which intentionally has none)."""
    return "\u2009".join(text)


def _render_ring_image(size, thickness, fraction, color_hex, track_hex, glow=True):
    """Rasterize the ring gauge with PIL, supersampled for smooth edges.
    Tkinter's own canvas arcs can't do a real soft glow or tick marks --
    faking it with a second flat arc reads much flatter than an actual blur."""
    scale = 3
    S = size * scale
    T = max(2, int(thickness * scale))
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = S / 2
    pad = T / 2 + 5 * scale
    bbox = [pad, pad, S - pad, S - pad]
    ring_radius = (S - 2 * pad) / 2

    # tick marks around the dial, longer/brighter every 90 degrees
    tick_r_outer = cx - 2 * scale
    for deg in range(0, 360, 15):
        major = deg % 90 == 0
        length = (10 if major else 5) * scale
        rad = math.radians(deg - 90)
        x1 = cx + tick_r_outer * math.cos(rad)
        y1 = cy + tick_r_outer * math.sin(rad)
        x2 = cx + (tick_r_outer - length) * math.cos(rad)
        y2 = cy + (tick_r_outer - length) * math.sin(rad)
        tick_color = (150, 170, 175, 230) if major else (70, 90, 95, 150)
        draw.line([x1, y1, x2, y2], fill=tick_color, width=max(1, scale))

    # background track (the full, unfilled ring)
    track_rgb = _hex_to_rgb(track_hex)
    draw.arc(bbox, start=-90, end=270, fill=track_rgb + (255,), width=T)

    color_rgb = _hex_to_rgb(color_hex)
    end_angle = -90 + 360 * fraction

    if fraction > 0.001:
        if glow:
            glow_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gdraw = ImageDraw.Draw(glow_layer)
            gdraw.arc(bbox, start=-90, end=end_angle, fill=color_rgb + (255,), width=T + 6 * scale)
            glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(4 * scale))
            img = Image.alpha_composite(img, glow_layer)
            draw = ImageDraw.Draw(img)

        draw.arc(bbox, start=-90, end=end_angle, fill=color_rgb + (255,), width=T)
        # rounded end caps, faked with small filled circles at each end
        r_cap = T / 2
        for ang in (-90, end_angle):
            rad = math.radians(ang)
            x = cx + ring_radius * math.cos(rad)
            y = cy + ring_radius * math.sin(rad)
            draw.ellipse([x - r_cap, y - r_cap, x + r_cap, y + r_cap], fill=color_rgb + (255,))

    return img.resize((size, size), Image.LANCZOS)


class RingGauge(tk.Canvas):
    """Circular progress ring with tick marks and a real soft glow (via PIL),
    styled after Star Citizen's quantum-drive spool/charge indicators.
    Reading is centered inside as native tkinter text (crisper than
    rasterized text)."""

    def __init__(self, parent, size=190, thickness=9, bg=None, **kw):
        parent_bg = bg or parent["bg"]
        super().__init__(parent, width=size, height=size, bg=parent_bg,
                          highlightthickness=0, **kw)
        self.size = size
        self.thickness = thickness
        self._photo = None
        self._last_render_key = None   # (fraction, color) already on screen -- skip re-rendering if unchanged
        cx = cy = size // 2
        self.image_id = self.create_image(cx, cy, image=None)
        self.number = self.create_text(cx, cy - 12, text="--", fill=COLORS["text"],
                                        font=("Segoe UI", 34, "bold"))
        self.caption = self.create_text(cx, cy + 30, text=_tracked("READING"),
                                         fill=COLORS["text_dim"], font=("Segoe UI", 9, "bold"))
        self.set("--", 0.0, COLORS["cyan"])

    def set(self, text, fraction, color):
        # cap just under a full circle -- an exact 360deg arc wraps to 0
        fraction = max(0.0, min(0.999, fraction))
        # The PIL render (with a real Gaussian blur) costs ~15ms; the UI tick
        # calls this 20x/sec regardless of whether the reading actually moved,
        # so re-rendering unconditionally burns real CPU for no visual change
        # most of the time. Round to well below one pixel of arc movement on
        # a ring this size, so nothing is ever visibly skipped.
        key = (round(fraction, 3), color)
        if key != self._last_render_key:
            img = _render_ring_image(self.size, self.thickness, fraction, color, COLORS["line"])
            self._photo = ImageTk.PhotoImage(img)
            self.itemconfig(self.image_id, image=self._photo)
            self._last_render_key = key
        self.itemconfig(self.number, text=text, fill=color)
        self.tag_raise(self.number)
        self.tag_raise(self.caption)


class GlassRingGauge(RingGauge):
    """Segmented glass cockpit instrument with cached high-resolution artwork."""
    def __init__(self, parent, size=360, thickness=18, **kw):
        super().__init__(parent, size=size, thickness=thickness, **kw)
        self.itemconfigure(self.number, font=("Segoe UI", 66, "bold"))
        self.coords(self.number, size/2, size/2-9)
        self.itemconfigure(self.caption, font=("Segoe UI", 12, "bold"), fill=COLORS["cyan"])
        self.coords(self.caption, size/2, size/2+55)

    def set(self, text, fraction, color):
        fraction = max(0.0, min(1.0, fraction))
        key = (round(fraction, 3), color)
        if key != self._last_render_key:
            scale, size = 2, self.size
            S, c = size*scale, size*scale/2
            img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            rgb = _hex_to_rgb(color)
            def circle(r, fill, width=1):
                d.ellipse((c-r*scale,c-r*scale,c+r*scale,c+r*scale), outline=fill, width=width*scale)
            circle(175, (43, 100, 129, 210))
            circle(147, (35, 109, 140, 255))
            circle(121, (32, 97, 122, 230))
            circle(115, (20, 59, 79, 200))
            for deg in range(0, 360, 3):
                a = math.radians(deg-90)
                outer = 171 if deg % 30 == 0 else 168
                inner = 157 if deg % 30 == 0 else 162
                d.line((c+inner*scale*math.cos(a),c+inner*scale*math.sin(a),
                        c+outer*scale*math.cos(a),c+outer*scale*math.sin(a)),
                        fill=(93, 170, 200, 220) if deg%30==0 else (40, 95, 123, 190), width=scale)
            box = (c-149*scale,c-149*scale,c+149*scale,c+149*scale)
            for i in range(8):
                start, end = -90+i*45+1, -90+(i+1)*45-1
                d.arc(box, start, end, fill=(22, 58, 78, 255), width=18*scale)
                progress = -90+360*fraction
                if progress > start:
                    d.arc(box, start, min(end, progress), fill=rgb+(255,), width=18*scale)
            # Cardinal crosshair ticks and the top index beacon.
            for deg in range(0, 360, 90):
                a=math.radians(deg)
                d.line((c+176*scale*math.cos(a),c+176*scale*math.sin(a),
                        c+180*scale*math.cos(a),c+180*scale*math.sin(a)), fill=rgb+(255,),width=2*scale)
            d.ellipse((c-5*scale,c-154*scale,c+5*scale,c-144*scale),fill=rgb+(255,))
            halo = img.filter(ImageFilter.GaussianBlur(4*scale))
            halo.putalpha(halo.getchannel("A").point(lambda a: int(a*.32)))
            img = Image.alpha_composite(halo, img)
            self._photo = ImageTk.PhotoImage(img.resize((size,size), Image.Resampling.LANCZOS))
            self.itemconfigure(self.image_id, image=self._photo)
            self._last_render_key = key
        self.itemconfigure(self.number, text=text, fill=color)
        self.tag_raise(self.number)
        self.tag_raise(self.caption)


def calibrate(parent=None):
    """Runs the whole calibration flow as tkinter dialogs/windows -- box
    selection, then a results screen showing exactly what was read, with the
    alert threshold set right there. Returns True if a new config was saved."""
    check_tesseract_or_die()

    owns_root = parent is None
    if owns_root:
        root = tk.Tk()
        root.withdraw()  # just an anchor window, never shown
    else:
        root = parent
        root.withdraw()  # hide the control panel so it isn't in the screenshot

    messagebox.showinfo(
        "Calibrate",
        "Get Star Citizen showing your capacitor number(s) on screen "
        "(borderless/windowed mode). If possible, also have INBOUND MISSILE "
        "visible for the missile-warning step. Click OK when ready -- a "
        "screenshot will be taken immediately.",
    )

    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.rgb)

    if not owns_root:
        root.deiconify()

    saved = _calibrate_pick_region(root, img)

    if owns_root:
        root.destroy()
    return saved


def _calibrate_pick_region(root, img):
    screen_w, screen_h = img.size
    max_w, max_h = 1600, 900
    scale = min(max_w / screen_w, max_h / screen_h, 1.0)
    disp_w, disp_h = int(screen_w * scale), int(screen_h * scale)
    disp_img = img.resize((disp_w, disp_h))

    cal_win = tk.Toplevel(root)
    cal_win.title("Drag a box around the capacitor number(s)")
    cal_win.configure(bg=COLORS["bg"])
    cal_win.grab_set()

    tk.Label(cal_win, text="Click-drag a box around the capacitor number(s) on your HUD",
              bg=COLORS["bg"], fg=COLORS["cyan"], font=("Segoe UI", 10, "bold")).pack(pady=(10, 6))

    canvas = tk.Canvas(cal_win, width=disp_w, height=disp_h, cursor="cross", highlightthickness=0)
    canvas.pack(padx=10)
    tk_img = ImageTk.PhotoImage(disp_img)
    canvas.image = tk_img  # keep a reference so it isn't garbage collected
    canvas.create_image(0, 0, anchor="nw", image=tk_img)

    box = {}
    rect_id = [None]
    result = {"done": False}

    def on_press(e):
        box["x1"], box["y1"] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])

    def on_drag(e):
        box["x2"], box["y2"] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])
        rect_id[0] = canvas.create_rectangle(
            box["x1"], box["y1"], box["x2"], box["y2"], outline=COLORS["cyan"], width=2
        )

    def on_release(e):
        box["x2"], box["y2"] = e.x, e.y

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    btn_row = tk.Frame(cal_win, bg=COLORS["bg"])
    btn_row.pack(pady=10)

    def on_continue():
        if "x1" not in box or "x2" not in box:
            messagebox.showwarning("No box drawn", "Drag a box around the number(s) first.", parent=cal_win)
            return
        result["done"] = True
        cal_win.destroy()

    def on_cancel():
        cal_win.destroy()

    RoundedButton(btn_row, text="CANCEL", command=on_cancel, bg=COLORS["bg2"], fg=COLORS["text_dim"],
                  width=110, height=38, radius=9, font=("Segoe UI", 9, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)
    RoundedButton(btn_row, text="CONTINUE", command=on_continue, bg=COLORS["cyan"], fg="#04211f",
                  width=150, height=38, radius=9, font=("Segoe UI", 10, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)

    cal_win.wait_window()

    if not result["done"]:
        return False

    x1, x2 = sorted([box["x1"], box["x2"]])
    y1, y2 = sorted([box["y1"], box["y2"]])
    real_x1, real_y1 = int(x1 / scale), int(y1 / scale)
    real_x2, real_y2 = int(x2 / scale), int(y2 / scale)
    pad = 4
    region = {
        "left": max(0, real_x1 - pad),
        "top": max(0, real_y1 - pad),
        "width": max(10, (real_x2 - real_x1) + pad * 2),
        "height": max(10, (real_y2 - real_y1) + pad * 2),
    }

    return _calibrate_show_results(root, img, region)


def _calibrate_show_results(root, img, region):
    test_crop = img.crop((region["left"], region["top"],
                           region["left"] + region["width"], region["top"] + region["height"]))
    groups = find_number_groups(test_crop)
    vals = read_numbers_from_crop(test_crop, None)

    debug_crop_path = os.path.join(app_dir(), "calibration_crop.png")
    test_crop.save(debug_crop_path)

    win = tk.Toplevel(root)
    win.title("Calibration result")
    win.configure(bg=COLORS["bg"])
    win.resizable(False, False)
    win.grab_set()

    tk.Label(win, text="CALIBRATION RESULT", bg=COLORS["bg"], fg=COLORS["cyan"],
              font=("Segoe UI", 12, "bold")).pack(pady=(16, 8))

    # thumbnail preview of exactly what was captured, scaled up a bit so it's visible
    prev_w = min(360, test_crop.size[0] * 3)
    prev_h = int(test_crop.size[1] * (prev_w / test_crop.size[0]))
    preview_img = test_crop.resize((prev_w, prev_h))
    tk_preview = ImageTk.PhotoImage(preview_img)
    prev_label = tk.Label(win, image=tk_preview, bg="black", bd=1, relief="solid")
    prev_label.image = tk_preview
    prev_label.pack(pady=(0, 10))

    if vals:
        reading_text = f"Read: {vals}"
        reading_color = COLORS["cyan"]
    else:
        reading_text = "Couldn't read a number in that box"
        reading_color = COLORS["red"]
    tk.Label(win, text=reading_text, bg=COLORS["bg"], fg=reading_color,
              font=("Segoe UI", 11, "bold")).pack()
    tk.Label(win, text=f"({len(groups)} number(s) detected -- lowest one triggers the alert)",
              bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 9)).pack()
    tk.Label(win, text="Tip: calibrate with a FULL capacitor so the alert scales correctly between ships.",
              bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 8)).pack(pady=(2, 14))

    form = tk.Frame(win, bg=COLORS["bg"])
    form.pack(pady=(0, 6))
    tk.Label(form, text="Flash alert when a number drops below:", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 10)).grid(row=0, column=0, padx=(0, 8))
    threshold_var = tk.StringVar(value="25")
    entry = tk.Entry(form, textvariable=threshold_var, width=6, font=("Segoe UI", 11),
                      bg=COLORS["panel"], fg=COLORS["text"], insertbackground=COLORS["text"],
                      bd=0, justify="center")
    entry.grid(row=0, column=1)

    outcome = {"next": False, "redo": False}

    def on_next():
        try:
            threshold = int(threshold_var.get())
        except ValueError:
            messagebox.showwarning("Invalid number", "Enter a whole number for the threshold.", parent=win)
            return
        outcome["next"] = True
        outcome["threshold"] = threshold
        win.destroy()

    def on_redo():
        outcome["redo"] = True
        win.destroy()

    def on_cancel():
        win.destroy()

    btn_row = tk.Frame(win, bg=COLORS["bg"])
    btn_row.pack(pady=18)
    RoundedButton(btn_row, text="CANCEL", command=on_cancel, bg=COLORS["bg2"], fg=COLORS["text_dim"],
                  width=100, height=38, radius=9, font=("Segoe UI", 9, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)
    RoundedButton(btn_row, text="REDO BOX", command=on_redo, bg=COLORS["panel"], fg=COLORS["text"],
                  width=120, height=38, radius=9, font=("Segoe UI", 9, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)
    RoundedButton(btn_row, text="NEXT", command=on_next, bg=COLORS["cyan"], fg="#04211f",
                  width=120, height=38, radius=9, font=("Segoe UI", 10, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)

    win.wait_window()

    if outcome["redo"]:
        return _calibrate_pick_region(root, img)

    if outcome["next"]:
        return _calibrate_pick_missile_region(root, img, region, outcome["threshold"])

    return False


def _auto_missile_region(screen_w, screen_h):
    """Resolution-relative upper-center HUD area used when no warning is
    available during calibration."""
    left = int(screen_w * 0.43)
    top = int(screen_h * 0.20)
    right = int(screen_w * 0.58)
    bottom = int(screen_h * 0.34)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def _calibrate_pick_missile_region(root, img, capacitor_region, threshold):
    """Optional second calibration box for the upper-center inbound warning."""
    screen_w, screen_h = img.size
    max_w, max_h = 1600, 900
    scale = min(max_w / screen_w, max_h / screen_h, 1.0)
    disp_w, disp_h = int(screen_w * scale), int(screen_h * scale)
    disp_img = img.resize((disp_w, disp_h))

    win = tk.Toplevel(root)
    win.title("Missile warning calibration")
    win.configure(bg=COLORS["bg"])
    win.grab_set()

    tk.Label(win, text="MISSILE WARNING REGION", bg=COLORS["bg"], fg=COLORS["red"],
             font=("Segoe UI", 11, "bold")).pack(pady=(10, 3))
    tk.Label(win, text="Use FULL SCREEN so the detector follows the cockpit warning as you look around.\n"
                       "A manual box remains available only for fixed-view setups.",
             bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 9),
             justify="center").pack(pady=(0, 7))

    canvas = tk.Canvas(win, width=disp_w, height=disp_h, cursor="cross", highlightthickness=0)
    canvas.pack(padx=10)
    tk_img = ImageTk.PhotoImage(disp_img)
    canvas.image = tk_img
    canvas.create_image(0, 0, anchor="nw", image=tk_img)

    box = {}
    rect_id = [None]
    outcome = {"continue": False, "auto": False, "skip": False, "back": False}

    def on_press(e):
        box["x1"], box["y1"] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])

    def on_drag(e):
        box["x2"], box["y2"] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])
        rect_id[0] = canvas.create_rectangle(box["x1"], box["y1"], box["x2"], box["y2"],
                                              outline=COLORS["red"], width=2)

    def on_release(e):
        box["x2"], box["y2"] = e.x, e.y

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    def use_region():
        if "x1" not in box or "x2" not in box:
            messagebox.showwarning("No box drawn", "Draw a box around INBOUND MISSILE first.", parent=win)
            return
        outcome["continue"] = True
        win.destroy()

    def skip_region():
        outcome["skip"] = True
        win.destroy()

    def auto_region():
        outcome["auto"] = True
        win.destroy()

    def go_back():
        outcome["back"] = True
        win.destroy()

    buttons = tk.Frame(win, bg=COLORS["bg"])
    buttons.pack(pady=10)
    RoundedButton(buttons, text="BACK", command=go_back, bg=COLORS["bg2"], fg=COLORS["text_dim"],
                  width=75, height=38, radius=8, font=("Segoe UI", 8, "bold"),
                  parent_bg=COLORS["bg"]).pack(side="left", padx=5)
    RoundedButton(buttons, text="SKIP / DISABLE", command=skip_region, bg=COLORS["panel"], fg=COLORS["text"],
                  width=115, height=38, radius=8, font=("Segoe UI", 7, "bold"),
                  parent_bg=COLORS["bg"]).pack(side="left", padx=5)
    RoundedButton(buttons, text="FULL SCREEN", command=auto_region, bg=COLORS["cyan"], fg="#04211f",
                  width=115, height=38, radius=8, font=("Segoe UI", 8, "bold"),
                  parent_bg=COLORS["bg"]).pack(side="left", padx=5)
    RoundedButton(buttons, text="USE REGION", command=use_region, bg=COLORS["red"], fg="#260403",
                  width=105, height=38, radius=8, font=("Segoe UI", 8, "bold"),
                  parent_bg=COLORS["bg"]).pack(side="left", padx=5)

    win.wait_window()
    if outcome["back"]:
        return _calibrate_show_results(root, img, capacitor_region)
    if outcome["skip"]:
        return _calibrate_customize_overlay(root, img, capacitor_region, threshold, None)
    if outcome["auto"]:
        return _calibrate_customize_overlay(
            root, img, capacitor_region, threshold, _auto_missile_region(screen_w, screen_h)
        )
    if not outcome["continue"]:
        return False

    x1, x2 = sorted([box["x1"], box["x2"]])
    y1, y2 = sorted([box["y1"], box["y2"]])
    real_x1, real_y1 = int(x1 / scale), int(y1 / scale)
    real_x2, real_y2 = int(x2 / scale), int(y2 / scale)
    pad = 8
    missile_region = {
        "left": max(0, real_x1 - pad),
        "top": max(0, real_y1 - pad),
        "width": max(40, (real_x2 - real_x1) + pad * 2),
        "height": max(20, (real_y2 - real_y1) + pad * 2),
    }
    return _calibrate_customize_overlay(root, img, capacitor_region, threshold, missile_region)


def _calibrate_customize_overlay(root, img, region, threshold, missile_region=None):
    """Live, drag-to-place customization step -- position, opacity, text
    size, colors, and alert duration, all previewed in real time before
    saving."""
    existing = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    cfg = dict(DEFAULT_CONFIG)
    cfg.update({
        "region": region,
        "low_threshold_value": threshold,
        "overlay_pos": existing.get("overlay_pos"),
        "overlay_anchor": existing.get(
            "overlay_anchor",
            "custom" if existing.get("overlay_pos") else existing.get("overlay_corner", "center-low"),
        ),
        "overlay_opacity": existing.get("overlay_opacity", 100),
        "overlay_text_size": existing.get("overlay_text_size", "medium"),
        "alert_duration": existing.get("alert_duration", 0),
        "alert_color_low": existing.get("alert_color_low", DEFAULT_CONFIG["alert_color_low"]),
        "alert_color_empty": existing.get("alert_color_empty", DEFAULT_CONFIG["alert_color_empty"]),
        "missile_enabled": missile_region is not None,
        "missile_region": missile_region,
        "capacitor_autoscale": existing.get("capacitor_autoscale", DEFAULT_CONFIG["capacitor_autoscale"]),
    })
    # Remember how big "full" was on THIS ship, so the alert can scale when you
    # swap to a ship with a different capacitor size. (Calibrate with the
    # capacitor full -- the results screen says so.)
    try:
        crop = img.crop((region["left"], region["top"],
                         region["left"] + region["width"], region["top"] + region["height"]))
        seen = read_numbers_from_crop(crop, None)
        cfg["reference_full"] = max(seen) if seen else None
    except Exception:
        cfg["reference_full"] = None

    preview = Overlay(root, dict(cfg), draggable=True)

    win = tk.Toplevel(root)
    win.title("Position & style")
    win.configure(bg=COLORS["bg"])
    win.resizable(False, False)

    tk.Label(win, text="POSITION & STYLE", bg=COLORS["bg"], fg=COLORS["cyan"],
              font=("Segoe UI", 12, "bold")).pack(pady=(16, 4))
    tk.Label(win, text="Drag the sample alert on your screen to where you want it.\n"
                        "Everything below updates the live preview instantly.",
              bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 9), justify="center"
              ).pack(pady=(0, 14))

    body = tk.Frame(win, bg=COLORS["bg"])
    body.pack(padx=28, pady=(0, 6), fill="x")

    # -- named position presets --
    tk.Label(body, text="Overlay position", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 5))
    position_var = tk.StringVar(value=cfg.get("overlay_anchor", "center-low"))
    position_row = tk.Frame(body, bg=COLORS["bg"])
    position_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))
    position_buttons = {}

    def choose_position(key):
        position_var.set(key)
        preview.snap_to(key)
        for preset, button in position_buttons.items():
            button.set_colors(
                bg=COLORS["cyan"] if preset == key else COLORS["panel"],
                fg="#04211f" if preset == key else COLORS["text"],
            )

    for i, (key, label) in enumerate((
        ("center-high", "HIGH"), ("center-low", "LOW"),
        ("center-left", "LEFT"), ("center", "CENTER"),
        ("center-right", "RIGHT"),
    )):
        btn = RoundedButton(position_row, text=label, command=lambda k=key: choose_position(k),
                            bg=COLORS["cyan"] if position_var.get() == key else COLORS["panel"],
                            fg="#04211f" if position_var.get() == key else COLORS["text"],
                            width=56, height=28, radius=6, font=("Segoe UI", 7, "bold"),
                            parent_bg=COLORS["bg"])
        btn.grid(row=0, column=i, padx=(0, 5))
        position_buttons[key] = btn
    tk.Label(body, text="Pick a snap point, or drag the preview anywhere for a custom position.",
              bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 8)
              ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 12))

    def mark_custom(_event=None):
        if position_var.get() != "custom":
            position_var.set("custom")
            for button in position_buttons.values():
                button.set_colors(bg=COLORS["panel"], fg=COLORS["text"])

    preview.on_drag = mark_custom

    # -- opacity --
    tk.Label(body, text="Opacity", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w", pady=(0, 2))
    opacity_var = tk.IntVar(value=cfg["overlay_opacity"])

    def on_opacity(v):
        preview.set_opacity(int(float(v)))

    opacity_scale = tk.Scale(body, from_=20, to=100, orient="horizontal", variable=opacity_var,
                              command=on_opacity, bg=COLORS["bg"], fg=COLORS["text"],
                              troughcolor=COLORS["panel"], highlightthickness=0, bd=0,
                              activebackground=COLORS["cyan"], length=260, showvalue=True)
    opacity_scale.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 2))
    tk.Label(body, text="(the preview box stays fully visible here so it's easy to drag --\nthis is how see-through the real in-game alert will be)",
              bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 8), justify="left"
              ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 10))

    # -- text size --
    tk.Label(body, text="Text size", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 10, "bold")).grid(row=6, column=0, sticky="w", pady=(0, 2))
    size_var = tk.StringVar(value=cfg["overlay_text_size"])

    def on_size():
        preview.set_text_size(size_var.get())

    size_row = tk.Frame(body, bg=COLORS["bg"])
    size_row.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 12))
    for key, label in (("small", "Small"), ("medium", "Medium"), ("large", "Large")):
        tk.Radiobutton(size_row, text=label, variable=size_var, value=key, command=on_size,
                        bg=COLORS["bg"], fg=COLORS["text"], selectcolor=COLORS["panel"],
                        activebackground=COLORS["bg"], activeforeground=COLORS["cyan"],
                        font=("Segoe UI", 9)).pack(side="left", padx=(0, 14))

    # -- alert colors --
    tk.Label(body, text="Alert colors", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 10, "bold")).grid(row=8, column=0, sticky="w", pady=(0, 4))
    low_color_var = tk.StringVar(value=cfg["alert_color_low"])
    empty_color_var = tk.StringVar(value=cfg["alert_color_empty"])

    color_row = tk.Frame(body, bg=COLORS["bg"])
    color_row.grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 12))

    def make_color_picker(parent, label_text, var, dialog_title):
        block = tk.Frame(parent, bg=COLORS["bg"])
        block.pack(side="left", padx=(0, 20))
        tk.Label(block, text=label_text, bg=COLORS["bg"], fg=COLORS["text_dim"],
                  font=("Segoe UI", 8)).pack(anchor="w")
        row = tk.Frame(block, bg=COLORS["bg"])
        row.pack(pady=(2, 0))
        swatch = tk.Canvas(row, width=22, height=22, bg=var.get(),
                            highlightthickness=1, highlightbackground=COLORS["line"])
        swatch.pack(side="left", padx=(0, 6))

        def pick():
            result = colorchooser.askcolor(color=var.get(), title=dialog_title, parent=win)
            if result and result[1]:
                var.set(result[1])
                swatch.configure(bg=result[1])
                show_demo()

        RoundedButton(row, text="Choose", command=pick, bg=COLORS["panel"], fg=COLORS["text"],
                      width=70, height=26, radius=7, font=("Segoe UI", 8, "bold"),
                      parent_bg=COLORS["bg"]).pack(side="left")
        return swatch

    make_color_picker(color_row, "Low", low_color_var, "Pick the LOW alert color")
    make_color_picker(color_row, "Empty", empty_color_var, "Pick the EMPTY alert color")

    # -- alert duration --
    tk.Label(body, text="Alert duration", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 10, "bold")).grid(row=10, column=0, sticky="w", pady=(0, 2))
    duration_mode = tk.StringVar(value=("stay" if not cfg["alert_duration"] else "timed"))
    duration_secs = tk.StringVar(value=str(cfg["alert_duration"] or 5))

    duration_row = tk.Frame(body, bg=COLORS["bg"])
    duration_row.grid(row=11, column=0, columnspan=2, sticky="w", pady=(0, 6))
    tk.Radiobutton(duration_row, text="Stay visible while low", variable=duration_mode, value="stay",
                    bg=COLORS["bg"], fg=COLORS["text"], selectcolor=COLORS["panel"],
                    activebackground=COLORS["bg"], activeforeground=COLORS["cyan"],
                    font=("Segoe UI", 9)).pack(anchor="w")
    timed_row = tk.Frame(body, bg=COLORS["bg"])
    timed_row.grid(row=12, column=0, columnspan=2, sticky="w", pady=(0, 4))
    tk.Radiobutton(timed_row, text="Flash for", variable=duration_mode, value="timed",
                    bg=COLORS["bg"], fg=COLORS["text"], selectcolor=COLORS["panel"],
                    activebackground=COLORS["bg"], activeforeground=COLORS["cyan"],
                    font=("Segoe UI", 9)).pack(side="left")
    tk.Entry(timed_row, textvariable=duration_secs, width=4, font=("Segoe UI", 10),
              bg=COLORS["panel"], fg=COLORS["text"], insertbackground=COLORS["text"],
              bd=0, justify="center").pack(side="left", padx=6)
    tk.Label(timed_row, text="seconds, then hide", bg=COLORS["bg"], fg=COLORS["text"],
              font=("Segoe UI", 9)).pack(side="left")

    # cycle the preview between LOW and EMPTY every couple seconds so both
    # colors/states can be seen while adjusting settings -- also re-rendered
    # immediately whenever a color is changed, via show_demo()
    demo_job = [None]
    demo_state = {"is_empty": False}

    def show_demo():
        if demo_state["is_empty"]:
            preview.preview("CAPACITOR EMPTY", "0", empty_color_var.get())
        else:
            preview.preview("CAPACITOR LOW", "42", low_color_var.get())

    def demo_cycle():
        demo_state["is_empty"] = not demo_state["is_empty"]
        show_demo()
        demo_job[0] = win.after(1800, demo_cycle)

    show_demo()
    demo_job[0] = win.after(1800, demo_cycle)

    def stop_demo():
        if demo_job[0] is not None:
            try:
                win.after_cancel(demo_job[0])
            except Exception:
                pass

    outcome = {"saved": False, "back": False}

    def on_save():
        try:
            secs = int(duration_secs.get())
        except ValueError:
            secs = 5
        cfg["overlay_pos"] = preview.get_position()
        cfg["overlay_anchor"] = position_var.get()
        cfg["overlay_opacity"] = opacity_var.get()
        cfg["overlay_text_size"] = size_var.get()
        cfg["alert_color_low"] = low_color_var.get()
        cfg["alert_color_empty"] = empty_color_var.get()
        cfg["alert_duration"] = 0 if duration_mode.get() == "stay" else max(1, secs)
        save_config(cfg)
        outcome["saved"] = True
        stop_demo()
        preview.destroy()
        win.destroy()

    def on_back():
        outcome["back"] = True
        stop_demo()
        preview.destroy()
        win.destroy()

    def on_cancel():
        stop_demo()
        preview.destroy()
        win.destroy()

    btn_row = tk.Frame(win, bg=COLORS["bg"])
    btn_row.pack(pady=18)
    RoundedButton(btn_row, text="CANCEL", command=on_cancel, bg=COLORS["bg2"], fg=COLORS["text_dim"],
                  width=100, height=38, radius=9, font=("Segoe UI", 9, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)
    RoundedButton(btn_row, text="BACK", command=on_back, bg=COLORS["panel"], fg=COLORS["text"],
                  width=100, height=38, radius=9, font=("Segoe UI", 9, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)
    RoundedButton(btn_row, text="SAVE", command=on_save, bg=COLORS["cyan"], fg="#04211f",
                  width=120, height=38, radius=9, font=("Segoe UI", 10, "bold"), parent_bg=COLORS["bg"]
                  ).pack(side="left", padx=6)

    win.grab_set()
    win.wait_window()

    if outcome["back"]:
        return _calibrate_show_results(root, img, region)

    return outcome["saved"]


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
def _run_winget_install_tesseract():
    """Runs the same silent install install_tesseract.bat does, from inside
    the app itself. Returns (success, combined_output)."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            ["winget", "install", "--id", "UB-Mannheim.TesseractOCR", "-e",
             "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=300, creationflags=creationflags,
        )
        return result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return False, str(e)


def check_tesseract_or_die(parent=None):
    try:
        pytesseract.get_tesseract_version()
        return
    except Exception:
        pass

    # PATH lookup failed -- try common install locations before giving up
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            try:
                pytesseract.get_tesseract_version()
                return
            except Exception:
                continue

    # Not found anywhere -- offer to install it automatically (via Windows'
    # built-in winget package manager) so a fresh machine needs nothing but
    # this one exe, no separate install script to run first.
    owns_root = parent is None
    root = parent
    if owns_root:
        root = tk.Tk()
        root.withdraw()

    want_install = messagebox.askyesno(
        "Tesseract OCR not found",
        "This app needs Tesseract OCR (the engine that reads the numbers off "
        "your screen) and it isn't installed yet.\n\n"
        "Install it automatically now? Takes about a minute, needs an "
        "internet connection, and Windows may show a one-time permission "
        "prompt during the install.",
        parent=root,
    )

    if want_install:
        wait_win = tk.Toplevel(root)
        wait_win.title("Installing Tesseract OCR")
        wait_win.configure(bg=COLORS["bg"])
        wait_win.resizable(False, False)
        tk.Label(wait_win, text=_tracked("INSTALLING TESSERACT OCR"), bg=COLORS["bg"], fg=COLORS["cyan"],
                 font=("Segoe UI", 11, "bold")).pack(padx=30, pady=(20, 6))
        tk.Label(wait_win, text="This can take a minute. Please wait...", bg=COLORS["bg"],
                 fg=COLORS["text_dim"], font=("Segoe UI", 9)).pack(padx=30, pady=(0, 20))
        wait_win.grab_set()
        wait_win.update()

        result = {}

        def worker():
            ok, output = _run_winget_install_tesseract()
            result["ok"] = ok
            result["output"] = output

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while t.is_alive():
            root.update()
            time.sleep(0.05)
        wait_win.destroy()

        if result.get("ok"):
            # winget just installed it -- this already-running process won't
            # have picked up the PATH change, so try the default location directly
            default_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.isfile(default_path):
                pytesseract.pytesseract.tesseract_cmd = default_path
            try:
                pytesseract.get_tesseract_version()
                messagebox.showinfo("Installed", "Tesseract OCR installed successfully.", parent=root)
                if owns_root:
                    root.destroy()
                return
            except Exception:
                pass

        messagebox.showerror(
            "Install didn't complete",
            "The automatic install didn't finish successfully. Please install "
            "manually from:\nhttps://github.com/UB-Mannheim/tesseract/wiki\n\n"
            "Then run this program again.",
            parent=root,
        )
        if owns_root:
            root.destroy()
        sys.exit(1)

    msg = (
        "Tesseract OCR isn't installed (or isn't on PATH).\n\n"
        "Download and install it from:\n"
        "https://github.com/UB-Mannheim/tesseract/wiki\n\n"
        "Then run this program again."
    )
    print("\n" + msg + "\n")
    try:
        messagebox.showerror("Tesseract OCR not found", msg, parent=root)
    except Exception:
        pass
    if owns_root:
        root.destroy()
    sys.exit(1)


def _otsu_threshold(gray_img):
    """Auto-compute a good black/white split point from the image's own
    histogram instead of guessing fixed values -- faster and more reliable
    than brute-forcing several fixed thresholds."""
    arr = np.array(gray_img)
    hist, _ = np.histogram(arr, bins=256, range=(0, 255))
    total = arr.size
    sum_total = np.dot(np.arange(256), hist)
    sum_b, w_b = 0.0, 0
    best_var, best_t = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > best_var:
            best_var = var_between
            best_t = t
    return best_t


def preprocess(img: Image.Image):
    """Upscale + brightness-normalize + contrast + auto-threshold. Returns
    the black/white image that actually gets handed to the OCR engine."""
    w, h = img.size
    base = img.resize((w * 4, h * 4), Image.LANCZOS)
    gray = ImageOps.grayscale(base)
    # Brightness pass: the area behind the HUD numbers can be anything from a
    # dark starfield to a sunlit planet, and a fixed contrast boost alone
    # doesn't help when the whole crop is uniformly bright, dim, or washed
    # out. Stretching whatever range of brightness is actually PRESENT in
    # this particular crop out to the full 0-255 range makes the threshold
    # step below far more consistent across different scenes/lighting.
    # cutoff=0 (no percentile clipping) is deliberate: the digit pixels are
    # often a small minority of a mostly-background crop, and any outlier
    # cutoff would clip them away as if they were noise, unevenly per digit.
    gray = ImageOps.autocontrast(gray, cutoff=0)
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    t = _otsu_threshold(gray)
    bw = gray.point(lambda p, t=t: 255 if p > t else 0)
    return bw


def _ocr_from_bw(bw: Image.Image):
    text = pytesseract.image_to_string(
        bw, config="--psm 7 -c tessedit_char_whitelist=0123456789%"
    )
    match = re.search(r"\d{1,4}", text)
    return int(match.group()) if match else None


def ocr_numbers(img: Image.Image):
    """Preprocess a crop and OCR out every number found in it (for HUDs that
    show multiple readouts side by side, e.g. one per weapon hardpoint)."""
    bw = preprocess(img)

    for config in (
        "--psm 6 -c tessedit_char_whitelist=0123456789% ",  # uniform block -- best for several numbers with gaps
        "--psm 11 -c tessedit_char_whitelist=0123456789%",  # sparse text, no particular order
        "--psm 7 -c tessedit_char_whitelist=0123456789%",   # single line
    ):
        text = pytesseract.image_to_string(bw, config=config)
        nums = re.findall(r"\d{1,4}", text)
        if nums:
            return [int(n) for n in nums]

    # fallback: try inverted threshold (handles bright-on-dark vs dark-on-bright)
    bw_inv = bw.point(lambda p: 255 - p)
    text = pytesseract.image_to_string(
        bw_inv, config="--psm 6 -c tessedit_char_whitelist=0123456789%"
    )
    nums = re.findall(r"\d{1,4}", text)
    return [int(n) for n in nums] if nums else None


def ocr_number(img: Image.Image):
    """Fast single-number OCR for one already-isolated crop (used per-group
    after find_number_groups has split the numbers apart). Tries the OCR
    mode best suited for one isolated word first; only falls back once."""
    bw = preprocess(img)
    for config in (
        "--psm 8 -c tessedit_char_whitelist=0123456789%",   # single word -- best fit here
        "--psm 7 -c tessedit_char_whitelist=0123456789%",   # single line
    ):
        text = pytesseract.image_to_string(bw, config=config)
        match = re.search(r"\d{1,4}", text)
        if match:
            return int(match.group())

    bw_inv = bw.point(lambda p: 255 - p)
    text = pytesseract.image_to_string(
        bw_inv, config="--psm 8 -c tessedit_char_whitelist=0123456789%"
    )
    match = re.search(r"\d{1,4}", text)
    return int(match.group()) if match else None


def _otsu_split_1d(values):
    """Given a handful of values, find the split point that best separates
    them into two clusters (max between-cluster variance) -- used to tell
    'gap within one number' apart from 'gap between separate numbers'
    without hardcoding a pixel count."""
    vals = sorted(values)
    if len(vals) < 2:
        return vals[0] if vals else 0
    best_t, best_var = vals[0], -1.0
    for i in range(1, len(vals)):
        left, right = vals[:i], vals[i:]
        m_l = sum(left) / len(left)
        m_r = sum(right) / len(right)
        var_between = len(left) * len(right) * (m_l - m_r) ** 2
        if var_between > best_var:
            best_var = var_between
            best_t = (left[-1] + right[0]) / 2
    return best_t


def find_number_groups(img: Image.Image, min_group_px=6):
    """Locate separate clusters of bright pixels along the x-axis (i.e.
    individual numbers). First finds every raw stroke/character run, then
    decides -- adaptively, from the actual gaps found in THIS image -- which
    gaps are just spacing within one number vs. real separation between
    different numbers. This avoids hardcoding a pixel count that would only
    be right at one particular resolution."""
    gray = ImageOps.grayscale(img)
    arr = np.array(gray)
    t = _otsu_threshold(gray)
    mask = arr > t
    fg_cols = mask.any(axis=0)
    w = len(fg_cols)

    # raw contiguous foreground runs -- could be individual digit strokes
    runs = []
    x = 0
    while x < w:
        if not fg_cols[x]:
            x += 1
            continue
        start = x
        while x < w and fg_cols[x]:
            x += 1
        runs.append((start, x))

    if not runs:
        return []
    if len(runs) == 1:
        s, e = runs[0]
        return [(s, e)] if (e - s) >= min_group_px else []

    gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
    if len(set(gaps)) <= 1:
        # every gap is identical -- nothing to separate on, treat it as one group
        gap_threshold = max(gaps) + 1
    else:
        gap_threshold = _otsu_split_1d(gaps)

    groups = []
    cur_start, cur_end = runs[0]
    for i in range(1, len(runs)):
        gap = runs[i][0] - cur_end
        if gap <= gap_threshold:
            cur_end = runs[i][1]
        else:
            groups.append((cur_start, cur_end))
            cur_start, cur_end = runs[i]
    groups.append((cur_start, cur_end))

    return [g for g in groups if g[1] - g[0] >= min_group_px]


def read_numbers_from_crop(img: Image.Image, expected_count=None):
    """Read every number in a crop by splitting it at real background gaps
    between digit groups, then OCRing each group individually."""
    groups = find_number_groups(img)
    if not groups:
        nums = ocr_numbers(img)
        return nums if nums else []

    w, h = img.size
    pad = 4
    vals = []
    for start, end in groups:
        left = max(0, start - pad)
        right = min(w, end + pad)
        sub = img.crop((left, 0, right, h))
        v = ocr_number(sub)
        if v is not None:
            vals.append(v)
    return vals


def read_values(sct, cfg):
    """Grab the calibrated region and OCR out the HUD number(s), one per
    detected weapon-group readout, left to right."""
    region = cfg["region"]
    raw = sct.grab(region)
    img = Image.frombytes("RGB", raw.size, raw.rgb)
    return read_numbers_from_crop(img, None)


def _missile_text_score(text):
    """Return whether OCR text is close enough to the inbound-missile phrase.
    Explicitly rejects the collision warning that shares the same HUD slot."""
    normalized = re.sub(r"[^A-Z]", "", (text or "").upper())
    if "COLLISION" in normalized or "PROXIMITY" in normalized:
        return False
    targets = ("INBOUNDMISSILE", "INBOUNDMISSLE")
    if "INBOUND" in normalized and ("MISSILE" in normalized or "MISSLE" in normalized):
        return True
    # Whole-phrase fuzzy matching tolerates dropped/incorrect glyphs while a
    # stricter threshold prevents any generic "INBOUND ..." message from
    # being mistaken for a missile warning.
    return max((SequenceMatcher(None, normalized, target).ratio() for target in targets), default=0.0) >= 0.82


_MISSILE_TEMPLATES = {}


def _missile_shape_feature(binary_img):
    """Normalize the red-letter silhouette so it is comparable across HUD
    resolutions."""
    bbox = binary_img.getbbox()
    if not bbox:
        return None
    glyphs = binary_img.crop(bbox)
    glyphs = glyphs.resize((256, 40), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.0))
    return np.asarray(glyphs, dtype=np.float32) / 255.0


def _shape_score(binary_img, template_name="missile_template.png"):
    if template_name not in _MISSILE_TEMPLATES:
        template_path = bundled_resource(template_name)
        try:
            _MISSILE_TEMPLATES[template_name] = np.asarray(
                Image.open(template_path).convert("L"), dtype=np.float32
            ) / 255.0
        except Exception:
            _MISSILE_TEMPLATES[template_name] = False
    template = _MISSILE_TEMPLATES[template_name]
    if template is False:
        return 0.0
    feature = _missile_shape_feature(binary_img)
    if feature is None or feature.shape != template.shape:
        return 0.0
    a = feature.ravel()
    b = template.ravel()
    if float(a.std()) < 1e-6 or float(b.std()) < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _missile_shape_score(binary_img):
    return _shape_score(binary_img, "missile_template.png")


def _analyze_missile_crop(img):
    """Analyze one candidate crop. Shape matching handles the tiny stylized
    HUD font; text OCR remains a fallback and collision-word guard."""
    arr = np.asarray(img).astype(np.int16)
    red = arr[:, :, 0]
    green = arr[:, :, 1]
    blue = arr[:, :, 2]
    mask = (red >= 85) & ((red - green) >= 20) & ((red - blue) >= 18) & (red >= green * 1.10)

    # No meaningful red lettering: avoid an expensive Tesseract call.
    if int(mask.sum()) < max(14, int(mask.size * 0.0015)):
        return False, ""

    bw_arr = np.where(mask, 255, 0).astype(np.uint8)
    bw = Image.fromarray(bw_arr, mode="L")
    shape_score = _missile_shape_score(bw)
    if shape_score >= 0.70:
        return True, f"INBOUND MISSILE [shape {shape_score:.2f}]"

    bbox = bw.getbbox()
    if not bbox:
        return False, ""
    bw = bw.crop(bbox)
    w, h = bw.size
    # Black text on white is more reliable for Tesseract at this tiny HUD size.
    bw = ImageOps.invert(bw.resize((max(1, w * 8), max(1, h * 8)), Image.NEAREST))
    text = pytesseract.image_to_string(
        bw, config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ).strip()
    return _missile_text_score(text), text


def _runs_with_gap(bits, allowed_gap=0):
    indexes = np.flatnonzero(bits)
    if not len(indexes):
        return []
    runs = []
    start = previous = int(indexes[0])
    for value in indexes[1:]:
        value = int(value)
        if value - previous > allowed_gap + 1:
            runs.append((start, previous + 1))
            start = value
        previous = value
    runs.append((start, previous + 1))
    return runs


def _scan_screen_for_missile(img):
    """Find red text bands anywhere on screen, then compare each phrase-sized
    candidate to the inbound-missile silhouette. This follows cockpit-fixed
    HUD text as head movement changes its screen coordinates."""
    arr = np.asarray(img).astype(np.int16)
    red, green, blue = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mask = (red >= 85) & ((red - green) >= 20) & ((red - blue) >= 18) & (red >= green * 1.10)
    height, width = mask.shape
    scale = max(0.75, width / 2048.0)
    row_runs = _runs_with_gap(mask.sum(axis=1) >= max(3, int(3 * scale)), allowed_gap=max(1, int(2 * scale)))
    best_score = 0.0
    best_inbound = 0.0
    best_missile = 0.0
    best_box = None

    def word_scores(glyphs):
        inbound_variants = [
            glyphs.crop((0, 0, max(1, int(glyphs.width * fraction)), glyphs.height))
            for fraction in (0.35, 0.40, 0.45, 0.50, 0.55)
        ]
        missile_variants = [
            glyphs.crop((int(glyphs.width * start), 0, max(int(glyphs.width * start) + 1,
                        int(glyphs.width * end)), glyphs.height))
            for start in (0.32, 0.38, 0.44)
            for end in (0.74, 0.80, 0.86, 0.92)
        ]
        inbound_score = max((_shape_score(v, "inbound_template.png") for v in inbound_variants), default=0.0)
        missile_score = max((_shape_score(v, "missile_word_template.png") for v in missile_variants), default=0.0)
        return inbound_score, missile_score

    for y1, y2 in row_runs:
        band_h = y2 - y1
        # A row projection can be made artificially tall when unrelated red
        # HUD markers occupy the same vertical range elsewhere on screen.
        # Keep that wider band, split it horizontally, then judge the actual
        # glyph box below instead of discarding the real warning early.
        if band_h < max(3, int(5 * scale)) or band_h > int(90 * scale):
            continue
        py1, py2 = max(0, y1 - 2), min(height, y2 + 2)
        stripe = mask[py1:py2]
        column_runs = _runs_with_gap(stripe.any(axis=0), allowed_gap=max(5, int(10 * scale)))
        for x1, x2 in column_runs:
            candidate_w = x2 - x1
            if candidate_w < int(55 * scale) or candidate_w > int(420 * scale):
                continue
            px1, px2 = max(0, x1 - 3), min(width, x2 + 3)
            candidate = Image.fromarray(
                np.where(mask[py1:py2, px1:px2], 255, 0).astype(np.uint8), mode="L"
            )
            # Icons and missile counts sit immediately to the right of the
            # words and change by missile type. Compare the full candidate and
            # several left-side text prefixes, keeping only the best wording
            # match so those variable symbols never become requirements.
            candidate_box = candidate.getbbox()
            glyphs = candidate.crop(candidate_box) if candidate_box else candidate
            if glyphs.width / max(1, glyphs.height) < 3.5:
                continue
            variants = [glyphs]
            for fraction in (0.70, 0.75, 0.80, 0.85, 0.90):
                variants.append(glyphs.crop((0, 0, max(1, int(glyphs.width * fraction)), glyphs.height)))
            score = max((_missile_shape_score(variant) for variant in variants), default=0.0)
            inbound_score, missile_score = word_scores(glyphs)
            best_inbound = max(best_inbound, inbound_score)
            best_missile = max(best_missile, missile_score)
            if score > best_score:
                best_score, best_box = score, (px1, py1, px2, py2)

    # When COLLISION ALERT is directly below the missile line, projection can
    # select the lower, wider line. Check narrow bands immediately above it
    # for the independent INBOUND and MISSILE word templates.
    if best_box:
        bx1, by1, bx2, _by2 = best_box
        for band_h in range(max(10, int(12 * scale)), max(16, int(24 * scale)), max(2, int(3 * scale))):
            sy1, sy2 = max(0, by1 - band_h - 2), max(1, by1 - 2)
            above = Image.fromarray(np.where(mask[sy1:sy2, bx1:bx2], 255, 0).astype(np.uint8), mode="L")
            above_box = above.getbbox()
            if above_box:
                inbound_score, missile_score = word_scores(above.crop(above_box))
                best_inbound = max(best_inbound, inbound_score)
                best_missile = max(best_missile, missile_score)

    combined_words = min(best_inbound, best_missile)
    decision_score = max(best_score, combined_words)
    if best_score >= 0.48 or (best_inbound >= 0.50 and best_missile >= 0.38):
        return True, (f"INBOUND MISSILE [screen {decision_score:.2f} phrase={best_score:.2f} "
                      f"inbound={best_inbound:.2f} missile={best_missile:.2f} at {best_box}]")
    return False, (f"best checks phrase={best_score:.2f} inbound={best_inbound:.2f} "
                   f"missile={best_missile:.2f}" if best_box else "no red phrase candidate")


def read_missile_warning(sct, cfg):
    """Scan the entire game monitor so cockpit-fixed text can move on screen."""
    if not cfg.get("missile_enabled"):
        return False, ""
    try:
        monitor = sct.monitors[1]
    except Exception:
        return False, "monitor unavailable"
    raw = sct.grab(monitor)
    img = Image.frombytes("RGB", raw.size, raw.rgb)
    return _scan_screen_for_missile(img)


# ---------------------------------------------------------------------------
# Ship-aware capacitor threshold
# ---------------------------------------------------------------------------
class ShipScaler:
    """Scales the low-capacitor alert to whatever ship you're flying.

    The threshold you set is really "this fraction of FULL on the ship I set
    it up on" (reference_full). Each weapon group's own full capacity is
    learned from the HUD itself: a fully recharged capacitor stops moving, so
    a reading that holds still for PLATEAU_SECS *is* that ship's full value.
    Swap from a 75-capacitor ship to a 25-capacitor one and an alert set at 25
    becomes ~8 instead of flashing LOW at full charge.

    When the numbers vanish for SWAP_GAP seconds (leaving/entering a ship),
    what it knew is thrown away and it re-learns, using the highest reading
    seen so far as a provisional "full" until the real plateau shows up.
    With one ship (full == reference_full) the limit equals the plain
    threshold, i.e. behavior is identical to a fixed threshold.
    """

    PLATEAU_SECS = 5.0
    SWAP_GAP = 8.0

    def __init__(self, threshold, reference_full=None, enabled=True):
        self.threshold = float(threshold)
        self.reference_full = float(reference_full) if reference_full else None
        self.enabled = bool(enabled)
        self.groups = {}              # group index -> learned state
        self._last_seen = None
        self.learned_reference = None  # set once if the reference had to be learned; the app saves it

    def _g(self, i):
        return self.groups.setdefault(
            i, {"full": None, "provisional": None, "pval": None, "psince": 0.0, "seen": 0.0})

    def no_reading(self, now):
        """Call when a poll found no numbers at all. Returns True on the poll
        where it decides you've swapped ships (so the caller can also drop the
        old ship's smoothed readings)."""
        if self._last_seen is not None and now - self._last_seen >= self.SWAP_GAP:
            for g in self.groups.values():
                g["full"], g["provisional"], g["pval"] = None, 0.0, None
            self._last_seen = None   # reset once per gap, not on every empty poll
            return True
        return False

    def update(self, i, value, now):
        """Feed one weapon group's smoothed reading; returns the limit to compare it to."""
        self._last_seen = now
        g = self._g(i)
        g["seen"] = now
        if g["provisional"] is not None:
            g["provisional"] = max(g["provisional"], value)
        if value != g["pval"]:
            g["pval"], g["psince"] = value, now
        elif value > 0 and now - g["psince"] >= self.PLATEAU_SECS:
            g["full"], g["provisional"] = float(value), None   # holding still => fully recharged
        if self.reference_full is None:
            live = [x for x in self.groups.values() if now - x["seen"] < 2.0]
            if live and all(x["full"] for x in live):
                self.reference_full = max(x["full"] for x in live)
                self.learned_reference = self.reference_full
        return self.limit(i)

    def full_for(self, i):
        g = self.groups.get(i)
        if g:
            if g["full"]:
                return g["full"]
            if g["provisional"]:
                return g["provisional"]
        return self.reference_full

    def limit(self, i):
        if not self.enabled or not self.reference_full:
            return self.threshold
        full = self.full_for(i)
        return self.threshold * full / self.reference_full if full else self.threshold


# ---------------------------------------------------------------------------
# Helpers for the optional on-device missile learner (missile_ai.py)
# ---------------------------------------------------------------------------
def _load_template_array(name):
    """Bundled template as a float array (0..1), for the learner's bootstrap."""
    return np.asarray(Image.open(bundled_resource(name)).convert("L"), dtype=np.float32) / 255.0


class ScreenGrabber:
    """Context manager the learner's own thread uses to grab the game monitor
    (mss handles are per-thread, so it can't share the other loops' handle)."""

    def __enter__(self):
        self.sct = mss.MSS()
        return self

    def grab(self):
        raw = self.sct.grab(self.sct.monitors[1])
        return Image.frombytes("RGB", raw.size, raw.rgb)

    def __exit__(self, *exc):
        try:
            self.sct.close()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Overlay window
# ---------------------------------------------------------------------------
OUTLINE_OFFSETS = [(-1, 0), (1, 0), (0, -1), (0, 1)]      # thin edge for crispness
EXTRUSION_STEPS = [(3, 0.28), (2, 0.42), (1, 0.58)]         # (px offset, shade factor) -- far-to-near


class Overlay:
    """The click-through HUD alert window. Also doubles as the live preview
    used by the calibration wizard's placement/style step -- in that mode
    click-through is left off (so it's draggable) and styles can be changed
    on the fly via set_opacity/set_text_size."""

    def __init__(self, parent, cfg, draggable=False):
        self.cfg = cfg
        self.draggable = draggable
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)

        if draggable:
            # No color-key transparency here -- Windows treats color-keyed
            # pixels as outside the window for click purposes, so only the
            # thin text glyphs would be draggable. Use a real, visible panel
            # instead so the whole box can be grabbed anywhere.
            bg_color = COLORS["bg2"]
            self.win.configure(bg=bg_color)
            # Keep this window fully opaque -- sub-100% window alpha can make
            # a layered window stop reliably receiving mouse clicks on
            # Windows, which would silently break dragging. The opacity
            # slider still saves correctly; it's just not previewed via real
            # window transparency here, only on the real overlay later.
        else:
            bg_color = "black"
            self.win.configure(bg="black")
            try:
                self.win.attributes("-transparentcolor", "black")
            except tk.TclError:
                pass  # not supported on this platform (e.g. non-Windows); overlay still works, just opaque
            self._set_opacity_raw(cfg.get("overlay_opacity", 100))

        self.w, self.h = 460, 100
        self.canvas = tk.Canvas(self.win, width=self.w, height=self.h, bg=bg_color, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        if draggable:
            self.canvas.create_rectangle(1, 1, self.w - 1, self.h - 1,
                                          outline=COLORS["line"], width=1)
            self.canvas.create_text(self.w // 2, 18, text="drag me anywhere",
                                     fill=COLORS["text_dim"], font=("Segoe UI", 8))

        # No background panel on the real overlay -- fully transparent. Each
        # piece of text (the warning word, a "-" separator, the reading) is
        # built from three layers, back to front: a few offset copies in
        # progressively darker shades of the alert color (giving the letters
        # a sense of thickness/depth, like raised lettering), a thin dark
        # edge for crispness, and the bright fill on top.
        self.status_extrusion = [self.canvas.create_text(0, 0, text="", fill="black") for _ in EXTRUSION_STEPS]
        self.status_outline = [self.canvas.create_text(0, 0, text="", fill="black") for _ in OUTLINE_OFFSETS]
        self.status_main = self.canvas.create_text(0, 0, text="", fill="black")
        self.sep_extrusion = [self.canvas.create_text(0, 0, text="", fill="black") for _ in EXTRUSION_STEPS]
        self.sep_outline = [self.canvas.create_text(0, 0, text="", fill="black") for _ in OUTLINE_OFFSETS]
        self.sep_main = self.canvas.create_text(0, 0, text="", fill="black")
        self.pct_extrusion = [self.canvas.create_text(0, 0, text="", fill="black") for _ in EXTRUSION_STEPS]
        self.pct_outline = [self.canvas.create_text(0, 0, text="", fill="black") for _ in OUTLINE_OFFSETS]
        self.pct_main = self.canvas.create_text(0, 0, text="", fill="black")
        self.set_text_size(cfg.get("overlay_text_size", "medium"))

        self._place(cfg)

        if draggable:
            self.canvas.configure(cursor="fleur")
            self.canvas.bind("<ButtonPress-1>", self._drag_start)
            self.canvas.bind("<B1-Motion>", self._drag_move)
            self.win.lift()
            self.win.focus_force()
        else:
            self.win.after(50, make_clickthrough, self.win)

        self.phase = "nominal"
        self._tick_count = 0
        self._startup_ticks = 14  # ~2s at the 150ms ui_tick rate -- confirms it's alive & where it sits
        self._alert_started_at = None

    def _place(self, cfg):
        anchor = cfg.get("overlay_anchor")
        pos = cfg.get("overlay_pos")
        if pos and (not anchor or anchor == "custom"):
            x, y = pos["x"], pos["y"]
        else:
            x, y = self._anchor_position(anchor or cfg.get("overlay_corner", "center-low"))
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _anchor_position(self, anchor):
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        margin = 24
        center_x = (screen_w - self.w) // 2
        center_y = (screen_h - self.h) // 2
        positions = {
            "center-high": (center_x, max(margin, int(screen_h * 0.22) - self.h // 2)),
            "center-low": (center_x, min(screen_h - self.h - margin, int(screen_h * 0.72) - self.h // 2)),
            "center-left": (margin, center_y),
            "center": (center_x, center_y),
            "center-right": (screen_w - self.w - margin, center_y),
            # Backward-compatible legacy choices.
            "top-left": (margin, margin),
            "top-center": (center_x, margin),
            "top-right": (screen_w - self.w - margin, margin),
        }
        return positions.get(anchor, positions["center-low"])

    def snap_to(self, anchor):
        x, y = self._anchor_position(anchor)
        self.cfg["overlay_anchor"] = anchor
        self.win.geometry(f"+{x}+{y}")

    def _drag_start(self, e):
        self._drag_offset = (e.x, e.y)

    def _drag_move(self, e):
        x = self.win.winfo_x() + (e.x - self._drag_offset[0])
        y = self.win.winfo_y() + (e.y - self._drag_offset[1])
        self.win.geometry(f"+{x}+{y}")
        if hasattr(self, "on_drag"):
            self.on_drag()

    def get_position(self):
        return {"x": self.win.winfo_x(), "y": self.win.winfo_y()}

    def _set_opacity_raw(self, pct):
        try:
            self.win.attributes("-alpha", max(0.1, min(1.0, pct / 100)))
        except tk.TclError:
            pass

    def set_opacity(self, pct):
        self.cfg["overlay_opacity"] = pct
        if not self.draggable:
            self._set_opacity_raw(pct)

    def set_text_size(self, size_key):
        self.cfg["overlay_text_size"] = size_key
        sizes = SIZE_MAP.get(size_key, SIZE_MAP["medium"])
        self._label_font = ("Segoe UI", sizes["label"], "normal")
        self._num_font = ("Segoe UI", sizes["number"], "normal")
        for item in self.status_extrusion + self.status_outline + [self.status_main]:
            self.canvas.itemconfig(item, font=self._label_font)
        for item in self.sep_extrusion + self.sep_outline + [self.sep_main]:
            self.canvas.itemconfig(item, font=self._label_font)
        for item in self.pct_extrusion + self.pct_outline + [self.pct_main]:
            self.canvas.itemconfig(item, font=self._num_font)
        self._layout()

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _place_group(self, extrusion_items, outline_items, main_item, cx, cy):
        self.canvas.coords(main_item, cx, cy)
        for item in outline_items:
            self.canvas.coords(item, cx, cy)
        for (offset, _shade_factor), item in zip(EXTRUSION_STEPS, extrusion_items):
            self.canvas.coords(item, cx + offset, cy + offset)

    def _color_group(self, extrusion_items, outline_items, main_item, fill, outline_fill, extrusion_base):
        self.canvas.itemconfig(main_item, fill=fill)
        for item in outline_items:
            self.canvas.itemconfig(item, fill=outline_fill)
        for (_offset, factor), item in zip(EXTRUSION_STEPS, extrusion_items):
            self.canvas.itemconfig(item, fill=_shade(extrusion_base, factor) if extrusion_base != "black" else "black")

    def _text_group(self, extrusion_items, outline_items, main_item, text):
        self.canvas.itemconfig(main_item, text=text)
        for item in outline_items:
            self.canvas.itemconfig(item, text=text)
        for item in extrusion_items:
            self.canvas.itemconfig(item, text=text)

    def _layout(self):
        """Center label - number as one horizontal group, separated by a
        plain '-' rather than a graphic divider."""
        cy = self.h // 2
        self.canvas.update_idletasks()
        lb = self.canvas.bbox(self.status_main)
        nb = self.canvas.bbox(self.pct_main)
        sb = self.canvas.bbox(self.sep_main)
        lw = (lb[2] - lb[0]) if lb else 0
        nw = (nb[2] - nb[0]) if nb else 0
        sw = (sb[2] - sb[0]) if sb else 0

        if lw and nw:
            gap = 10
            total = lw + gap + sw + gap + nw
            start = self.w // 2 - total / 2
            label_cx = start + lw / 2
            sep_cx = start + lw + gap + sw / 2
            num_cx = start + lw + gap + sw + gap + nw / 2
        else:
            label_cx, sep_cx, num_cx = self.w // 2, -50, -50

        self._place_group(self.status_extrusion, self.status_outline, self.status_main, label_cx, cy)
        self._place_group(self.sep_extrusion, self.sep_outline, self.sep_main, sep_cx, cy)
        self._place_group(self.pct_extrusion, self.pct_outline, self.pct_main, num_cx, cy)

    def _apply(self, label, sub, color, visible):
        if visible:
            fill, outline_fill, extrusion_base = color, "#04070a", color
            label_out = label
            sep_out = "-" if (label and sub) else ""
            sub_out = sub
        else:
            fill = outline_fill = extrusion_base = "black"
            # All three pieces of text must actually be emptied here, not just
            # recolored. Leaving the number's real text content in place while
            # only changing its color meant _layout() (which measures text
            # width to center everything) saw a zero-width label but a
            # nonzero-width number every "off" blink frame, and forcibly
            # knocked the number off to the side (-50) as a result -- then
            # snapped it straight back on the very next "on" frame. That
            # repeated jump, on every single blink, is what showed up as the
            # letters visibly moving/flickering.
            label_out = ""
            sep_out = ""
            sub_out = ""

        self._text_group(self.status_extrusion, self.status_outline, self.status_main, label_out)
        self._text_group(self.sep_extrusion, self.sep_outline, self.sep_main, sep_out)
        self._text_group(self.pct_extrusion, self.pct_outline, self.pct_main, sub_out)
        self._color_group(self.status_extrusion, self.status_outline, self.status_main, fill, outline_fill, extrusion_base)
        self._color_group(self.sep_extrusion, self.sep_outline, self.sep_main, fill, outline_fill, extrusion_base)
        self._color_group(self.pct_extrusion, self.pct_outline, self.pct_main, fill, outline_fill, extrusion_base)
        self._layout()

    def preview(self, label, sub, color):
        """Static preview for the calibration wizard -- no blinking, no
        duration logic, just shows exactly what the real alert looks like."""
        self._apply(label, sub, color, True)

    def set_value(self, val, confirmed):
        if self._startup_ticks > 0:
            self._startup_ticks -= 1
            self._apply("OVERLAY ONLINE", "", COLORS["cyan"], True)
            return

        if val is None:
            self._apply("", "", COLORS["cyan"], False)
            self._alert_started_at = None
            return

        threshold = self.cfg["low_threshold_value"]
        if not confirmed:
            phase, color, label = "nominal", COLORS["cyan"], ""
        elif val <= 0:
            phase, color, label = "empty", self.cfg.get("alert_color_empty", COLORS["red"]), "CAPACITOR EMPTY"
        else:
            phase, color, label = "low", self.cfg.get("alert_color_low", COLORS["amber"]), "CAPACITOR LOW"

        # alert-duration: once confirmed-low starts, optionally auto-hide
        # after N seconds so it doesn't sit on screen indefinitely. Resets
        # the moment it's no longer confirmed, so the next dip re-alerts.
        duration = self.cfg.get("alert_duration", 0) or 0
        if label:
            if self._alert_started_at is None:
                self._alert_started_at = time.time()
            elif duration > 0 and (time.time() - self._alert_started_at) > duration:
                label = ""
        else:
            self._alert_started_at = None

        self._tick_count += 1
        if phase == "empty":
            blink_on = (self._tick_count % 2) == 0        # fast flash
        elif phase == "low":
            blink_on = (self._tick_count % 4) < 2          # slower flash
        else:
            blink_on = False

        visible = bool(label) and blink_on
        sub = f"{val:.0f}" if label else ""
        self._apply(label, sub, color, visible)
        self.phase = phase


class FlareOverlay:
    """Large click-through countermeasure prompt controlled by missile OCR."""

    def __init__(self, parent):
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="black")
        try:
            self.win.attributes("-transparentcolor", "black")
        except tk.TclError:
            pass
        self.w, self.h = 520, 150
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        x = (screen_w - self.w) // 2
        y = max(20, int(screen_h * 0.34) - self.h // 2)
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")
        canvas = tk.Canvas(self.win, width=self.w, height=self.h, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        # Dark edge and warm extrusion keep the command readable against both
        # space and bright planetary backgrounds.
        for ox, oy in ((4, 4), (3, 3), (2, 2)):
            canvas.create_text(self.w // 2 + ox, self.h // 2 + oy, text="FLARE",
                               fill="#5b100d", font=("Segoe UI", 54, "bold"))
        for ox, oy in OUTLINE_OFFSETS:
            canvas.create_text(self.w // 2 + ox, self.h // 2 + oy, text="FLARE",
                               fill="#120303", font=("Segoe UI", 54, "bold"))
        canvas.create_text(self.w // 2, self.h // 2, text="FLARE", fill=COLORS["red"],
                           font=("Segoe UI", 54, "bold"))
        self.win.withdraw()
        self.visible = False
        self.win.after(50, make_clickthrough, self.win)

    def set_visible(self, visible):
        if visible and not self.visible:
            self.win.deiconify()
            self.win.lift()
        elif not visible and self.visible:
            self.win.withdraw()
        self.visible = visible

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class App:
    """Persistent control window: calibrate, start/stop monitoring, exit.
    Stays open the whole time -- the click-through overlay is a separate
    window spawned underneath it while monitoring is active."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"SC Capacitor Overlay v{__version__}")
        self.root.geometry("1100x720")
        self.root.configure(bg=COLORS["bg"])
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.overlay = None
        self.flare_overlay = None
        self.flare_test_overlay = None
        self.stop_event = None
        self.monitor_thread = None
        self.missile_thread = None
        self.state = {"val": None, "confirmed": False, "missile": False, "missile_text": ""}
        self.group_state = []
        self.last_good_time = time.time()
        self._ui_job = None
        self.threshold_for_display = None
        self.max_observed_value = None
        self.scaler = None
        self._shown_limit = None
        self.ai_assist_flag = False      # read by the learner's thread; mirrors the AI ASSIST switch
        self._modal_open = False         # hotkeys ignore key presses while a dialog has focus/grab
        self.hotkeys = None
        self.learner = None
        if missile_ai is not None:
            try:
                self.learner = missile_ai.MissileLearner(
                    os.path.join(app_dir(), "missile_ai"), _runs_with_gap, _missile_shape_score,
                    _load_template_array, on_ai_hit=self._on_ai_hit, log=print)
            except Exception as e:
                print("Missile AI unavailable:", e)

        self._build_glass_cockpit()

        self.refresh_status()
        self._setup_hotkeys()
        from updater import Updater
        self.updater = Updater(self, sys.modules[__name__])
        if self.learner is not None:
            self.root.after(3000, self._kick_idle_training)
            self._ai_status_tick()

    def _build_glass_cockpit(self):
        from glass_skin import build
        build(self, sys.modules[__name__])

    # -- global hotkeys (work even while the game has focus) ---------------
    def _hotkey_spec(self, key, cfg):
        return (cfg or {}).get(key, DEFAULT_CONFIG[key])

    def _setup_hotkeys(self):
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        if not cfg.get("hotkeys_enabled", DEFAULT_CONFIG["hotkeys_enabled"]):
            self.hotkey_label.config(text="Hotkeys disabled (set hotkeys_enabled: true in config.json)")
            return

        specs = [
            ("hotkey_toggle_monitoring", "start/stop", lambda: self.root.after(0, self._hk_toggle_monitoring)),
            ("hotkey_false_alarm", "false alarm", lambda: self.root.after(0, self._hk_false_alarm)),
            ("hotkey_test_flare", "test flare", lambda: self.root.after(0, self._hk_test_flare)),
            ("hotkey_ai_assist", "ai assist", lambda: self.root.after(0, self._hk_toggle_ai_assist)),
        ]
        bindings, shown = {}, []
        for i, (key, label, cb) in enumerate(specs, start=1):
            raw = self._hotkey_spec(key, cfg)
            parsed = parse_hotkey(raw)
            if parsed is None:
                if raw:
                    shown.append(f"{raw} {label} - invalid, skipped")
                else:
                    shown.append(f"(unset) {label}")
                continue
            mods, vk = parsed
            bindings[i] = (mods, vk, cb)
            shown.append(f"{raw} {label}")

        if not bindings:
            self.hotkey_label.config(text="No valid hotkeys configured.")
            return
        if os.name != "nt":
            self.hotkey_label.config(text="HOTKEYS (inactive -- needs Windows)  " + "  •  ".join(shown))
            return

        self.hotkeys = HotkeyManager(bindings, log=print)
        self.hotkeys.start()
        self.root.after(500, lambda: self._report_hotkey_registration(shown))

    def _report_hotkey_registration(self, shown):
        failed = set(self.hotkeys.failed_ids) if self.hotkeys else set()
        if failed:
            shown = [s + ("  [in use by another app]" if i in failed else "")
                    for i, s in enumerate(shown, start=1)]
        self.hotkey_label.config(text="HOTKEYS  " + "  •  ".join(shown))

    def _hk_toggle_monitoring(self):
        if self._modal_open or not os.path.exists(CONFIG_PATH):
            return
        self.toggle_monitoring()

    def _hk_false_alarm(self):
        if self._modal_open:
            return
        self.ai_false_alarm()

    def _hk_test_flare(self):
        if self._modal_open or not os.path.exists(CONFIG_PATH):
            return
        self.test_flare()

    def _hk_toggle_ai_assist(self):
        if self._modal_open or not (self.learner and self.learner.is_ready()):
            return
        self.ai_assist_toggle.set(not self.ai_assist_toggle.get())
        self.toggle_ai_assist()

    def open_hotkey_settings(self):
        """A small settings dialog: click-to-record a binding for each
        action, live conflict checking, and Save re-registers the hotkeys
        immediately -- no restart needed."""
        if not os.path.exists(CONFIG_PATH):
            messagebox.showinfo("Calibrate first", "Run CALIBRATE once before setting up hotkeys.",
                                parent=self.root)
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        self._modal_open = True
        win = tk.Toplevel(self.root)
        win.title("Hotkey settings")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text="HOTKEY SETTINGS", bg=COLORS["bg"], fg=COLORS["cyan"],
                  font=("Segoe UI", 12, "bold")).pack(pady=(18, 4))
        tk.Label(win, text="Click RECORD, then press a key combo. Esc cancels a recording.\n"
                            "These work even while Star Citizen has focus.",
                  bg=COLORS["bg"], fg=COLORS["text_dim"], font=("Segoe UI", 9), justify="center"
                  ).pack(pady=(0, 14))

        enabled_var = tk.BooleanVar(value=cfg.get("hotkeys_enabled", DEFAULT_CONFIG["hotkeys_enabled"]))
        enable_row = tk.Frame(win, bg=COLORS["bg"])
        enable_row.pack(padx=28, pady=(0, 10), fill="x")
        enable_toggle = ToggleSwitch(enable_row, "ENABLE HOTKEYS", bg=COLORS["bg"], parent_bg=COLORS["bg"],
                                     initial=enabled_var.get(), command=lambda: enabled_var.set(enable_toggle.get()))
        enable_toggle.pack(anchor="w")

        rows_frame = tk.Frame(win, bg=COLORS["bg"])
        rows_frame.pack(padx=28, pady=(0, 6))
        actions = [
            ("hotkey_toggle_monitoring", "Start / stop"),
            ("hotkey_false_alarm", "False alarm"),
            ("hotkey_test_flare", "Test flare"),
            ("hotkey_ai_assist", "AI assist"),
        ]
        rows = {}
        for key, label in actions:
            row = HotkeyCapture(rows_frame, label, self._hotkey_spec(key, cfg), bg=COLORS["bg"])
            row.pack(pady=4, fill="x")
            rows[key] = row

        conflict_label = tk.Label(win, text="", bg=COLORS["bg"], fg=COLORS["red"], font=("Segoe UI", 8))
        conflict_label.pack(pady=(4, 0))

        def check_conflicts():
            specs = {k: r.get() for k, r in rows.items() if r.get()}
            seen, dupes = {}, set()
            for k, spec in specs.items():
                if spec in seen:
                    dupes.add(spec)
                seen[spec] = k
            if dupes:
                conflict_label.config(text="Two actions are using the same combo: " + ", ".join(sorted(dupes)))
            else:
                conflict_label.config(text="")
            return not dupes

        outcome = {"saved": False}

        def on_save():
            if not check_conflicts():
                return
            cfg2 = dict(cfg)
            cfg2["hotkeys_enabled"] = bool(enabled_var.get())
            for key, row in rows.items():
                cfg2[key] = row.get()
            save_config(cfg2)
            outcome["saved"] = True
            if self.hotkeys is not None:
                self.hotkeys.stop()
                self.hotkeys = None
            win.destroy()

        def on_cancel():
            win.destroy()

        btn_row = tk.Frame(win, bg=COLORS["bg"])
        btn_row.pack(pady=18)
        RoundedButton(btn_row, text="CANCEL", command=on_cancel, bg=COLORS["bg2"], fg=COLORS["text_dim"],
                     width=100, height=38, radius=9, font=("Segoe UI", 9, "bold"), parent_bg=COLORS["bg"]
                     ).pack(side="left", padx=6)
        RoundedButton(btn_row, text="SAVE", command=on_save, bg=COLORS["cyan"], fg="#04211f",
                     width=120, height=38, radius=9, font=("Segoe UI", 10, "bold"), parent_bg=COLORS["bg"]
                     ).pack(side="left", padx=6)

        win.wait_window()
        self._modal_open = False
        if outcome["saved"]:
            self._setup_hotkeys()   # re-register with the new bindings, live

    # -- status --------------------------------------------------------
    def refresh_status(self):
        calibrated = os.path.exists(CONFIG_PATH)
        if self.monitor_thread is not None:
            text, color = "MONITORING", COLORS["cyan"]
        elif calibrated:
            text, color = "READY", COLORS["cyan"]
        else:
            text, color = "SETUP NEEDED", COLORS["amber"]
        self.status_label.config(text=text, fg=color)
        self.status_dot.config(fg=color)

        threshold = None
        anchor = "center-low"
        missile_enabled = False
        missile_region = None
        current_cfg = {}
        if calibrated:
            try:
                with open(CONFIG_PATH, "r") as f:
                    current_cfg = json.load(f)
                    threshold = current_cfg.get("low_threshold_value")
                    anchor = current_cfg.get("overlay_anchor", "custom" if current_cfg.get("overlay_pos") else "center-low")
                    missile_enabled = bool(current_cfg.get("missile_enabled"))
                    missile_region = current_cfg.get("missile_region")
            except Exception:
                threshold = None
        self.threshold_label.config(text=str(threshold) if threshold is not None else "--")
        self._shown_limit = None      # let the running scale re-apply itself on the next tick
        self.hotkey_edit_btn.set_disabled(not calibrated)
        position_names = {
            "center-high": "Center High", "center-low": "Center Low",
            "center-left": "Center Left", "center": "Center",
            "center-right": "Center Right", "custom": "Custom",
        }
        self.position_label.config(text=position_names.get(anchor, "Center Low"))
        self.missile_toggle.set(missile_enabled)
        self.missile_toggle.set_disabled(self.monitor_thread is not None or not calibrated)
        ai_on = bool(current_cfg.get("missile_ai_enabled", True)) if calibrated else False
        assist_on = bool(current_cfg.get("missile_ai_assist", False)) if calibrated else False
        self.ai_assist_flag = assist_on
        has_ai = self.learner is not None and calibrated
        self.ai_toggle.set(ai_on)
        self.ai_toggle.set_disabled(not has_ai or self.monitor_thread is not None)
        ready = bool(has_ai and self.learner.is_ready())
        self.ai_assist_toggle.set(assist_on and ready)
        self.ai_assist_toggle.set_disabled(not ready)
        self.false_alarm_btn.set_disabled(not has_ai)
        for key, button in self.position_buttons.items():
            selected = calibrated and key == anchor
            button.set_colors(bg=COLORS["cyan"] if selected else COLORS["panel"],
                              fg="#04211f" if selected else COLORS["text_dim"])
            button.set_disabled(not calibrated)

        if not calibrated:
            self.ring.set("--", 0.0, COLORS["text_dim"])
        self.toggle_btn.set_disabled(not calibrated)

    def toggle_missile_enabled(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            requested = bool(self.missile_toggle.get())
            cfg["missile_enabled"] = requested
            save_config(cfg)
        except Exception as e:
            print("Missile setting error:", e)

    # -- missile AI (optional) ---------------------------------------------
    def _update_cfg_key(self, key, value):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            cfg[key] = value
            save_config(cfg)
        except Exception as e:
            print("AI setting error:", e)

    def toggle_ai_enabled(self):
        if os.path.exists(CONFIG_PATH):
            self._update_cfg_key("missile_ai_enabled", bool(self.ai_toggle.get()))

    def toggle_ai_assist(self):
        if not os.path.exists(CONFIG_PATH):
            return
        want = bool(self.ai_assist_toggle.get())
        if want and not (self.learner and self.learner.is_ready()):
            self.ai_assist_toggle.set(False)   # can't be enabled before it has proven itself
            return
        self.ai_assist_flag = want
        self._update_cfg_key("missile_ai_assist", want)

    def ai_false_alarm(self):
        """'That FLARE popup wasn't a missile' -- the one real ground-truth
        signal the learner gets."""
        if self.learner is None:
            return
        n = self.learner.false_alarm()
        self.ai_status_label.config(
            text=f"noted - {n} marked NOT a warning" if n else "nothing recent to correct")
        self.root.after(4000, self._ai_status_tick_once)

    def _on_ai_hit(self, value):
        """Called from the learner's thread when an ASSIST detection starts/stops."""
        self.state["missile_ai"] = bool(value)

    def _ai_enabled_in_cfg(self):
        try:
            with open(CONFIG_PATH, "r") as f:
                return bool(json.load(f).get("missile_ai_enabled", True))
        except Exception:
            return False

    def _kick_idle_training(self):
        if self.learner is not None and self.monitor_thread is None and self._ai_enabled_in_cfg():
            self.learner.request_training()

    def _ai_status_tick_once(self):
        if self.learner is None:
            return
        try:
            st = self.learner.status()
            self.ai_status_label.config(text=st["text"])
            if st["ready"] != (not self.ai_assist_toggle.disabled):
                self.refresh_status()
        except Exception:
            pass

    def _ai_status_tick(self):
        self._ai_status_tick_once()
        self._ai_job = self.root.after(1500, self._ai_status_tick)

    def test_flare(self):
        """Show the popup independently so display problems can be separated
        from OCR/calibration problems."""
        if self.flare_test_overlay is None:
            self.flare_test_overlay = FlareOverlay(self.root)
        self.flare_test_overlay.set_visible(True)
        self.root.after(2200, self._hide_test_flare)

    def _hide_test_flare(self):
        if self.flare_test_overlay is not None:
            self.flare_test_overlay.set_visible(False)

    def set_overlay_position(self, anchor):
        """Persist a named snap position and move a running overlay instantly."""
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            cfg["overlay_anchor"] = anchor
            cfg["overlay_corner"] = anchor
            cfg["overlay_pos"] = None
            save_config(cfg)
            if self.overlay is not None:
                self.overlay.snap_to(anchor)
                self.overlay.cfg["overlay_anchor"] = anchor
            self.refresh_status()
        except Exception as e:
            print("Position update error:", e)

    # -- calibrate -------------------------------------------------------
    def do_calibrate(self):
        was_monitoring = self.monitor_thread is not None
        if was_monitoring:
            self.stop_monitoring()
        self._modal_open = True
        try:
            calibrate(self.root)
        except SystemExit:
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()   # full details still go to app_log.txt
            messagebox.showerror("Calibration error",
                                 f"Something went wrong during calibration:\n\n{friendly_error(e)}",
                                 parent=self.root)
        finally:
            self._modal_open = False
        self.refresh_status()

    def reset_to_defaults(self):
        """For when settings get into a weird state -- clears the saved
        region, threshold, position, colors, hotkeys, and everything else in
        config.json, so the next CALIBRATE starts completely clean. Does NOT
        touch the missile-AI training data, which is a separate, valuable
        thing to lose by accident."""
        if not os.path.exists(CONFIG_PATH):
            return
        if not messagebox.askyesno(
            "Reset to defaults?",
            "This clears your saved region, threshold, overlay position, colors, "
            "duration, and hotkeys. You'll need to run CALIBRATE again.\n\n"
            "This does NOT delete your missile-AI training data.\n\nAre you sure?",
            parent=self.root,
        ):
            return
        self.stop_monitoring()
        try:
            os.remove(CONFIG_PATH)
        except Exception as e:
            messagebox.showerror("Reset failed", friendly_error(e), parent=self.root)
            return
        self.refresh_status()
        messagebox.showinfo("Reset complete", "Settings cleared. Click CALIBRATE to set up again.",
                            parent=self.root)

    # -- monitoring --------------------------------------------------------
    def toggle_monitoring(self):
        if self.monitor_thread is None:
            self.start_monitoring()
        else:
            self.stop_monitoring()

    def start_monitoring(self):
        try:
            check_tesseract_or_die(self.root)
            cfg = load_config()
        except SystemExit:
            return
        self.cfg = cfg
        self.threshold_for_display = cfg["low_threshold_value"]
        # The first/highest valid OCR value establishes the full-scale ring
        # for this monitoring session. Any later higher reading expands the
        # scale; lower readings drain the arc proportionally.
        self.max_observed_value = None
        self._shown_limit = None
        self.overlay = Overlay(self.root, cfg)
        self.flare_overlay = FlareOverlay(self.root) if cfg.get("missile_enabled") else None
        self.stop_event = threading.Event()
        self.state = {"val": None, "confirmed": False, "missile": False, "missile_text": ""}
        self.group_state = []  # per-weapon-group tracking: [{'recent':deque,'streak':int,'val':int,'confirmed':bool}, ...]
        self.last_good_time = time.time()

        if self.learner is not None:
            self.learner.set_busy(True)          # no background training while the game is monitored
        self.monitor_thread = threading.Thread(
            target=self._capture_loop, args=(cfg, self.stop_event), daemon=True
        )
        self.monitor_thread.start()
        if cfg.get("missile_enabled"):
            self.missile_thread = threading.Thread(
                target=self._missile_loop, args=(cfg, self.stop_event), daemon=True
            )
            self.missile_thread.start()
            if self.learner is not None and cfg.get("missile_ai_enabled", True):
                self.ai_assist_flag = bool(cfg.get("missile_ai_assist", False))
                # This does its OWN independent full-screen grab, separate from
                # the detector's above -- it's collecting training samples in
                # the background, not doing live detection, so it doesn't need
                # to run anywhere near as often. Lowered from 3Hz to reduce
                # its share of screen-capture + CPU load while a game is
                # running; 1/sec is still plenty of samples over a session.
                self.learner.start(ScreenGrabber, hz=1.0, assist_getter=lambda: self.ai_assist_flag,
                                   teacher_getter=lambda: bool(self.state.get("missile", False)))

        self.toggle_btn.set_text(_tracked("STOP MONITORING"))
        self.toggle_btn.set_colors(bg=COLORS["red"], fg="#240708")
        self.refresh_status()
        print(f"\nMonitoring started. Alert threshold: below {cfg['low_threshold_value']}.")
        self._ui_tick()

    def _capture_loop(self, cfg, stop_event):
        threshold = cfg["low_threshold_value"]
        scaler = ShipScaler(threshold, cfg.get("reference_full"), cfg.get("capacitor_autoscale", True))
        self.scaler = scaler
        REQUIRED_STREAK = 2       # consecutive below-threshold reads on the SAME weapon
                                   # group before flashing -- a glitch on group A this frame
                                   # and group B next frame no longer chains into a false alert
        STALE_TIMEOUT = 2.5       # seconds of failed reads before we drop a stale value
        heartbeat_every = max(1, int(cfg.get("poll_hz", 4) * 2))  # ~every 2 seconds
        n = 0
        with mss.MSS() as sct:
            interval = 1.0 / max(1, cfg.get("poll_hz", 4))
            while not stop_event.is_set():
                try:
                    vals = read_values(sct, cfg)
                except Exception as e:
                    print("OCR error:", e)
                    vals = []

                now = time.time()
                if vals:
                    while len(self.group_state) < len(vals):
                        self.group_state.append({"recent": deque(maxlen=3), "streak": 0,
                                                   "val": None, "confirmed": False})
                    any_confirmed = False
                    for i, v in enumerate(vals):
                        gs = self.group_state[i]
                        gs["recent"].append(v)
                        ordered = sorted(gs["recent"])
                        gs["val"] = ordered[len(ordered) // 2]
                        gs["limit"] = scaler.update(i, gs["val"], now)
                        if v < gs["limit"]:
                            gs["streak"] += 1
                        else:
                            gs["streak"] = 0
                        gs["confirmed"] = gs["streak"] >= REQUIRED_STREAK
                        if gs["confirmed"]:
                            any_confirmed = True

                    self.state["val"] = min(gs["val"] for gs in self.group_state[:len(vals)])
                    lowest = min(range(len(vals)), key=lambda k: self.group_state[k]["val"])
                    self.state["limit"] = self.group_state[lowest]["limit"]     # shown on the ALERT BELOW card
                    self.state["full"] = scaler.full_for(lowest)                # scale for the ring gauge
                    if scaler.learned_reference:
                        self.state["learned_reference"] = scaler.learned_reference
                        scaler.learned_reference = None
                    self.state["confirmed"] = any_confirmed
                    self.last_good_time = now
                else:
                    if scaler.no_reading(now):
                        # Left the ship: the old ship's smoothed readings must not
                        # leak into the next ship's (they'd fake a 75 "full").
                        for gs in self.group_state:
                            gs["recent"].clear()
                            gs["streak"] = 0
                            gs["confirmed"] = False
                    if now - self.last_good_time > STALE_TIMEOUT:
                        self.state["val"] = None
                        self.state["confirmed"] = False
                        for gs in self.group_state:
                            gs["streak"] = 0
                            gs["confirmed"] = False

                n += 1
                if n % heartbeat_every == 0:
                    status = "no reading" if self.state["val"] is None else f"reading={self.state['val']:.0f}"
                    flag = " *** LOW/EMPTY ***" if self.state["confirmed"] else ""
                    missile_status = ""
                    if cfg.get("missile_enabled"):
                        seen = self.state.get("missile_text") or "no missile phrase"
                        missile_status = f" | missile_ocr={seen!r} active={self.state.get('missile', False)}"
                    print(f"[running] {status}{flag}{missile_status}")

                stop_event.wait(interval)

    def _missile_loop(self, cfg, stop_event):
        """High-speed whole-screen warning scan, deliberately independent of
        slower Tesseract capacitor reads."""
        scan_hz = max(12, int(cfg.get("missile_poll_hz", 16)))
        interval = 1.0 / scan_hz
        hits = 0
        misses = 0
        with mss.MSS() as sct:
            while not stop_event.is_set():
                started = time.perf_counter()
                try:
                    missile_hit, missile_text = read_missile_warning(sct, cfg)
                except Exception as e:
                    print("Missile scan error:", e)
                    missile_hit, missile_text = False, ""

                self.state["missile_text"] = missile_text
                if missile_hit:
                    hits += 1
                    misses = 0
                    score_match = re.search(r"\[screen ([0-9.]+)", missile_text or "")
                    confidence = float(score_match.group(1)) if score_match else 0.0
                    required_hits = 1 if confidence >= 0.82 else 2
                    if hits >= required_hits:
                        self.state["missile"] = True
                else:
                    hits = 0
                    misses += 1
                    if misses >= 3:
                        self.state["missile"] = False

                remaining = interval - (time.perf_counter() - started)
                if remaining > 0:
                    stop_event.wait(remaining)

    def _ui_tick(self):
        if self.overlay is None:
            return
        self.overlay.set_value(self.state["val"], self.state["confirmed"])
        if self.flare_overlay is not None:
            self.flare_overlay.set_visible(bool(self.state.get("missile")) or bool(self.state.get("missile_ai")))
        val = self.state["val"]
        confirmed = self.state["confirmed"]

        limit = self.state.get("limit")
        if limit is not None and self.threshold_for_display:
            shown = f"{limit:.0f}" if abs(limit - self.threshold_for_display) >= 0.5 else str(self.threshold_for_display)
            if shown != getattr(self, "_shown_limit", None):
                self._shown_limit = shown
                self.threshold_label.config(text=shown)
        learned = self.state.pop("learned_reference", None)
        if learned:                   # first plateau on a config that had no reference yet -> remember it
            self._update_cfg_key("reference_full", learned)

        if val is None:
            self.ring.set("--", 0.0, COLORS["text_dim"])
        else:
            if val >= 0 and (self.max_observed_value is None or val > self.max_observed_value):
                self.max_observed_value = val
            full_scale = max(1.0, self.max_observed_value or val or 1.0)
            learned_full = self.state.get("full")
            if learned_full:          # this ship's own full capacity, learned from the HUD
                full_scale = max(1.0, float(learned_full))
            fraction = max(0.0, min(1.0, val / full_scale))
            if confirmed:
                color = COLORS["red"] if val <= 0 else COLORS["amber"]
            else:
                color = COLORS["cyan"]
            self.ring.set(f"{val:.0f}", fraction, color)

        self._ui_job = self.root.after(50, self._ui_tick)

    def stop_monitoring(self):
        if self.monitor_thread is None:
            return
        self.stop_event.set()
        self.monitor_thread.join(timeout=2)
        self.monitor_thread = None
        if self.missile_thread is not None:
            self.missile_thread.join(timeout=2)
            self.missile_thread = None
        if self.learner is not None:
            self.learner.stop()

        if self._ui_job is not None:
            try:
                self.root.after_cancel(self._ui_job)
            except Exception:
                pass
            self._ui_job = None

        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None
        if self.flare_overlay is not None:
            self.flare_overlay.destroy()
            self.flare_overlay = None

        self.ring.set("--", 0.0, COLORS["text_dim"])
        self.max_observed_value = None
        self._shown_limit = None
        self.toggle_btn.set_text(_tracked("START MONITORING"))
        self.toggle_btn.set_colors(bg=COLORS["cyan"], fg="#04211f")
        self.refresh_status()
        if self.learner is not None:
            self.learner.set_busy(False)
            if self._ai_enabled_in_cfg():
                self.learner.request_training()  # learn from the session, now that the game is idle
        print("Monitoring stopped.")

    # -- lifecycle --------------------------------------------------------
    def on_close(self):
        if getattr(self, "updater", None):
            self.updater.close()
        self.stop_monitoring()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        if getattr(self, "_ai_job", None) is not None:
            try:
                self.root.after_cancel(self._ai_job)
            except Exception:
                pass
        if self.learner is not None:
            self.learner.shutdown()
        if self.flare_test_overlay is not None:
            self.flare_test_overlay.destroy()
            self.flare_test_overlay = None
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    # Run the whole process at a lower OS scheduling priority than normal.
    # This app periodically does real, bursty work (screen capture, spawning
    # the Tesseract OCR engine as a subprocess many times a second) while
    # Star Citizen is running and already using most of the machine's
    # resources. BELOW_NORMAL tells Windows to prefer the game's own threads
    # whenever the two are competing for the same CPU time, rather than this
    # app's background loops causing frame-time stalls in the game itself.
    if os.name == "nt":
        try:
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true", help="Run calibration wizard, then exit")
    parser.add_argument("--run", action="store_true", help="Open the control window and start monitoring immediately")
    args = parser.parse_args()

    try:
        if args.calibrate:
            calibrate(None)
        else:
            app = App()
            if args.run:
                app.root.after(200, app.start_monitoring)
            app.run()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        err_text = traceback.format_exc()
        log_path = os.path.join(app_dir(), "error_log.txt")
        try:
            with open(log_path, "w") as f:
                f.write(err_text)
        except Exception:
            log_path = None
        print("\n" + "=" * 60)
        print("SC Capacitor Overlay hit an error and had to stop:")
        print("=" * 60)
        print(err_text)
        # A windowed build (the normal, shipped exe) has no console and no
        # stdin at all -- the old input()-based prompt here would silently
        # fail, meaning a crash was completely invisible to anyone actually
        # using the app. A messagebox is the one thing guaranteed to show up.
        summary = friendly_error(e)
        msg = f"SC Capacitor Overlay hit a problem and had to stop:\n\n{summary}"
        if log_path:
            msg += f"\n\nFull details were saved to:\n{log_path}"
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("SC Capacitor Overlay - error", msg)
            r.destroy()
        except Exception:
            pass
