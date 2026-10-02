"""Windows window shape and application identity for the Glass Cockpit UI."""
import ctypes
from ctypes import wintypes
import os

APP_ID = 'SCCapacitor.GlassCockpit.Desktop'


def prepare_app_identity():
    if os.name != 'nt':
        return
    try:
        func = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        func.argtypes = [wintypes.LPCWSTR]
        func.restype = ctypes.c_long
        func(APP_ID)
    except (OSError, AttributeError) as exc:
        print('Taskbar identity unavailable:', exc)


def install_icon(root, resource):
    try:
        if os.name == 'nt':
            icon = resource('sc_capacitor.ico')
            root.iconbitmap(default=icon)
            root.iconbitmap(icon)
        else:
            import tkinter as tk
            root._app_icon = tk.PhotoImage(file=resource('sc_capacitor_icon.png'))
            root.iconphoto(True, root._app_icon)
    except Exception as exc:
        # Missing icon must never prevent the app from opening.
        print('Application icon unavailable:', exc)


def window_handle(root):
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    return user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT: Tk's native wrapper


def set_rounded_region(hwnd, bounds, radius):
    """Clip the real native window, including the outside corner triangles."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    region = gdi32.CreateRoundRectRgn(*bounds, radius * 2, radius * 2)
    if not region:
        return False
    if not user32.SetWindowRgn(hwnd, region, True):
        gdi32.DeleteObject(region)
        return False
    # On success Windows owns the region and disposes it when replaced.
    return True


def clear_region(root):
    if os.name == 'nt':
        user32 = ctypes.windll.user32
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        user32.SetWindowRgn(window_handle(root), None, True)
