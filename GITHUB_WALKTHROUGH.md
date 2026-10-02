# Put SC Capacitor Overlay on GitHub and publish updates

This package is version **1.1.4**. The updater was introduced in **1.1.0**.
If you already published an earlier version, copy this package's contents into your existing
repository, commit and push the changes, then run Build Windows release. Test
and publish the new v1.1.4 draft. Your installed app can then update to it.
The first-time setup examples below describe the original 1.1.0 release.

A repository stores your source code. A commit saves a named snapshot on your PC.
A push uploads those commits to GitHub. A release distributes the finished EXE.
**Pushing source changes does not update installed copies: publish a new release.**

The supplied updater uses a **public GitHub repository**. Users do not need a
GitHub account or token to check or download releases. A private repository would
need a different authenticated distribution setup.

## 1. Create your account and install GitHub Desktop

1. Create/sign in to your account at https://github.com.
2. Install GitHub Desktop from https://desktop.github.com/download/.
3. Open GitHub Desktop and sign in to GitHub.com.
4. In Desktop, choose **File → New repository**.
5. Name it **sc-cap-overlay**. Use **Documents/GitHub** as the local parent path.
   Desktop creates the folder `Documents/GitHub/sc-cap-overlay` for you.
6. Leave the license unselected for now unless you have chosen how to license your
   code. A public repository makes the code visible; it does not automatically
   grant a broad open-source license.
7. Click **Create repository**.

## 2. Copy this app into that repository

1. Extract this ZIP outside your repository first.
2. Open the extracted inner `sc-cap-overlay` folder.
3. Copy its **contents**, including `.github`, `.gitignore`, `tests`, and the Python
   files, into `Documents/GitHub/sc-cap-overlay`.
4. Verify `sc_capacitor_ocr.py` sits directly inside the repository folder. Do not
   accidentally nest it as `sc-cap-overlay/sc-cap-overlay/sc_capacitor_ocr.py`.
5. GitHub Desktop should list the changed files. Enter **Initial Glass Cockpit release**
   in the Summary field and click **Commit to main** (or your current branch name).
6. Click **Publish repository**. Uncheck **Keep this code private** for this updater
   setup, then click **Publish repository**.

The included `.gitignore` excludes local calibration, AI training data, logs,
build environments and `dist`. Commit the source and assets; the workflow creates
the release EXE separately. Do not copy old `dist`/`build_env` folders into this package.

## 3. Let GitHub build your first Windows release

1. In Desktop choose **Repository → View on GitHub**.
2. Open the **Actions** tab on the website. Enable workflows if prompted.
3. Select **Build Windows release** in the left sidebar.
4. Click **Run workflow**, leave the default branch selected, then **Run workflow**.
5. Wait for the run to finish with a green check. Open it to see progress or errors.

The workflow installs Python on a Windows runner, runs updater tests, builds the
EXE and its SHA-256 checksum, and checks that the EXE stays open during startup.
It reads the version from `sc_capacitor_ocr.py`, embeds your repository name into
the EXE automatically, and creates a **draft** release with both assets attached.
No personal access token is needed; GitHub supplies the workflow's temporary token.

## 4. Review and publish the first release

1. Return to the repository's **Code** tab and open **Releases**.
2. Open/edit the new **v1.1.0** draft. Add a short description of the changes.
3. Confirm these two assets are attached:
   - `SC_Capacitor_Overlay.exe`
   - `SC_Capacitor_Overlay.exe.sha256`
4. Download the EXE from the draft and try opening it on your Windows PC. Check
   calibration, monitoring and the UPDATES button before distributing it.
5. Keep it as a normal release, not a prerelease. Select **Set as latest release**
   if that option is shown, then **Publish release**.
6. Share the release page with your users. They download **SC_Capacitor_Overlay.exe**,
   not GitHub's automatically generated “Source code” ZIP.

