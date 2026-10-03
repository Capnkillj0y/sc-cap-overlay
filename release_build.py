"""Build the Windows installer, update EXE, and their SHA-256 checksums."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from updater import ASSET, sha256_file, valid_repo, version_tuple

SETUP_ASSET = 'SC-Capacitor-Setup.exe'


def build_installer(version):
    compiler = shutil.which('ISCC.exe') or shutil.which('iscc')
    if not compiler:
        for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
            if base:
                candidate = Path(base)/'Inno Setup 6'/'ISCC.exe'
                if candidate.is_file():
                    compiler = str(candidate)
                    break
    if not compiler:
        raise RuntimeError('Install Inno Setup 6 from https://jrsoftware.org/isinfo.php, or use the GitHub workflow.')
    subprocess.run([compiler, '/DAppVersion='+version, str(Path('installer/setup.iss').resolve())], check=True)
    setup = Path('dist')/SETUP_ASSET
    if not setup.is_file():
        raise RuntimeError('Installer build did not produce '+SETUP_ASSET)
    Path(str(setup)+'.sha256').write_text(sha256_file(setup)+'  '+SETUP_ASSET+'\n', encoding='ascii')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repository',default='')
    parser.add_argument('--metadata-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    os.chdir(root)
    version=re.search(r'^__version__ = "([^"]+)"',Path('sc_capacitor_ocr.py').read_text(encoding='utf-8'),re.M).group(1)
    version_tuple(version)
    if args.repository:
        if not valid_repo(args.repository): raise ValueError('Use owner/repository.')
        Path('update_config.json').write_text(json.dumps({'repository':args.repository,'check_on_startup':True},indent=2),encoding='utf-8')
    if not args.metadata_only:
        if os.name!='nt': raise RuntimeError('Build this Windows release on Windows or use the included GitHub workflow.')
        cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
             '--name','SC_Capacitor_Overlay','--icon','sc_capacitor.ico','--hidden-import','missile_ai','--hidden-import','glass_skin','--hidden-import','updater']
        for name in ('sc_capacitor.ico','sc_capacitor_icon.png','glass_cockpit_skin.png','update_config.json','missile_template.png','inbound_template.png','missile_word_template.png'):
            cmd+=['--add-data',name+';.']
        cmd += ['--add-data', 'assets/Orbitron.ttf;assets', '--add-data', 'assets/Orbitron-OFL.txt;assets']
        subprocess.run(cmd+['sc_capacitor_ocr.py'],check=True)
        exe=Path('dist')/ASSET
        if not exe.is_file(): raise RuntimeError('Build did not produce the release executable.')
        Path(str(exe)+'.sha256').write_text(sha256_file(exe)+'  '+ASSET+'\n',encoding='ascii')
        build_installer(version)
    Path('release-info.json').write_text(json.dumps({'version':version,'tag':'v'+version}),encoding='utf-8')
    print('Release version: v'+version)


if __name__=='__main__': main()
