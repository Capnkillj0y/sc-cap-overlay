import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from updater import Updater


class UpdateButtonTests(unittest.TestCase):
    def test_configured_button_checks_without_prompts(self):
        updater=Updater.__new__(Updater)
        updater.busy=False; updater.settings={'repository':'owner/repo'}
        updater.check=Mock()
        # No simpledialog is supplied: any repository prompt would fail.
        dialogs=SimpleNamespace(showinfo=Mock(),showerror=Mock())
        with patch.dict(sys.modules,{'tkinter':SimpleNamespace(messagebox=dialogs)}):
            updater.open_settings()
        updater.check.assert_called_once_with(True)
        dialogs.showinfo.assert_not_called(); dialogs.showerror.assert_not_called()