Your current v1.0.6 EXE does not contain an updater. Replace it manually with this
first v1.1.0 release once. Keep `config.json` and the `missile_ai` folder beside the
new EXE to retain calibration and learned samples. Future releases can update
through the app. Tesseract OCR is still a separate prerequisite.

## 5. Push a future update

For example, to publish version 1.1.1:

1. Edit the app files inside your repository folder. If I give you updated source,
   copy the changed files into this same folder; keep its `.git` directory intact.
2. Near the top of `sc_capacitor_ocr.py`, change:
   ```python
   __version__ = "1.1.0"
   ```
   to:
   ```python
   __version__ = "1.1.1"
   ```
3. Open GitHub Desktop and review the changed files.
4. Write a short Summary, such as **Fix capacitor reading**, and click **Commit to main**.
5. Click **Push origin**. This uploads the source changes.
6. On GitHub repeat **Actions → Build Windows release → Run workflow**.
7. Review/test the **v1.1.1** draft, add release notes and publish it as the latest
   normal release.

Use a new three-part version every time: 1.1.0, 1.1.1, 1.1.2, 1.2.0, and so on.
Do not reuse a published version number or replace a published release's assets.
A failed unpublished draft can be deleted before rerunning the same version.

## What users see

- A startup check runs in the background when the repository is configured.
- No automatic prompt interrupts active monitoring or calibration; it waits until
  those end. Offline/rate-limit errors do not stop the app.
- When a newer complete release exists, the app offers to download it.
- After verifying its SHA-256 checksum, it asks to close, install and restart.
- A separate helper waits for the running EXE to exit, replaces only that EXE,
  then launches the new version. Configuration and AI data are not replaced.
- The old EXE is retained beside it as `SC_Capacitor_Overlay.exe.previous`.
- The **UPDATES** button performs a manual check directly. GitHub builds embed
  the repository automatically; no repository prompt is shown. Existing startup
  preferences are retained in update_settings.json.

The checksum detects a corrupted/mismatched download; it is not a publisher's
code-signing certificate. Releases are trusted from the configured repository.
Source/Python installations check for new releases but use GitHub Desktop/pull
for installation. Automatic replacement is for the built Windows EXE.

## If you prefer to build releases on your own PC

Set `repository` in `update_config.json` to `YOUR_USERNAME/sc-cap-overlay`, then
run `build.bat`. It produces both required assets in `dist`. Commit/push the source,
create a GitHub release with the matching `v1.1.0` tag, attach both files, and
publish. You can skip the GitHub Actions build when following this manual path.

## Troubleshooting

- **No Run workflow button:** `.github/workflows/windows-release.yml` must be in
  the repository's default branch at the top level. Ensure it was committed/pushed.
- **Red workflow result:** open the failed step's log. No completed release should
  be published until the build and startup check pass.
- **No public release found:** verify the repository spelling and that the release
  is published, not a draft/private/prerelease.
- **Up to date when you expected an update:** increment `__version__`, push it,
  build the new draft, and publish it as latest.
- **Cannot write/update EXE:** keep the app in a folder you can write to, such as
  `Documents/SC Capacitor`, rather than Program Files. The updater does not request
  administrator access.
- **Install failed:** the old EXE or its `.previous` backup remains available.
  Diagnostics are in `.sc-update-*/update-error.txt` beside the app. A failed
  checksum never replaces the running EXE.

## Verification in this delivery

Updater unit tests cover version ordering, incomplete releases, URL restrictions,
checksum rejection, preserving configuration, and rollback on replacement errors.
The UI smoke checks passed with the added controls. The GitHub workflow and full
Windows EXE replacement cycle must still be exercised on your repository/PC.
For an end-to-end trial, install v1.1.0, publish a small v1.1.1 change, then use
UPDATES in v1.1.0 to test download, restart and retained calibration.

## Official GitHub guides

- https://docs.github.com/en/desktop/overview/creating-your-first-repository-using-github-desktop
- https://docs.github.com/en/desktop/making-changes-in-a-branch/pushing-changes-to-github-from-github-desktop
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
- https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository
