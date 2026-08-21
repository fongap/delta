#requires -Version 5.1
<#
.SYNOPSIS
  Build the Delta Windows Portable ("DeltaPortable") package + relocatable ZIP + SHA-256.

.DESCRIPTION
  Produces an extract-and-run, fully relocatable Delta build (no installation, no registry
  writes, no %APPDATA% dependency). The result is a folder tree that can be copied / moved /
  renamed / carried to another drive or machine and keeps working:

      DeltaPortable\
        Delta.exe            <- root launcher (relay: resolves ROOT from its own location)
        App\
          Delta\Delta.exe    <- the real Tauri app (productName "Delta")
          Delta\sidecar\...  <- PyInstaller onedir delta-server (resources)
          (DefaultData\      <- optional first-run data seed; emitted only if one exists)
        Data\                <- created and seeded on first launch by Delta.exe (COWORKER_STATE_DIR)
        Other\
          Source\            <- pointers to the open-source repo + build config
          Help\              <- this portable's README
          License\           <- LICENSE text
        AppInfo\             <- appinfo.xml (portability metadata)

  Steps:
    1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
    2. Stage it at binaries\sidecar\ for Tauri's `resources` slot.
    3. Build (or reuse) the root launcher -> Delta.exe.
    4. `tauri build --no-bundle` -> the raw app Delta.exe + frontend (embedded).
    5. Assemble the relocatable tree above.
    6. Run the absolute-path leak scan (scan_portable_paths.ps1) -> fail the build on any leak.
    7. Emit Deltaportable-<version>-Windows.zip + .sha256.

  Prerequisites (same as build_windows.ps1 — see its header):
    - Rust (rustup) with the x86_64-pc-windows-msvc target + MSVC C++ build tools (link.exe).
    - Node + npm (frontend build).
    - A Python venv at platform\.venv with this package installed editable, plus pyinstaller
      and tzdata (Windows tz database); `typer` also needed at build time (see build_windows.ps1).
        py -m venv .venv ; .\.venv\Scripts\pip install -e ".[bedrock]" pyinstaller tzdata typer
    - tar.exe (system bsdtar, present on Windows 10 1803+) for a long-path / Unicode-safe ZIP.

  WebView2 is NOT bundled (tauri.conf.json uses downloadBootstrapper for installers). A portable
  needs the WebView2 Evergreen runtime installed system-wide; this is a documented limitation,
  not a build error. The launcher redirects the WebView2 profile (best effort) so the browser
  profile moves with the portable whenever wry honors WEBVIEW2_USER_DATA_FOLDER.

  Secrets stay safe in portable mode: every store/pref/log/DB/secret key is derived from
  COWORKER_STATE_DIR (see coworker/secrets.py + src-tauri/src/lib.rs), which the launcher points
  at <ROOT>\Data. Moving Data moves the secrets; the folder never writes to %APPDATA%.
#>
[CmdletBinding()]
param(
    # Optional root-launcher .exe to embed. Defaults to rebuilding/reusing
    # packaging\portable\target\release\delta-portable-launcher.exe.
    [string]$LauncherExe = "",
    # Skip the slow npm/tauri frontend rebuild and reuse the last app exe if present.
    [switch]$SkipAppBuild
)
$ErrorActionPreference = "Stop"

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Platform  = Split-Path -Parent $Here
$Gui       = Join-Path $Platform "surfaces\gui"
$Venv      = Join-Path $Platform ".venv"
$PyExe     = Join-Path $Venv "Scripts\python.exe"

# Version + app-name come from tauri.conf.json (single source of truth).
$TauriCfg  = Join-Path $Gui "src-tauri\tauri.conf.json"
$Cfg       = Get-Content $TauriCfg -Raw | ConvertFrom-Json
$AppName   = $Cfg.productName                     # "Delta"
$Version   = $Cfg.version                          # e.g. 0.1.7

function Require-Cmd($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$name' not found on PATH. See the prerequisites in this script's header."
    }
}
Require-Cmd rustc
Require-Cmd cargo
Require-Cmd npm
Require-Cmd tar
if (-not (Test-Path $PyExe)) {
    throw "Python interpreter not found at $PyExe. Create the venv and install deps (see header)."
}

$Triple = (& rustc -vV | Select-String '^host:').ToString().Split()[-1]

# A running delta-server.exe locks the output exe and makes PyInstaller's overwrite fail.
$running = Get-Process -Name "delta-server" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "==> stopping $($running.Count) running delta-server process(es) holding the output exe"
    $running | Stop-Process -Force
    Start-Sleep -Seconds 1
}

