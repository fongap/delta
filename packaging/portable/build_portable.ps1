#requires -Version 5.1
<#
.SYNOPSIS
  Build the Delta Windows Portable ("DeltaPortable") package + relocatable ZIP + SHA-256.

.DESCRIPTION
  Produces an extract-and-run, fully relocatable Delta build (no installation, no registry
  writes, no %APPDATA% dependency). The result is a folder tree that can be copied / moved /
  renamed / carried to another drive or machine and keeps working:

      DeltaPortable\
        Delta.exe            <- root bootstrapper (resolves ROOT, launches GUI, then exits)
        App\
          Delta\Delta.exe    <- the real Tauri app (productName "Delta")
          Delta\sidecar\...  <- PyInstaller onedir delta-server (resources)
          (DefaultData\      <- optional first-run data seed; emitted only if one exists)
        Data\                <- created and seeded on first launch by Delta.exe (DELTA_STATE_DIR)
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
    7. Emit Delta-Windows-Portable.zip + .sha256 under <repo>\releases.

  Prerequisites:
    - Rust (rustup) with the x86_64-pc-windows-msvc target + MSVC C++ build tools (link.exe).
    - Node + npm (frontend build).
    - uv plus a locked Python environment at <repo>\.venv. Create it with the exact
      release dependency graph (including pyinstaller and Windows tzdata):
        uv sync --locked --extra bedrock --extra build
    - tar.exe (system bsdtar, present on Windows 10 1803+) for a long-path / Unicode-safe ZIP.

  WebView2 is NOT bundled (tauri.conf.json uses downloadBootstrapper for installers). A portable
  needs the WebView2 Evergreen runtime installed system-wide; this is a documented limitation,
  not a build error. The launcher redirects the WebView2 profile (best effort) so the browser
  profile moves with the portable whenever wry honors WEBVIEW2_USER_DATA_FOLDER.

  Secrets stay safe in portable mode: every store/pref/log/DB/secret key is derived from
  DELTA_STATE_DIR (see packages/secrets.py + src-tauri/src/lib.rs), which the launcher points
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

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path  # packaging\portable
$Platform  = Split-Path -Parent $Here                         # packaging
$Root      = Split-Path -Parent $Platform                     # <repo>
$Gui       = Join-Path $Root "apps\desktop"
$Venv      = Join-Path $Root ".venv"
$PyExe     = Join-Path $Venv "Scripts\python.exe"
$TauriCmd  = Join-Path $Gui "node_modules\.bin\tauri.cmd"

