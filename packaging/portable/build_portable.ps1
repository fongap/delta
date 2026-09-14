#requires -Version 5.1
<#
.SYNOPSIS
  Build the native Delta Windows Portable package, relocatable ZIP, and SHA-256.

.DESCRIPTION
  The portable contains the Tauri application with the Rust Runtime embedded in-process.
  Python is not an application dependency and no local server or companion process is shipped.

      Delta\
        Delta.exe
        App\Delta\Delta.exe
        Data\
        Other\

  The packaged application is exercised through `--runtime-self-test` before archiving. That
  headless path initializes the Rust authorities and executes a native capability through the
  Capability Host. The resulting ZIP is rejected if a retired server artifact reappears.
#>
[CmdletBinding()]
param(
    [string]$LauncherExe = "",
    [switch]$SkipAppBuild
)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Platform = Split-Path -Parent $Here
$RepoRoot = Split-Path -Parent $Platform
$Gui = Join-Path $RepoRoot "apps\desktop"
$TauriCmd = Join-Path $Gui "node_modules\.bin\tauri.cmd"
$TauriCfg = Join-Path $Gui "src-tauri\tauri.conf.json"
$Cfg = Get-Content -LiteralPath $TauriCfg -Raw | ConvertFrom-Json
$AppName = $Cfg.productName
$Version = $Cfg.version

function Require-Cmd([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$Name' was not found on PATH."
    }
}

function Assert-ChildPath([string]$Child, [string]$Parent) {
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    $childFull = [System.IO.Path]::GetFullPath($Child)
    $prefix = $parentFull + [System.IO.Path]::DirectorySeparatorChar
    if (-not $childFull.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem mutation outside ${parentFull}: $childFull"
    }
}

Require-Cmd cargo
Require-Cmd npm
Require-Cmd tar
if (-not (Test-Path -LiteralPath $TauriCmd -PathType Leaf)) {
    throw "Tauri CLI not found at $TauriCmd. Run npm install in $Gui first."
}

# ---- 1. Root launcher ---------------------------------------------------------
Write-Host "==> [1/5] root launcher (Delta.exe)" -ForegroundColor Cyan
if ($LauncherExe) {
    if (-not (Test-Path -LiteralPath $LauncherExe -PathType Leaf)) {
        throw "provided launcher executable not found: $LauncherExe"
    }
} else {
    $LauncherCrate = Join-Path $Here "launcher"
    $LauncherExe = Join-Path $LauncherCrate "target\release\delta-portable-launcher.exe"
    Push-Location $LauncherCrate
    try {
        & cargo build --release --locked
        if ($LASTEXITCODE -ne 0) { throw "portable launcher build failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
}

# ---- 2. Native Tauri application ---------------------------------------------
if (-not $SkipAppBuild) {
    Write-Host "==> [2/5] native Tauri application with embedded Rust Runtime" -ForegroundColor Cyan
    Push-Location $Gui
    try {
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $TauriCmd build --no-bundle
            $buildCode = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $savedPreference }
        if ($buildCode -ne 0) { throw "tauri build failed (exit $buildCode)" }
    }
    finally { Pop-Location }
} else {
    Write-Host "==> [2/5] app build skipped; reusing the existing release executable" -ForegroundColor DarkYellow
}

$CargoToml = Join-Path $Gui "src-tauri\Cargo.toml"
$CargoText = Get-Content -LiteralPath $CargoToml -Raw
$BinName = $AppName
if ($CargoText -match '\[\[bin\]\]\s*\r?\nname\s*=\s*"([^"]+)"') {
    $BinName = $Matches[1]
} elseif ($CargoText -match '(?m)^\[package\]\s*\r?\nname\s*=\s*"([^"]+)"') {
    $BinName = $Matches[1]
}
$AppExe = Join-Path $Gui "src-tauri\target\release\$BinName.exe"
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    throw "native app executable not found at $AppExe"
}

# ---- 3. Assemble and self-test ------------------------------------------------
Write-Host "==> [3/5] assemble portable tree and run native self-test" -ForegroundColor Cyan
$StageRoot = Join-Path $Here "build\portable-staging"
$Portable = Join-Path $StageRoot "DeltaPortable"
$AppDir = Join-Path $Portable "App\Delta"
Assert-ChildPath $StageRoot $Here
if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $Portable "Data") | Out-Null
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Copy-Item -LiteralPath $AppExe -Destination (Join-Path $AppDir "$AppName.exe") -Force
Copy-Item -LiteralPath $LauncherExe -Destination (Join-Path $Portable "$AppName.exe") -Force

