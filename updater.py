"""GitHub Releases updater. Network work stays off Tk's main thread."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ASSET = 'SC_Capacitor_Overlay.exe'
MAX_EXE = 300 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Release version must look like v1.1.0 (no prerelease suffix).')
    return tuple(map(int, match.groups()))


def valid_repo(value):
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+', value))


def trusted_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443)
            and (host in ('github.com', 'api.github.com') or host.endswith('.githubusercontent.com')))


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted_url(newurl):
            raise ValueError('Rejected an unexpected update download destination.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_url(url, limit, destination=None):
    if not trusted_url(url):
        raise ValueError('Updates must come from GitHub over HTTPS.')
    request = Request(url, headers={'User-Agent': 'SC-Capacitor-Updater',
                                   'Accept': 'application/vnd.github+json' if 'api.github.com/' in url else 'application/octet-stream'})
    opener = build_opener(HTTPSRedirect())
    with opener.open(request, timeout=30) as response:
        chunks, total = [], 0
        output = open(destination, 'wb') if destination else None
        try:
            while True:
                chunk = response.read(128*1024)
                if not chunk: break
                total += len(chunk)
                if total > limit: raise ValueError('Update download exceeds the size limit.')
                if output: output.write(chunk)
                else: chunks.append(chunk)
        finally:
            if output: output.close()
    return total if destination else b''.join(chunks)


def latest_release(repo, current):
    if not valid_repo(repo): raise ValueError('Enter your GitHub repository as username/repository.')
    data = json.loads(read_url(f'https://api.github.com/repos/{repo}/releases/latest', 2*1024*1024))
    if data.get('draft') or data.get('prerelease'): return None
    tag = data.get('tag_name', '')
    if version_tuple(tag) <= version_tuple(current): return None
    assets = {asset.get('name'): asset for asset in data.get('assets', [])}
    exe, checksum = assets.get(ASSET), assets.get(ASSET+'.sha256')
    if not exe or not checksum:
        raise ValueError('The newer release is missing its EXE or SHA-256 file. Try again after its build finishes.')
    prefix = f'https://github.com/{repo}/releases/download/'
    for asset in (exe, checksum):
        if not asset.get('browser_download_url', '').startswith(prefix):
            raise ValueError('Release asset is outside the configured repository.')
    return {'tag': tag, 'exe': exe['browser_download_url'], 'checksum': checksum['browser_download_url']}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''): digest.update(chunk)
    return digest.hexdigest()


def download_release(release, target):
    target = Path(target).resolve()
    # Same-volume staging allows atomic replacements. A failure leaves the app intact.
    staging = Path(tempfile.mkdtemp(prefix='.sc-update-', dir=target.parent))
    try:
        checksum = read_url(release['checksum'], 1024).decode('ascii').strip().split()
        if len(checksum) != 2 or not re.fullmatch(r'[0-9a-fA-F]{64}', checksum[0]) or checksum[1].lstrip('*') != ASSET:
            raise ValueError('Invalid release checksum file.')
        expected = checksum[0].lower()
        new_exe = staging/'new.exe'
        size = read_url(release['exe'], MAX_EXE, new_exe)
        if size < 1024 or sha256_file(new_exe) != expected:
            raise ValueError('The download failed its integrity check. Your current app was not changed.')
        with new_exe.open('rb') as source:
            if source.read(2) != b'MZ': raise ValueError('The update is not a Windows executable.')
        helper = staging/'update-helper.exe'
        shutil.copy2(target, helper)
        manifest = staging/'update.json'
        manifest.write_text(json.dumps({'target_name': target.name, 'sha256': expected, 'parent_pid': os.getpid()}), encoding='utf-8')
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def independent_environment():
    env = os.environ.copy()
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def launch_helper(manifest):
    manifest = Path(manifest)
    subprocess.Popen([str(manifest.parent/'update-helper.exe'), '--apply-update', str(manifest)],
                     cwd=str(manifest.parent), env=independent_environment(),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def replace_executable(staged, target, retry_seconds=0):
    """Keep one previous EXE; restore it if the second rename fails."""
    staged, target = Path(staged), Path(target)
    backup = target.with_name(target.name+'.previous')
    deadline = time.monotonic() + retry_seconds
    while True:
        try:
            os.replace(target, backup)
            break
        except PermissionError:
            # The one-file bootloader can retain a Windows file handle briefly
            # after the application's Python process exits.
            if time.monotonic() >= deadline: raise
            time.sleep(0.25)
    try:
        os.replace(staged, target)
    except Exception:
        os.replace(backup, target)
        raise
    return backup


def wait_for_parent(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87: return  # Parent already exited.
        raise OSError(error, 'Could not wait for the running application to exit.')
    try:
        if kernel.WaitForSingleObject(handle, 60000) != 0:
            raise TimeoutError('The running app did not close within 60 seconds. It was not replaced.')
    finally: kernel.CloseHandle(handle)


def apply_update(manifest_path):
    """Runs from a separate copy of the old EXE after the user approves install."""
    manifest = Path(manifest_path).resolve()
    log_dir = manifest.parent
    try:
        if os.name != 'nt' or not getattr(sys, 'frozen', False):
            raise RuntimeError('The update installer runs only from a built Windows EXE.')
        if Path(sys.executable).resolve() != manifest.parent/'update-helper.exe':
            raise ValueError('Update installer path mismatch.')
        if not manifest.parent.name.startswith('.sc-update-'):
            raise ValueError('Invalid staging directory.')
        data = json.loads(manifest.read_text(encoding='utf-8'))
        name = data['target_name']
        if Path(name).name != name or '/' in name or '\\' in name or not name.lower().endswith('.exe'):
            raise ValueError('Invalid target name.')
        target = manifest.parent.parent/name
        staged = manifest.parent/'new.exe'
        if sha256_file(staged) != data['sha256']: raise ValueError('Staged update checksum mismatch.')
        wait_for_parent(int(data['parent_pid']))
        backup = replace_executable(staged, target, retry_seconds=15)
        try:
            subprocess.Popen([str(target)], cwd=str(target.parent), env=independent_environment())
        except Exception:
            os.replace(backup, target)
            raise
        # Remove large temporary files. The running helper is cleaned up next launch.
        manifest.write_text(json.dumps({'complete': True}), encoding='utf-8')
        return 0
    except Exception as exc:
        try: (log_dir/'update-error.txt').write_text(str(exc), encoding='utf-8')
        except OSError: pass
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, f'Update could not finish:\n{exc}\n\nYour existing EXE or its .previous backup remains available.', 'SC Capacitor update', 0x10)
        return 1


class Updater:
    def __init__(self, app, module):
        self.app, self.module, self.root = app, module, app.root
        self.queue = queue.Queue()
        self.busy, self.closed = False, False
        self.settings_path = Path(module.app_dir())/'update_settings.json'
        defaults = {'repository': '', 'check_on_startup': True}
        for path in (Path(module.bundled_resource('update_config.json')), self.settings_path):
            try: defaults.update(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError): pass
        self.settings = defaults
        self.poll_job = self.root.after(150, self.poll)
        self.start_job = self.root.after(4000, self.startup)
        # Delete only completed staging directories; failed updates keep diagnostics.
        if getattr(sys, 'frozen', False):
            for directory in Path(module.app_dir()).glob('.sc-update-*'):
                try:
                    if json.loads((directory/'update.json').read_text()).get('complete'):
                        shutil.rmtree(directory, ignore_errors=True)
                except (OSError, ValueError): pass

    def startup(self):
        self.start_job = None
        if self.settings.get('check_on_startup') and self.settings.get('repository'):
            self.check(False)

    def save(self):
        temp = self.settings_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.settings, indent=2), encoding='utf-8')
        os.replace(temp, self.settings_path)

    def open_settings(self):
        from tkinter import messagebox, simpledialog
        if self.busy:
            messagebox.showinfo('Updates', 'An update check or download is already running.', parent=self.root)
            return
        self.app._modal_open = True
        try:
            repo = simpledialog.askstring('GitHub updates',
                f'Current version: {self.module.__version__}\n\nPublic GitHub repository (username/repository):',
                initialvalue=self.settings.get('repository',''), parent=self.root)
            if repo is None: return
            repo = repo.strip().removeprefix('https://github.com/').rstrip('/')
            if not valid_repo(repo):
                messagebox.showerror('Updates', 'Use username/repository, for example shane/sc-cap-overlay.', parent=self.root)
                return
            auto = messagebox.askyesno('Automatic checks', 'Check for updates automatically each time the app opens?\n\nInstallation will still ask for your approval.', parent=self.root)
            self.settings.update(repository=repo, check_on_startup=auto)
            try: self.save()
            except OSError as exc:
                messagebox.showerror('Updates', f'Could not save update settings:\n{exc}', parent=self.root)
                return
        finally: self.app._modal_open = False
        self.check(True)

    def status(self, text):
        label = getattr(self.app, 'update_status_label', None)
        if label: label.config(text=text)

    def check(self, manual):
        if self.busy or self.closed: return
        self.busy = True
        self.status('Checking for updates…')
        def work():
            try: self.queue.put(('checked', manual, latest_release(self.settings['repository'], self.module.__version__)))
            except Exception as exc: self.queue.put(('error', manual, exc))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if self.closed: return
        try: event = self.queue.get_nowait()
        except queue.Empty: event = None
        # Do not interrupt calibration, hotkey capture, or a running game session.
        if event and (self.app._modal_open or self.app.monitor_thread is not None):
            self.queue.put(event)
        elif event: self.handle(event)
        if not self.closed: self.poll_job = self.root.after(250, self.poll)

    def handle(self, event):
        from tkinter import messagebox
        kind, manual, value = event
        self.busy = False
        self.status('')
        self.app._modal_open = True
        try:
            if kind == 'error':
                if manual:
                    detail = 'No public release found. Check the repository name and publish its first release.' if isinstance(value,HTTPError) and value.code==404 else str(value)
                    messagebox.showerror('Update check', detail, parent=self.root)
                else: print('Automatic update check:', value)
            elif kind == 'checked':
                if value is None:
                    if manual: messagebox.showinfo('Updates', f'You are up to date (v{self.module.__version__}).', parent=self.root)
                    return
                if not getattr(sys,'frozen',False) or os.name!='nt':
                    messagebox.showinfo('Update available', f"{value['tag']} is available. Source installations update with GitHub Desktop / git pull. In-app installation is for the Windows EXE.", parent=self.root)
                    return
                if messagebox.askyesno('Update available', f"{value['tag']} is available (current: v{self.module.__version__}).\n\nDownload, install and restart? Your calibration and AI data will be kept.", parent=self.root):
                    self.busy = True
                    self.status('Downloading update…')
                    def work():
                        try: self.queue.put(('downloaded',True,download_release(value,sys.executable)))
                        except Exception as exc: self.queue.put(('error',True,exc))
                    threading.Thread(target=work,daemon=True).start()
            elif kind == 'downloaded':
                # Reconfirm after download because the user may have resumed playing.
                if not messagebox.askyesno('Update ready', 'Download verified. Close this app, install the update and restart now?', parent=self.root):
                    shutil.rmtree(Path(value).parent,ignore_errors=True)
                    return
                try: launch_helper(value)
                except Exception as exc:
                    messagebox.showerror('Update failed', str(exc), parent=self.root)
                    return
                self.app.on_close()
        finally: self.app._modal_open = False

    def close(self):
        self.closed = True
        for job in (self.poll_job, self.start_job):
            if job:
                try: self.root.after_cancel(job)
                except Exception: pass
