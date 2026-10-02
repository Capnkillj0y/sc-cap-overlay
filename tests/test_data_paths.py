import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import data_paths as d


class DataMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.old=self.root/'old café app';self.old.mkdir()
        self.new=self.root/'settings';self.new.mkdir()
        (self.old/'config.json').write_text('{"threshold":25}')
        (self.old/'update_settings.json').write_text('{"repository":"owner/repo"}')
        (self.old/'missile_ai').mkdir()
        (self.old/'missile_ai'/'model.npz').write_bytes(b'training model')
    def test_migration_keeps_originals_and_copies_training(self):
        copied=d.migrate_from(self.old,self.new)
        self.assertIn('config.json',copied)
        self.assertIn('missile_ai',copied)
        self.assertEqual((self.new/'missile_ai'/'model.npz').read_bytes(),b'training model')
        self.assertEqual((self.old/'config.json').read_bytes(),(self.new/'config.json').read_bytes())
    def test_existing_settings_and_ai_are_never_overwritten_or_merged(self):
        (self.new/'config.json').write_text('existing')
        (self.new/'missile_ai').mkdir()
        (self.new/'missile_ai'/'meta.json').write_text('newer training set')
        d.migrate_from(self.old,self.new)
        self.assertEqual((self.new/'config.json').read_text(),'existing')
        self.assertFalse((self.new/'missile_ai'/'model.npz').exists())
    def test_failed_training_copy_is_not_exposed_and_can_retry(self):
        def fail(source,target,**kw):
            Path(target).mkdir();(Path(target)/'partial').write_text('partial')
            raise OSError('simulated disk failure')
        with patch.object(d.shutil,'copytree',side_effect=fail):
            with self.assertRaises(OSError):d.migrate_from(self.old,self.new)
        self.assertFalse((self.new/'missile_ai').exists())
        self.assertFalse(list(self.new.glob('.migration-*')))
        d.migrate_from(self.old,self.new)
        self.assertTrue((self.new/'missile_ai'/'model.npz').exists())
    def initialize(self):
        with patch.object(d,'user_data_dir',return_value=self.new),patch.object(d,'program_dir',return_value=self.old),patch.dict(d.os.environ,{},clear=True):
            return d.initialize_data_dir()
    def test_migration_runs_once_so_reset_does_not_restore_old_config(self):
        data,warnings=self.initialize()
        self.assertEqual(warnings,[])
        (self.new/'config.json').unlink()
        self.initialize()
        self.assertFalse((self.new/'config.json').exists())
    def test_explicit_import_marker_supports_unicode_and_is_consumed(self):
        (self.new/'migration-source.txt').write_text(str(self.old),encoding='utf-8-sig')
        _,warnings=self.initialize()
        self.assertEqual(warnings,[])
        self.assertFalse((self.new/'migration-source.txt').exists())
        self.assertTrue((self.new/'config.json').exists())
    def test_missing_import_keeps_marker_for_retry(self):
        (self.new/'migration-source.txt').write_text(str(self.root/'missing'))
        _,warnings=self.initialize()
        self.assertTrue(warnings)
        self.assertTrue((self.new/'migration-source.txt').exists())
    def test_override_does_not_import_real_settings(self):
        with patch.dict(d.os.environ,{'SC_CAPACITOR_DATA_DIR':str(self.new)}),patch.object(d,'program_dir',return_value=self.old):
            data,warnings=d.initialize_data_dir()
        self.assertEqual(Path(data),self.new)
        self.assertFalse((self.new/'config.json').exists())
