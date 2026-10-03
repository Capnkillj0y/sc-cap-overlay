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


class ManagedFrameTests(unittest.TestCase):
    def frame(self):
        frame=desktop.ManagedWindowFrame.__new__(desktop.ManagedWindowFrame)
        frame.root=object();frame.handles=set();frame.callback=object();frame.subclass_id=123
        frame.get_long=Mock(return_value=0x80)
        frame.set_long=Mock()
        frame.user32=SimpleNamespace(SetWindowPos=Mock(return_value=1))
        frame.comctl32=SimpleNamespace(SetWindowSubclass=Mock(return_value=1),
            RemoveWindowSubclass=Mock(),DefSubclassProc=Mock(return_value=42))
        return frame
    def test_attach_is_idempotent_and_keeps_taskbar_styles(self):
        frame=self.frame()
        with patch.object(desktop,'window_handle',return_value=99):
            frame.attach();frame.attach()
        frame.comctl32.SetWindowSubclass.assert_called_once()
        frame.user32.SetWindowPos.assert_called_once()
        ex_style=frame.set_long.call_args_list[0].args[2]
        self.assertTrue(ex_style & 0x40000)
        self.assertFalse(ex_style & 0x80)
        style=frame.set_long.call_args_list[1].args[2]
        self.assertTrue(style & 0x80000)
        self.assertTrue(style & 0x20000)
    def test_native_size_and_system_commands_reach_tk(self):
        frame=self.frame()
        for message in (0x0005,0x0112):  # WM_SIZE, WM_SYSCOMMAND
            self.assertEqual(frame._dispatch(99,message,0,0,123,0),42)
        self.assertEqual(frame.comctl32.DefSubclassProc.call_count,2)
        self.assertEqual(frame._dispatch(99,0x0083,1,0,123,0),0)
    def test_destroy_detaches_callback_and_allows_new_wrapper(self):
        frame=self.frame();frame.handles.add(99)
        frame._dispatch(99,0x0082,0,0,123,0)
        self.assertNotIn(99,frame.handles)
        frame.comctl32.RemoveWindowSubclass.assert_called_once_with(99,frame.callback,123)

    def test_move_preserves_size_and_focus_with_negative_coordinates(self):
        frame=self.frame()
        with patch.object(desktop,'window_handle',return_value=99):
            frame.move(-240,-30)
        frame.user32.SetWindowPos.assert_called_once_with(99,None,-240,-30,0,0,0x15)
