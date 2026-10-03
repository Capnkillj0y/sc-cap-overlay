# Publish SC Capacitor Overlay v1.2.3

This version fixes taskbar window handling and retains the installer and central settings storage. The Flight
icon, rounded window, keyboard fix and automatic updates are included.

## Update your existing GitHub repository

1. Extract this ZIP outside your repository.
2. In GitHub Desktop, choose **Repository → Show in Explorer**.
3. Copy the extracted inner app folder's contents into that repository folder.
   Replace existing files. Include `.github`, `installer`, `assets`, and `tests`.
4. In Desktop, enter **Prevent duplicate app launches** in Summary.
5. Click **Commit to main** (or your branch name), then **Push origin**.
6. On the GitHub website, open **Actions → Build Windows release → Run workflow**.
7. Wait for a green check. The Windows runner tests the EXE, installer, shortcuts,
   settings migration, reinstall, and data preservation after uninstall.
8. Open **Code → Releases → v1.2.3 draft**.

Do not rerun a successful build for the same version while its release exists.
Use a new version for another release, or delete an unpublished failed draft
before retrying that version.

## Test the installer before publishing

1. Download **SC-Capacitor-Setup.exe** from the draft.
2. Close every old copy of the app.
3. Run Setup and choose the program location (the default is recommended).
4. If your settings still live beside your old EXE, choose that old folder on
   **Keep your existing settings**. Otherwise leave this optional field blank.
5. Keep **Create a desktop shortcut** checked if you want one, then install.
6. Launch from the new shortcut. Confirm the Flight icon and version 1.2.3.
7. Confirm calibration, hotkeys and AI data survived the import. Test monitoring.
8. Check minimize/restore, the rounded corners and the UPDATES button.
9. Open **SC Capacitor Settings Folder** from Start to inspect the data location.
10. Publish the draft as a normal latest release after testing.

The draft contains FOUR assets:

| File | Purpose |
| --- | --- |
| SC-Capacitor-Setup.exe | Recommended download for installing the app |
| SC-Capacitor-Setup.exe.sha256 | Installer checksum |
| SC_Capacitor_Overlay.exe | Required by existing in-app updaters |
| SC_Capacitor_Overlay.exe.sha256 | Required by existing in-app updaters |

Keep the exact filenames and all four assets. Pushing code alone does not update
installed copies; publishing the release makes it available to the updater.

## Where files live

- Program files default to `%LOCALAPPDATA%\Programs\SC Capacitor Overlay`.
- All user data lives in `%LOCALAPPDATA%\SC Capacitor Overlay`.
- `config.json`: calibration, hotkeys and overlay preferences.
- `update_settings.json`: saved updater preferences, if customized previously.
- `missile_ai`: training samples and model files.
- Logs, calibration debug image and migration state live in the same data folder.

Program location is selectable. The per-user settings location stays stable so
updates and reinstalls do not scatter or replace personal data. Setup normally
needs no administrator access. Uninstall via Windows Installed apps removes the
program and shortcuts but retains the data folder. Close the app and manually
delete that folder if you want to remove personal data too.

## Existing portable copies and auto-updates

Updating an old EXE to 1.2.0 automatically imports missing settings from beside
that EXE into the central data folder. Original files are left intact. Migration
is recorded so resetting your settings does not import old calibration again.
When installing into a different folder, Setup's optional import page lets you
point to the old folder. Existing central settings are never overwritten and
separate AI datasets are not mixed.

Run Setup once for desktop/Start shortcuts and an Installed apps entry. Future
in-app updates replace only the EXE in the installation folder, retain settings,
and refresh the Installed apps version on launch. Keep the standalone EXE assets
in every release for compatibility with older updaters. If a future version needs
more installed files, update the release/updater design before distributing it.

## First-time GitHub setup

Create a GitHub account and install GitHub Desktop. Choose **File → New repository**,
name it `sc-cap-overlay`, and copy the contents of this package into its folder.
Commit, then **Publish repository** with **Keep this code private** unchecked.
Use the build/release steps above. The workflow automatically embeds your
repository name in the app; users do not enter a GitHub link.

## Future releases

Change `__version__` near the top of `sc_capacitor_ocr.py` to a new three-part
version, for example `1.2.3`. Commit, push, run the workflow, test the draft, and
publish. Do not reuse a published version tag.

## Local builds and troubleshooting

Local Windows builds require Python 3.12 and [Inno Setup 6](https://jrsoftware.org/isinfo.php).
Set `repository` in `update_config.json` to your `username/repository`, then run
`build.bat`. The build stops if the EXE or installer fails. Upload all four files
from `dist` if publishing manually. GitHub's Windows runner already includes
Inno Setup and embeds the repository automatically.

If import fails, the app explains the problem and leaves old files untouched;
restore access to the old folder and launch again. A failed download verification
never replaces the running EXE. Failed update diagnostics are in
`.sc-update-*/update-error.txt` beside the program; the previous EXE is retained
as `.exe.previous`. Choose a writable installation folder for in-app updates.

Local automated tests cover settings migration, non-overwrite behavior, retry
safety, reset behavior, hotkeys and updates. The new Windows installer workflow
must still complete in your repository; then manually review setup and shortcuts.

## Official documentation

- https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-an-existing-project-to-github-using-github-desktop
- https://docs.github.com/en/actions/managing-workflow-runs/manually-running-a-workflow
- https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository
- https://jrsoftware.org/ishelp/
