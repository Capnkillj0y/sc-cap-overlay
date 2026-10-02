$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:RUNNER_TEMP 'SC Capacitor installed test'
$legacyDir = Join-Path $env:RUNNER_TEMP 'SC Capacitor legacy test'
$dataDir = Join-Path $env:LOCALAPPDATA 'SC Capacitor Overlay'
$regKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A737BD14-5A13-44C5-BBFA-6A37FA93C870}_is1'
# This script runs only on a fresh hosted CI runner, never on a user's PC.
if (Test-Path $dataDir) { throw 'Expected a clean CI profile before installer test.' }
New-Item -ItemType Directory -Path (Join-Path $legacyDir 'missile_ai') -Force | Out-Null
# Python accepts UTF-8 JSON without BOM; use explicit UTF8NoBOM for the fixture.
[IO.File]::WriteAllText((Join-Path $legacyDir 'config.json'), '{"low_threshold_value":25,"hotkeys_enabled":false,"missile_ai_enabled":false,"migration_probe":"preserved"}', [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $legacyDir 'update_settings.json'), '{"check_on_startup":false}', [Text.UTF8Encoding]::new($false))
'keep training files' | Set-Content (Join-Path $legacyDir 'missile_ai/probe.txt')
$setup = Start-Process -FilePath (Join-Path $PWD 'dist/SC-Capacitor-Setup.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$installDir`"",'/TASKS=desktopicon',"/LEGACYDIR=`"$legacyDir`"") -Wait -PassThru
if ($setup.ExitCode -ne 0) { throw "Installer failed: $($setup.ExitCode)" }
$exe = Join-Path $installDir 'SC_Capacitor_Overlay.exe'
if (!(Test-Path $exe) -or !(Test-Path $regKey)) { throw 'Installed files or Installed Apps entry missing.' }
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'SC Capacitor Overlay.lnk'
$startLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'SC Capacitor Overlay.lnk'
foreach ($link in @($desktopLink, $startLink)) {
  if (!(Test-Path $link)) { throw "Missing shortcut: $link" }
  $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
  if ($shortcut.TargetPath -ne $exe) { throw "Wrong shortcut target: $link" }
}
$app = Start-Process -FilePath $exe -WorkingDirectory $installDir -PassThru
try {
  if ($app.WaitForExit(12000)) { throw 'Installed app exited during startup.' }
  if (Test-Path (Join-Path $dataDir 'error_log.txt')) { throw (Get-Content (Join-Path $dataDir 'error_log.txt') -Raw) }
  if (Select-String -Path (Join-Path $dataDir 'app_log.txt') -Pattern 'Traceback' -Quiet) { throw 'Traceback in installed app log.' }
  $config = Get-Content (Join-Path $dataDir 'config.json') | ConvertFrom-Json
  if ($config.migration_probe -ne 'preserved') { throw 'Calibration import failed.' }
  if (!(Test-Path (Join-Path $dataDir 'missile_ai/probe.txt'))) { throw 'AI data import failed.' }
  if (!(Test-Path (Join-Path $legacyDir 'config.json'))) { throw 'Original settings were removed.' }
  if (Test-Path (Join-Path $installDir 'config.json')) { throw 'Settings leaked into program folder.' }
} finally {
  if (!$app.HasExited) {
    & taskkill.exe /PID $app.Id /T /F | Out-Null
  }
  # taskkill returning doesn't guarantee Windows has released the exe's file
  # handle yet -- this app has a few background threads (screen capture,
  # hotkey listener, AI collector) that can take a moment to unwind even
  # after a force-kill. Wait for the process to actually be gone, not just
  # for the kill request to have been issued, so the reinstall below isn't
  # racing a lock that's still being torn down.
  $deadline = (Get-Date).AddSeconds(15)
  while ((Get-Process -Id $app.Id -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
  }
  if (Get-Process -Id $app.Id -ErrorAction SilentlyContinue) {
    throw "Installed app process $($app.Id) did not fully exit within 15s; its exe may still be locked."
  }
}
# Reinstall must retain settings. Uninstall removes shortcuts/program but keeps data.
$logsBefore = @(Get-ChildItem (Join-Path $env:TEMP 'Setup Log*.txt') -ErrorAction SilentlyContinue)
$again = Start-Process -FilePath (Join-Path $PWD 'dist/SC-Capacitor-Setup.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$installDir`"") -Wait -PassThru
if ($again.ExitCode -ne 0) {
  # SetupLogging=yes means Inno already wrote the real reason to its own log
  # (file-in-use, disk space, etc.) -- surface it instead of just the exit
  # code, so a future failure here is self-diagnosing on the first try.
  $newLog = Get-ChildItem (Join-Path $env:TEMP 'Setup Log*.txt') -ErrorAction SilentlyContinue |
    Where-Object { $logsBefore.FullName -notcontains $_.FullName } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  $detail = if ($newLog) { "`n--- $($newLog.Name) ---`n" + (Get-Content $newLog.FullName -Raw) } else { ' (no Inno Setup log found)' }
  throw "Reinstall failed, exit code $($again.ExitCode).$detail"
}
$uninstall = Start-Process -FilePath (Join-Path $installDir 'unins000.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw 'Uninstall failed.' }
if ((Test-Path $exe) -or (Test-Path $desktopLink) -or (Test-Path $startLink) -or (Test-Path $regKey)) { throw 'Uninstall left program registration or shortcuts.' }
$config = Get-Content (Join-Path $dataDir 'config.json') | ConvertFrom-Json
if ($config.migration_probe -ne 'preserved' -or !(Test-Path (Join-Path $dataDir 'missile_ai/probe.txt'))) { throw 'Uninstall removed user data.' }
Write-Host 'PASS: install, shortcuts, launch, settings migration, reinstall and data-preserving uninstall.'