$Other = Join-Path $Portable "Other"
$SourceDir = Join-Path $Other "Source"
$HelpDir = Join-Path $Other "Help"
$LicenseDir = Join-Path $Other "License"
$AppInfoDir = Join-Path $Other "AppInfo"
New-Item -ItemType Directory -Force -Path $SourceDir, $HelpDir, $LicenseDir, $AppInfoDir | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot "LICENSE") -Destination (Join-Path $LicenseDir "LICENSE.txt") -Force
Set-Content -LiteralPath (Join-Path $SourceDir "BUILD.txt") -Encoding utf8 -Value @"
This Delta Windows Portable was built from https://github.com/fongap/delta.
Build entry: packaging\portable\build_portable.ps1
Runtime: React -> Tauri IPC/events -> embedded Rust Runtime -> controlled capabilities.
"@
Set-Content -LiteralPath (Join-Path $HelpDir "PORTABLE.txt") -Encoding utf8 -Value @"
Delta Portable — 绿色便携版（免安装）

1. 解压完整的 Delta 文件夹到任意可写位置。
2. 双击 Delta.exe 启动。应用不写注册表，个人数据均在 Data\ 中。
3. 复制、移动或重命名整个文件夹后仍可运行。

便携版内置 Rust Runtime，不依赖 Python 应用服务。需要系统已安装 WebView2 Runtime。
"@

$AppInfoXml = Join-Path $AppInfoDir "appinfo.xml"
$XmlSettings = [System.Xml.XmlWriterSettings]::new()
$XmlSettings.Indent = $true
$AppInfoWriter = [System.Xml.XmlWriter]::Create($AppInfoXml, $XmlSettings)
try {
    $AppInfoWriter.WriteStartDocument()
    $AppInfoWriter.WriteStartElement("appinfo")
    $AppInfoWriter.WriteElementString("name", "$AppName Portable")
    $AppInfoWriter.WriteElementString("version", $Version)
    $AppInfoWriter.WriteElementString("launcher", "$AppName.exe")
    $AppInfoWriter.WriteStartElement("layout")
    $AppInfoWriter.WriteAttributeString("relocatable", "true")
    $AppInfoWriter.WriteAttributeString("runtime", "rust-embedded")
    $AppInfoWriter.WriteEndElement()
    $AppInfoWriter.WriteEndElement()
    $AppInfoWriter.WriteEndDocument()
}
finally { $AppInfoWriter.Dispose() }

$SmokeState = Join-Path $StageRoot "runtime-self-test-state"
New-Item -ItemType Directory -Force -Path $SmokeState | Out-Null
$OldStateDir = $env:DELTA_STATE_DIR
$env:DELTA_STATE_DIR = $SmokeState
try {
    & (Join-Path $AppDir "$AppName.exe") --runtime-self-test
    if ($LASTEXITCODE -ne 0) { throw "embedded Rust Runtime self-test failed (exit $LASTEXITCODE)" }
}
finally { $env:DELTA_STATE_DIR = $OldStateDir }

$ForbiddenNames = @("delta-server", "fastapi", "uvicorn", "server_entry", "packaging/server", "packaging\server", "sidecar")
$BadEntries = @(
    Get-ChildItem -LiteralPath $Portable -Recurse -Force |
        ForEach-Object { $_.FullName.Substring($Portable.Length).TrimStart('\', '/') } |
        Where-Object {
            $candidate = $_.ToLowerInvariant()
            @($ForbiddenNames | Where-Object { $candidate.Contains($_) }).Count -gt 0
        }
)
if ($BadEntries.Count) {
    throw "retired application-backend artifact found in portable tree: $($BadEntries -join ', ')"
}

# ---- 4. Relocatability gate ---------------------------------------------------
Write-Host "==> [4/5] relocatability scan" -ForegroundColor Cyan
$ScanScript = Join-Path $Here "scan_portable_paths.ps1"
& $ScanScript -Root $Portable
if ($LASTEXITCODE -ne 0) { throw "portable relocatability scan failed" }

# ---- 5. ZIP and checksum ------------------------------------------------------
Write-Host "==> [5/5] archive and SHA-256" -ForegroundColor Cyan
$ReleaseDir = Join-Path $RepoRoot "releases"
$ZipPath = Join-Path $ReleaseDir "$AppName-Windows-Portable.zip"
$HashPath = "$ZipPath.sha256"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Assert-ChildPath $ZipPath $ReleaseDir
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
if (Test-Path -LiteralPath $HashPath) { Remove-Item -LiteralPath $HashPath -Force }

$Zipped = Join-Path $StageRoot $AppName
Assert-ChildPath $Zipped $StageRoot
Move-Item -LiteralPath $Portable -Destination $Zipped
Push-Location $StageRoot
try {
    & tar -a -c -f $ZipPath Delta
    if ($LASTEXITCODE -ne 0) { throw "portable ZIP creation failed (exit $LASTEXITCODE)" }
}
finally { Pop-Location }

$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $HashPath -Encoding ascii -Value $Hash
Write-Host "Portable ZIP : $ZipPath" -ForegroundColor Green
Write-Host "SHA-256      : $Hash" -ForegroundColor Green
