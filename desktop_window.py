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


class ManagedWindowFrame:
    """Custom chrome on a normal Tk window, without override-redirect remapping.

    Keep native system-menu/minimize styles for Explorer. A subclass removes
    only the non-client frame; Tk still owns normal/iconic/withdrawn state.
    """
    def __init__(self, root):
        self.root = root
        self.handles = set()
        self.user32 = ctypes.windll.user32
        self.comctl32 = ctypes.windll.comctl32
        pointer = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(pointer, wintypes.HWND, wintypes.UINT,
                                          ctypes.c_size_t, pointer,
                                          ctypes.c_size_t, ctypes.c_size_t)
        # Strong reference for the entire Tk lifetime, including wrapper changes.
        self.callback = callback_type(self._dispatch)
        self.subclass_id = id(self)
        self.comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
        self.comctl32.SetWindowSubclass.restype = wintypes.BOOL
        self.comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
        self.comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
        self.comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, pointer]
        self.comctl32.DefSubclassProc.restype = pointer
        suffix = 'PtrW' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'W'
        self.get_long = getattr(self.user32, 'GetWindowLong'+suffix)
        self.set_long = getattr(self.user32, 'SetWindowLong'+suffix)
        self.get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_long.restype = pointer
        self.set_long.argtypes = [wintypes.HWND, ctypes.c_int, pointer]
        self.set_long.restype = pointer
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL

    def _dispatch(self, hwnd, message, wparam, lparam, subclass_id, ref_data):
        if message == 0x0083 and wparam:  # WM_NCCALCSIZE: all of the window is client area
            return 0
        if message == 0x0084:  # WM_NCHITTEST: our canvas handles titlebar and resize controls
            return 1  # HTCLIENT
        if message == 0x0085:  # WM_NCPAINT: the glass canvas paints its own border
            return 0
        if message == 0x0086:  # WM_NCACTIVATE: do not paint a native caption on activation
            return 1
        if message == 0x0082:  # WM_NCDESTROY: detach before forwarding final destruction
            self.comctl32.RemoveWindowSubclass(hwnd, self.callback, self.subclass_id)
            self.handles.discard(hwnd)
        return self.comctl32.DefSubclassProc(hwnd, message, wparam, lparam)

    def attach(self):
        hwnd = window_handle(self.root)
        if not hwnd or hwnd in self.handles:
            return hwnd
        if not self.comctl32.SetWindowSubclass(hwnd, self.callback, self.subclass_id, 0):
            raise OSError('Could not install the custom window frame')
        self.handles.add(hwnd)
        ex_style = self.get_long(hwnd, -20)
        self.set_long(hwnd, -20, (ex_style | 0x00040000) & ~0x00000080)
        style = self.get_long(hwnd, -16)
        self.set_long(hwnd, -16, style | 0x00080000 | 0x00020000)  # SYSMENU, MINIMIZEBOX
        # Recalculate the client area without hiding/showing or activating.
        self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)
        return hwnd
