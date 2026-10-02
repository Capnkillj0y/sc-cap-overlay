"""One per-user data directory, with non-destructive migration from old EXEs."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

APP_NAME = 'SC Capacitor Overlay'
DATA_FILES = ('config.json', 'update_settings.json', 'calibration_crop.png',
              'app_log.txt', 'app_log.txt.old', 'error_log.txt')
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A737BD14-5A13-44C5-BBFA-6A37FA93C870}_is1'


def program_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def user_data_dir():
    override = os.environ.get('SC_CAPACITOR_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local')))/APP_NAME
    return program_dir()  # Keep development runs on non-Windows self-contained.


def migrate_from(source, destination):
    """Copy missing data only; never move originals or merge two AI datasets."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return []
    if not source.is_dir():
        raise FileNotFoundError(f'Previous app folder is not available: {source}')
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in DATA_FILES + ('missile_ai',):
        old, new = source/name, destination/name
        if new.exists() or not old.exists():
            continue
        if old.is_symlink():
            continue
        # Stage on the destination volume. Failed copies never leave partial
        # settings or a partial training directory under their final names.
        with tempfile.TemporaryDirectory(prefix='.migration-', dir=destination) as temp:
            staged = Path(temp)/name
            if old.is_dir():
                shutil.copytree(old, staged, ignore=lambda folder, names: [n for n in names if (Path(folder)/n).is_symlink()])
            else:
                shutil.copy2(old, staged)
            if not new.exists():
                os.rename(staged, new)
                copied.append(name)
    return copied


def initialize_data_dir():
    data = user_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    marker, state_path = data/'migration-source.txt', data/'migration-state.json'
    warnings = []
    try:
        completed = set(json.loads(state_path.read_text(encoding='utf-8')).get('completed', []))
    except (OSError, ValueError, TypeError):
        completed = set()
    sources = []
    if marker.exists():
        try:
            source = marker.read_text(encoding='utf-8-sig').strip()
            if source:
                sources.append((Path(source), True))
        except OSError as exc:
            warnings.append(str(exc))
    # Test/development overrides must not import real personal data.
    if not os.environ.get('SC_CAPACITOR_DATA_DIR'):
        sources.append((program_dir(), False))
    changed = False
    for source, explicit in sources:
        key = str(source.resolve())
        if key in completed and not explicit:
            continue
        try:
            migrate_from(source, data)
            completed.add(key)
            changed = True
            if explicit:
                marker.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f'Could not import settings from {source}: {exc}. Your originals are unchanged.')
    if changed:
        staged = state_path.with_suffix('.tmp')
        staged.write_text(json.dumps({'completed': sorted(completed)}, indent=2), encoding='utf-8')
        os.replace(staged, state_path)
    return str(data), warnings


def refresh_installed_version(version):
    """Keep Installed Apps accurate after the existing EXE updater runs."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
            installed = winreg.QueryValueEx(key, 'InstallLocation')[0]
            if Path(installed).resolve() == program_dir():
                winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, version)
    except OSError:
        pass  # A portable EXE has no installed-program entry.
