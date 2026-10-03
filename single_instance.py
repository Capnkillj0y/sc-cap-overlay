"""Prevent duplicate desktop app launches before settings or UI are loaded."""
import ctypes
from ctypes import wintypes
import os

MUTEX_NAME = r'Local\SCCapacitor.GlassCockpit.Desktop.SingleInstance'


class InstanceGuard:
    def __init__(self, name=MUTEX_NAME):
        self.name = name
        self.handle = None
        self.kernel = None

    def acquire(self):
        if os.name != 'nt' or self.handle is not None:
            return True
        k = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        handle = k.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = k.WaitForSingleObject(handle, 0)
        if result in (0, 0x80):  # acquired, or recovered from a crashed owner
            self.handle = handle
            # Hold until process termination. Windows closes the handle even
            # after a crash; no stale lock file or early shutdown unlock.
            return True
        error = ctypes.get_last_error()
        k.CloseHandle(handle)
        if result == 0x102:  # another instance owns the mutex
            return False
        raise OSError(error, 'Could not check whether the app is already running.')


def enforce_single_instance():
    guard = InstanceGuard()
    try:
        acquired = guard.acquire()
    except OSError as exc:
        _message('SC Capacitor Overlay could not check for another running copy.\n\n'
                 f'Close any existing copy and try again.\n\n{exc}', error=True)
        raise SystemExit(1)
    if not acquired:
        _message('SC Capacitor Overlay is already running.\n\n'
                 'Open the existing window from the taskbar or Alt+Tab.\n'
                 'Close it before launching another copy.')
        raise SystemExit(0)
    return guard


def _message(text, error=False):
    if os.name == 'nt':
        fn = ctypes.WinDLL('user32', use_last_error=True).MessageBoxW
        fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        fn.restype = ctypes.c_int
        fn(None, text, 'SC Capacitor Overlay', (0x10 if error else 0x40) | 0x10000)