# Version + app-name come from tauri.conf.json (single source of truth).
$TauriCfg  = Join-Path $Gui "src-tauri\tauri.conf.json"
$Cfg       = Get-Content $TauriCfg -Raw | ConvertFrom-Json
$AppName   = $Cfg.productName                     # "Delta"
$Version   = $Cfg.version                          # e.g. 0.2.0

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
if (-not (Test-Path $TauriCmd)) {
    throw "Tauri CLI not found at $TauriCmd. Run npm install in $Gui first."
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
# PyInstaller logs its progress through Python's logging module, which writes to stderr.
# When native stderr is captured (pipe, CI, this script's host), PowerShell 5.1 rewrites each
# line as an ErrorRecord; with the global $ErrorActionPreference="Stop" that would terminate
# the build on PyInstaller's first INFO line. Scope the preference down for the call and gate
# purely on the process exit code instead.
$script:oldEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PyExe -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $Here "dist") --workpath (Join-Path $Here "build") `
        (Join-Path $Platform "server\delta-server.spec")
    $pyCode = $LASTEXITCODE
}
finally { $ErrorActionPreference = $script:oldEap }
if ($pyCode -ne 0) { throw "PyInstaller failed (exit $pyCode)" }

$BinDir = Join-Path $Gui "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$SideSrc = Join-Path $Here "dist\delta-server"
$SideDst = Join-Path $BinDir "sidecar"
if (Test-Path $SideDst) { Remove-Item -Recurse -Force $SideDst }
Copy-Item -Recurse -Force $SideSrc $SideDst
Write-Host "    sidecar -> $SideDst"

# ---- 2. Root launcher (Delta.exe) ---------------------------------------------
Write-Host "==> [2/6] root launcher (Delta.exe)" -ForegroundColor Cyan
if ($LauncherExe) {
    if (-not (Test-Path -LiteralPath $LauncherExe -PathType Leaf)) {
        throw "provided launcher executable not found: $LauncherExe"
    }
} else {
    # Cargo puts the artifact under the crate's own target dir
    # (<here>\launcher\target\release\...). There is no workspace Cargo.toml at
    # packaging\portable, so no shared target-dir.
    $LauncherExe = Join-Path $Here "launcher\target\release\delta-portable-launcher.exe"
    # Always rebuild the default launcher: build.rs owns the icon and PE version metadata,
    # so reusing an existing executable can silently ship the previous release's version.
    Write-Host "    building launcher from the current source/version"
    Push-Location (Join-Path $Here "launcher")
    try {
        & cargo build --release
        if ($LASTEXITCODE -ne 0) { throw "cargo build (launcher) failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $LauncherExe -PathType Leaf)) {
        throw "launcher build finished but no binary at $LauncherExe"
    }
}

# ---- 3. Tauri app build (frontend embedded, no installers) ---------------------
if (-not $SkipAppBuild) {
    Write-Host "==> [3/6] tauri build --no-bundle" -ForegroundColor Cyan
    Push-Location $Gui
    try {
        # Same stderr-as-ErrorRecord hazard as step 1: Tauri's informational lines
        # ("Info Looking up installed tauri packages…") go to stderr. When the host captures stderr,
        # PowerShell 5.1 wraps each line as an ErrorRecord; with the global
        # $ErrorActionPreference="Stop" that terminates the build on the first info line.
        # Call the checked-in CLI shim directly: npm 12 no longer forwards the historic
        # `npm run tauri build -- --no-bundle` argument shape reliably. Gate on exit code.
        $script:oldEap3 = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $TauriCmd build --no-bundle
            $npmCode = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $script:oldEap3 }
        if ($npmCode -ne 0) { throw "tauri build failed (exit $npmCode)" }
    }
    finally { Pop-Location }
} else {
    Write-Host "==> [3/6] tauri build skipped (-SkipAppBuild), reusing existing app exe" -ForegroundColor DarkYellow
}

# The Cargo [package] name may differ from tauri.conf.json productName (here: the Cargo
# package is "delta-desktop" while productName is "Delta"), and Tauri names the built
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
$StageRoot = Join-Path $Here "build\portable-staging"
$Portable  = Join-Path $StageRoot "DeltaPortable"
$AppDir   = Join-Path $Portable "App\Delta"
if (Test-Path $StageRoot) { Remove-Item -Recurse -Force $StageRoot }
if (Test-Path $Portable) { Remove-Item -Recurse -Force $Portable }
New-Item -ItemType Directory -Force -Path (Join-Path $Portable "Data")    | Out-Null
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
Copy-Item -Force (Join-Path $Root "LICENSE") (Join-Path $Other "License\LICENSE.txt")
Set-Content -Path (Join-Path $Other "Source\BUILD.txt") -Encoding utf8 -Value @"
This is a Delta Windows Portable built from the Delta source tree.

Source:  https://github.com/fongap/delta (see UPSTREAM.md / README.md in the repo)
Build :  packaging\portable\build_portable.ps1
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

# AppInfo moved into Other\ (no standalone AppInfo\ folder).
# Use System.Xml.XmlWriter (System.Xml.Linq is not loaded by default on PowerShell 5.1).
$AppInfoDir = Join-Path $Other "AppInfo"
New-Item -ItemType Directory -Force -Path $AppInfoDir | Out-Null
$AppInfoXml = Join-Path $AppInfoDir "appinfo.xml"
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
$ZipName = "$AppName-Windows-Portable.zip"
$ReleaseDir = Join-Path $Root "releases"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$ZipPath = Join-Path $ReleaseDir $ZipName
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
if (Test-Path "$ZipPath.sha256") { Remove-Item -Force "$ZipPath.sha256" }

# The ZIP must open with a single top-level "Delta/" folder (requirements: 解压即用、
# 可整体移动). tar.exe stores every path literally, so zipping the tree from inside
# staging would start the entries at "./". Move the assembled tree to the explicit
# packaging/build staging root as `Delta`, then zip that — entries begin `Delta/` while
# the repository-root releases directory receives only the final ZIP and checksum.
Write-Host "    staging top-level $AppName/ in the archive"
$Zipped = Join-Path $StageRoot $AppName
if (Test-Path $Zipped) { Remove-Item -Recurse -Force $Zipped }
Move-Item -Force $Portable $Zipped
Push-Location $StageRoot
try {
    & tar -a -c -f $ZipPath Delta
    if ($LASTEXITCODE -ne 0) { throw "tar zip failed (exit $LASTEXITCODE)" }
}
finally { Pop-Location }

$Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$ZipPath.sha256" -Encoding ascii -Value "$Hash"
Write-Host ""
Write-Host "Portable ZIP : $ZipPath" -ForegroundColor Green
Write-Host "SHA-256 file: $ZipPath.sha256" -ForegroundColor Green
Write-Host "SHA-256      : $Hash"
Get-ChildItem -Path $Zipped -Recurse -File |
    Measure-Object -Property Length -Sum |
    ForEach-Object { Write-Host ("Staged files   : {0} file(s), {1:N1} MB" -f $_.Count, ($_.Sum/1MB)) }
