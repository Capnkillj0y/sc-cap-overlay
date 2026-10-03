"""Runs on the GitHub Windows runner; skips on non-Windows development hosts."""
import ctypes
from ctypes import wintypes
import os
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'nt', 'Requires a real Windows window manager')
class WindowsWindowLifecycleTests(unittest.TestCase):
    def test_startup_minimize_restore_and_calibration_hide(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'SC_CAPACITOR_DATA_DIR':data}):
            import sc_capacitor_ocr as module
            from desktop_window import window_handle
            with patch.object(module,'missile_ai',None):
                app=module.App()
            def pump():
                end=time.monotonic()+0.25
                while time.monotonic()<end:
                    app.root.update()
                    time.sleep(0.01)
            user=ctypes.windll.user32
            for name in ('IsWindowVisible','IsIconic'):
                fn=getattr(user,name);fn.argtypes=[wintypes.HWND];fn.restype=wintypes.BOOL
            user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
            user.ShowWindow.restype=wintypes.BOOL
            def eligible(hwnd):
                self.assertFalse(app.root.overrideredirect())
                ex=app.glass_surface.native_frame.get_long(hwnd,-20)
                self.assertTrue(ex & 0x40000)
                self.assertFalse(ex & 0x80)
            try:
                pump();hwnd=window_handle(app.root)
                eligible(hwnd)
                self.assertTrue(user.IsWindowVisible(hwnd))
                from types import SimpleNamespace
                surface=app.glass_surface
                background=surface.background
                surface.drag=(15,15)
                for x,y in ((100,100),(-100,100),(100,-20),(240,160)):
                    surface.drag_motion(SimpleNamespace(x_root=x+15,y_root=y+15))
                    pump()
                    self.assertEqual(surface.native_frame.position(),(x,y))
                    self.assertTrue(user.IsWindowVisible(hwnd))
                    self.assertEqual(window_handle(app.root),hwnd)
                    self.assertIs(surface.background,background)
                surface.drag=None
                for _ in range(3):
                    app.glass_surface.minimize();pump()
                    self.assertEqual(window_handle(app.root),hwnd)
                    self.assertTrue(user.IsIconic(hwnd))
                    self.assertEqual(app.root.state(),'iconic')
                    eligible(hwnd)
                    user.ShowWindow(hwnd,9)  # SW_RESTORE: native restore, as used by the shell
                    pump()
                    self.assertEqual(app.root.state(),'normal')
                    self.assertFalse(user.IsIconic(hwnd))
                    self.assertTrue(user.IsWindowVisible(hwnd))
                    self.assertEqual(window_handle(app.root),hwnd)
                    eligible(hwnd)
                app.root.withdraw();pump()
                app.root.deiconify();pump()
                eligible(window_handle(app.root))
                self.assertEqual(app.root.state(),'normal')
            finally:
                app.updater.close()
                if app.hotkeys:app.hotkeys.stop()
                app.root.destroy()
