import unittest
from hotkeys import HotkeyManager


class Scheduler:
    def __init__(self): self.jobs = {}; self.index = 0
    def after(self, delay, callback):
        self.index += 1
        self.jobs[self.index] = callback
        return self.index
    def after_cancel(self, job): self.jobs.pop(job, None)
    def tick(self):
        job = next(iter(self.jobs))
        self.jobs.pop(job)()


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.state = {}; self.events = []; self.scheduler = Scheduler()
        self.manager = HotkeyManager({1: (0, 75, lambda: self.events.append('K')),
                                      2: (2, 75, lambda: self.events.append('Ctrl+K'))},
                                     self.scheduler, read_state=lambda vk: self.state.get(vk, 0))
        self.manager.start()
    def step(self, *keys):
        self.state = dict.fromkeys(keys, 0x8000)
        self.scheduler.tick()
    def test_key_hold_fires_once_and_release_rearms(self):
        self.step(75); self.step(75); self.step(75)
        self.assertEqual(self.events, ['K'])
        self.step(); self.step(75)
        self.assertEqual(self.events, ['K', 'K'])
        self.assertEqual(self.state[75], 0x8000)
    def test_exact_modifiers_and_no_retrigger_on_modifier_release(self):
        self.step(0x11); self.step(0x11,75); self.step(75)
        self.assertEqual(self.events, ['Ctrl+K'])
        self.step(); self.step(0x10,75)
        self.assertEqual(self.events, ['Ctrl+K'])
    def test_ignore_unreliable_low_bit(self):
        self.state={75:1}; self.scheduler.tick()
        self.assertEqual(self.events, [])
    def test_stop_and_restart_does_not_trigger_already_held_key(self):
        self.manager.stop(); self.assertFalse(self.scheduler.jobs)
        self.state={75:0x8000}; self.manager.start(); self.scheduler.tick()
        self.assertEqual(self.events, [])
        self.step(); self.step(75)
        self.assertEqual(self.events, ['K'])
