import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import desktop_window as desktop


class WindowRegionTests(unittest.TestCase):
    def apply(self, success):
        user=SimpleNamespace(SetWindowRgn=Mock(return_value=success))
        gdi=SimpleNamespace(CreateRoundRectRgn=Mock(return_value=123),DeleteObject=Mock())
        with patch.object(desktop.ctypes,'windll',SimpleNamespace(user32=user,gdi32=gdi),create=True):
            result=desktop.set_rounded_region(99,(0,0,1280,793),28)
        gdi.CreateRoundRectRgn.assert_called_once_with(0,0,1280,793,56,56)
        user.SetWindowRgn.assert_called_once_with(99,123,True)
        return result,gdi
    def test_success_transfers_region_ownership_to_windows(self):
        result,gdi=self.apply(1)
        self.assertTrue(result)
        gdi.DeleteObject.assert_not_called()
    def test_failed_region_is_released(self):
        result,gdi=self.apply(0)
        self.assertFalse(result)
        gdi.DeleteObject.assert_called_once_with(123)
