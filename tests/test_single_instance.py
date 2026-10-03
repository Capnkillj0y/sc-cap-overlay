import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch
import single_instance as si


class LaunchMessageTests(unittest.TestCase):
    def test_duplicate_exits_without_starting_app(self):
        with patch.object(si.InstanceGuard, 'acquire', return_value=False), patch.object(si, '_message') as msg:
            with self.assertRaises(SystemExit) as caught:
                si.enforce_single_instance()
            self.assertEqual(caught.exception.code, 0)
            self.assertIn('already running', msg.call_args[0][0])

    def test_check_failure_does_not_allow_duplicate(self):
        with patch.object(si.InstanceGuard, 'acquire', side_effect=OSError('denied')), patch.object(si, '_message'):
            with self.assertRaises(SystemExit) as caught:
                si.enforce_single_instance()
            self.assertEqual(caught.exception.code, 1)


@unittest.skipUnless(os.name == 'nt', 'Windows named mutex integration')
class WindowsInstanceTests(unittest.TestCase):
    def test_blocks_second_process_and_recovers_after_owner_killed(self):
        name = 'Local\\SCCapacitor.Test.' + uuid.uuid4().hex
        root = str(Path(si.__file__).resolve().parent)
        owner_code = ('from single_instance import InstanceGuard; import time; '
                      f'g=InstanceGuard({name!r}); print(g.acquire(), flush=True); time.sleep(60)')
        probe_code = ('from single_instance import InstanceGuard; '
                      f'g=InstanceGuard({name!r}); print(g.acquire())')
        owner = subprocess.Popen([sys.executable, '-u', '-c', owner_code], cwd=root, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(owner.stdout.readline().strip(), 'True')
            probe = subprocess.run([sys.executable, '-c', probe_code], cwd=root, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(probe.stdout.strip(), 'False')
        finally:
            owner.kill()
            owner.wait(timeout=10)
            owner.stdout.close()
        probe = subprocess.run([sys.executable, '-c', probe_code], cwd=root, capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(probe.stdout.strip(), 'True')
