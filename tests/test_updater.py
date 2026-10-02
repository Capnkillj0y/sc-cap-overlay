import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import updater as u


class UpdateTests(unittest.TestCase):
    def test_version_order_and_invalid_versions(self):
        self.assertGreater(u.version_tuple('v1.10.0'),u.version_tuple('1.9.9'))
        for value in ('1.0','v1.2.3-beta','latest','1.2.3; echo bad'):
            with self.assertRaises(ValueError): u.version_tuple(value)

    def test_latest_requires_complete_assets_and_newer_version(self):
        prefix='https://github.com/shane/sc-cap-overlay/releases/download/v1.2.0/'
        data={'tag_name':'v1.2.0','draft':False,'prerelease':False,'assets':[
            {'name':name,'browser_download_url':prefix+name} for name in (u.ASSET,u.ASSET+'.sha256')]}
        with patch.object(u,'read_url',return_value=json.dumps(data).encode()):
            self.assertEqual(u.latest_release('shane/sc-cap-overlay','1.1.0')['tag'],'v1.2.0')
            self.assertIsNone(u.latest_release('shane/sc-cap-overlay','1.2.0'))
            self.assertIsNone(u.latest_release('shane/sc-cap-overlay','1.3.0'))
        data['assets'].pop()
        with patch.object(u,'read_url',return_value=json.dumps(data).encode()):
            with self.assertRaises(ValueError): u.latest_release('shane/sc-cap-overlay','1.1.0')

    def test_untrusted_urls_rejected(self):
        for url in ('http://github.com/a','https://github.com.evil.test/a','https://github.com@evil.test/a','file:///tmp/app.exe'):
            self.assertFalse(u.trusted_url(url))
        self.assertTrue(u.trusted_url('https://release-assets.githubusercontent.com/a'))

    def test_verified_download_and_hash_failure_leave_original_untouched(self):
        payload=b'MZ'+b'new application'*100
        digest=hashlib.sha256(payload).hexdigest()
        release={'exe':'exe','checksum':'checksum','tag':'v1.2.0'}
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/u.ASSET
            target.write_bytes(b'old application')
            for valid in (True,False):
                def read(url,limit,destination=None):
                    if url=='checksum': return ((digest if valid else '0'*64)+'  '+u.ASSET).encode()
                    Path(destination).write_bytes(payload)
                    return len(payload)
                with patch.object(u,'read_url',side_effect=read):
                    if valid:
                        manifest=u.download_release(release,target)
                        self.assertEqual(json.loads(manifest.read_text())['sha256'],digest)
                        self.assertEqual((manifest.parent/'new.exe').read_bytes(),payload)
                    else:
                        with self.assertRaises(ValueError): u.download_release(release,target)
                self.assertEqual(target.read_bytes(),b'old application')
            self.assertEqual(len(list(Path(temp).glob('.sc-update-*'))),1)

    def test_replace_preserves_backup_and_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=root/u.ASSET; staged=root/'new.exe'; cfg=root/'config.json'
            target.write_bytes(b'old'); staged.write_bytes(b'new'); cfg.write_text('keep')
            backup=u.replace_executable(staged,target)
            self.assertEqual(target.read_bytes(),b'new')
            self.assertEqual(backup.read_bytes(),b'old')
            self.assertEqual(cfg.read_text(),'keep')

    def test_failed_swap_restores_old_exe(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=root/u.ASSET; staged=root/'new.exe'
            target.write_bytes(b'old'); staged.write_bytes(b'new')
            real=u.os.replace
            def replace(a,b):
                if Path(a)==staged: raise PermissionError('simulated antivirus lock')
                return real(a,b)
            with patch.object(u.os,'replace',side_effect=replace):
                with self.assertRaises(PermissionError): u.replace_executable(staged,target)
            self.assertEqual(target.read_bytes(),b'old')


if __name__=='__main__': unittest.main()
