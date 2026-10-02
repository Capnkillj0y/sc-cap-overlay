"""Non-exclusive Windows shortcuts: observe key state without reserving keys."""
import ctypes
import os


class HotkeyManager:
    def __init__(self, bindings, scheduler, log=print, read_state=None):
        self.bindings = bindings
        self.scheduler = scheduler
        self.log = log
        self.read_state = read_state
        self.registered_ids, self.failed_ids = [], []
        self.running = False
        self.job = None
        self.previous_down = set()

    def snapshot(self):
        keys = {vk for _, vk, _ in self.bindings.values()} | {0x10, 0x11, 0x12, 0x5B, 0x5C}
        # Only the high bit means currently down. The low bit is shared and
        # unreliable; reading state never consumes keyboard input.
        return {vk for vk in keys if self.read_state(vk) & 0x8000}

    def start(self):
        if self.running or not self.bindings:
            return
        if self.read_state is None:
            if os.name != 'nt':
                return
            self.read_state = ctypes.windll.user32.GetAsyncKeyState
            self.read_state.argtypes = [ctypes.c_int]
            self.read_state.restype = ctypes.c_short
        self.previous_down = self.snapshot()
        self.registered_ids = list(self.bindings)
        self.running = True
        self.job = self.scheduler.after(12, self.poll)

    def poll(self):
        self.job = None
        if not self.running:
            return
        down = self.snapshot()
        mods = ((1 if 0x12 in down else 0) | (2 if 0x11 in down else 0)
                | (4 if 0x10 in down else 0) | (8 if down & {0x5B, 0x5C} else 0))
        pressed = down - self.previous_down
        self.previous_down = down
        for required, vk, callback in self.bindings.values():
            if vk in pressed and mods == required:
                try:
                    callback()
                except Exception as exc:
                    self.log('Hotkey callback error:', exc)
        if self.running:
            self.job = self.scheduler.after(12, self.poll)

    def stop(self):
        self.running = False
        if self.job is not None:
            self.scheduler.after_cancel(self.job)
            self.job = None
        self.previous_down.clear()