# ---- 1. PyInstaller onedir server sidecar -------------------------------------
Write-Host "==> [1/6] PyInstaller: bundling delta-server ($Triple)" -ForegroundColor Cyan
# Run via `python -m PyInstaller` — the console-script .exe launcher in the venv can fail
# silently (exit 1, no output) on some installs; the module invocation is the reliable path.
& $PyExe -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Here "dist") --workpath (Join-Path $Here "build") `
    (Join-Path $Here "delta-server.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

$BinDir = Join-Path $Gui "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$SideSrc = Join-Path $Here "dist\delta-server"
$SideDst = Join-Path $BinDir "sidecar"
if (Test-Path $SideDst) { Remove-Item -Recurse -Force $SideDst }
Copy-Item -Recurse -Force $SideSrc $SideDst
Write-Host "    sidecar -> $SideDst"

# ---- 2. Root launcher (Delta.exe) ---------------------------------------------
Write-Host "==> [2/6] root launcher (Delta.exe)" -ForegroundColor Cyan
if (-not $LauncherExe) {
    # Cargo puts the artifact under the crate's own target dir
    # (<here>\portable\launcher\target\release\...), NOT under <here>\portable\target —
    # there is no workspace Cargo.toml at packaging\portable, so no shared target-dir.
    $LauncherExe = Join-Path $Here "portable\launcher\target\release\delta-portable-launcher.exe"
}
if (-not (Test-Path $LauncherExe)) {
    Write-Host "    launcher binary missing -> building with cargo"
    Push-Location (Join-Path $Here "portable\launcher")
    try {
        & cargo build --release
        if ($LASTEXITCODE -ne 0) { throw "cargo build (launcher) failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
    if (-not (Test-Path $LauncherExe)) {
        throw "launcher build finished but no binary at $LauncherExe"
    }
}

# ---- 3. Tauri app build (frontend embedded, no installers) ---------------------
if (-not $SkipAppBuild) {
    Write-Host "==> [3/6] tauri build --no-bundle" -ForegroundColor Cyan
    Push-Location $Gui
    try {
        & npm run tauri build -- --no-bundle
        if ($LASTEXITCODE -ne 0) { throw "tauri build failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
} else {
    Write-Host "==> [3/6] tauri build skipped (-SkipAppBuild), reusing existing app exe" -ForegroundColor DarkYellow
}

# The Cargo [package] name may differ from tauri.conf.json productName (here: the Cargo
# package is "openworker-desktop" while productName is "Delta"), and Tauri names the built
# exe after the Cargo binary, not productName. Resolve the real binary name from Cargo.toml
# (explicit [[bin]] name wins, else [package] name), falling back to productName.
$CargoToml = Join-Path $Gui "src-tauri\Cargo.toml"
$BinName   = $AppName
if (Test-Path $CargoToml) {
    $cargoText = Get-Content $CargoToml -Raw
    if ($cargoText -match '\[\[bin\]\]\s*\r?\nname\s*=\s*"([^"]+)"') {
        $BinName = $Matches[1]
    } elseif ($cargoText -match '(?m)^\[package\]\s*\r?\nname\s*=\s*"([^"]+)"') {
        $BinName = $Matches[1]
    }
}
$AppExe = Join-Path $Gui "src-tauri\target\release\$BinName.exe"
if (-not (Test-Path $AppExe)) {
    throw "app exe not found at $AppExe — run without -SkipAppBuild (or check productName in tauri.conf.json)."
}

# ---- 4. Assemble relocatable tree ----------------------------------------------
Write-Host "==> [4/6] assembling portable tree ($AppName $Version)" -ForegroundColor Cyan
$Out      = Join-Path $Here "out"
$Portable = Join-Path $Out "DeltaPortable"
$AppDir   = Join-Path $Portable "App\Delta"
if (Test-Path $Portable) { Remove-Item -Recurse -Force $Portable }
New-Item -ItemType Directory -Force -Path (Join-Path $Portable "Data")    | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Portable "AppInfo") | Out-Null
New-Item -ItemType Directory -Force -Path $AppDir                          | Out-Null

# App\ : real app exe + sidecar onedir (landing next to the exe, matching server_bin()).
Copy-Item -Force $AppExe (Join-Path $AppDir "$AppName.exe")
Copy-Item -Recurse -Force $SideDst (Join-Path $AppDir "sidecar")

# Optional first-run data seed (launcher copies App\DefaultData -> Data only on first run).
$DefaultSeed = Join-Path $AppDir "DefaultData"
# (Nothing seeds DefaultData in this repo yet — the launcher creates Data\ empty on first run.)

# Root launcher.
Copy-Item -Force $LauncherExe (Join-Path $Portable "$AppName.exe")

# Other\ : source pointers + help + license.
$Other = Join-Path $Portable "Other"
New-Item -ItemType Directory -Force -Path (Join-Path $Other "Source") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Other "Help")   | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Other "License")| Out-Null
Copy-Item -Force (Join-Path $Platform "LICENSE") (Join-Path $Other "License\LICENSE.txt")
Set-Content -Path (Join-Path $Other "Source\BUILD.txt") -Encoding utf8 -Value @"
This is a Delta Windows Portable built from the Delta / OpenWorker source tree.

Source:  https://github.com/openworker (see UPSTREAM.md / README.md in the repo)
Build :  packaging\build_portable.ps1  (see packaging\build_windows.ps1 for toolchain prerequisites)
"@
Set-Content -Path (Join-Path $Other "Help\PORTABLE.txt") -Encoding utf8 -Value @"
Delta Portable — 绿色便携版（免安装）

使用方法
  1. 将整个 DeltaPortable 文件夹解压到任意可写位置（桌面、D 盘、U 盘均可）。
  2. 双击运行 Delta.exe 即启动，无需安装，不写注册表，不依赖 %APPDATA%。
  3. 复制 / 移动 / 重命名整个文件夹（甚至换盘、换电脑）后再启动，依然有效。

数据目录
  所有个人数据（配置、密钥、对话、日志、数据库）都保存在本文件夹的 Data\ 目录中，
  全部随文件夹移动。删除 Data\ 即可完全清除本便携版在所有机器上留下的数据。

注意
  - 不要放在 Program Files 等需要管理员权限的目录（便携版不会请求提权）。
  - 需要系统已安装 WebView2 运行时（Windows 10/11 通常自带；否则请到微软官网安装）。
  - 若文件夹被设为只读，程序会明确提示“便携版目录不可写”而不是静默失败。
"@

# AppInfo\appinfo.xml (PortableApps / relocatable conventions). Use System.Xml.XmlWriter
# (System.Xml.Linq is not loaded by default on PowerShell 5.1 / Windows PowerShell).
$AppInfoXml = Join-Path $Portable "AppInfo\appinfo.xml"
$AppInfoWriter = [System.Xml.XmlWriter]::Create($AppInfoXml)
try {
    $AppInfoWriter.WriteStartDocument()
    $AppInfoWriter.WriteStartElement("appinfo")
    $AppInfoWriter.WriteElementString("name", "$AppName Portable")
    $AppInfoWriter.WriteElementString("version", $Version)
    $AppInfoWriter.WriteElementString("launcher", "$AppName.exe")
    $AppInfoWriter.WriteStartElement("layout")
    $AppInfoWriter.WriteAttributeString("relocatable", "true")
    $AppInfoWriter.WriteAttributeString("description", "App/Data/Other convention; all data under Data\")
    $AppInfoWriter.WriteEndElement()
    $AppInfoWriter.WriteEndElement()
    $AppInfoWriter.WriteEndDocument()
}
finally { $AppInfoWriter.Dispose() }

# ---- 5. Absolute-path leak scan (gate) ------------------------------------------
Write-Host "==> [5/6] absolute-path leak scan" -ForegroundColor Cyan
$ScanScript = Join-Path $Here "scan_portable_paths.ps1"
if (-not (Test-Path $ScanScript)) {
    throw "scan helper not found at $ScanScript"
}
& $ScanScript -Root $Portable
if ($LASTEXITCODE -ne 0) {
    throw "Absolute-path leak scan FAILED — inspect the lines above; portable must be fully relocatable."
}

# ---- 6. ZIP + SHA-256 (tar.exe: long paths, UTF-8, Chinese filenames) ------------
Write-Host "==> [6/6] packaging ZIP + SHA-256" -ForegroundColor Cyan
$ZipName = "$AppName-$Version-Windows-Portable.zip"
$ZipPath = Join-Path $Out $ZipName
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
# tar.exe changes into $Portable and zips the tree. bsdtar stores UTF-8 names and handles
# paths beyond MAX_PATH that Compress-Archive chokes on. Entry root is the tree (no parent).
Push-Location $Portable
try {
    & tar -a -c -f $ZipPath .
    if ($LASTEXITCODE -ne 0) { throw "tar zip failed (exit $LASTEXITCODE)" }
}
finally { Pop-Location }

$Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$ZipPath.sha256" -Encoding ascii -Value "$Hash"
Write-Host ""
Write-Host "Portable ZIP : $ZipPath" -ForegroundColor Green
Write-Host "SHA-256      : $Hash"
Get-ChildItem -Path $Portable -Recurse -File |
    Measure-Object -Property Length -Sum |
    ForEach-Object { Write-Host ("Staged files   : {0} file(s), {1:N1} MB" -f $_.Count, ($_.Sum/1MB)) }
