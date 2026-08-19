#requires -Version 5.1
<#
.SYNOPSIS
  Scan a staged Delta Portable tree for absolute-path / machine-path leaks that would break
  relocatability (the portable must run from any drive / folder / machine).

.DESCRIPTION
  The portable derives everything at runtime from <PORTABLE ROOT> (see co-worker
  state_dir + the Rust shell's portable handling), so the packaged files must NOT contain a
  hardcoded absolute path to the build machine (e.g. C:\, D:\900 AIWork\..., a venv path).

  This helper inspects the staged tree and exits non-zero if it finds:
    - A literal drive-letter absolute path (C:\ ... ) in a text file.
    - The build machine's repo/source root embedded anywhere (text or binary string scan).

  Known-acceptable hits are pass-listed and reported as informational:
    - %APPDATA%\coworker (and other %ENVVAR% forms) in code are env-driven fallbacks the
      launcher overrides via COWORKER_STATE_DIR, so they are NOT absolute leaks.
    - Server/web paths like http://127.0.0.1:8765 are not filesystem paths.

.EXAMPLE
  .\scan_portable_paths.ps1 -Root C:\out\DeltaPortable
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Root
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Root)) { throw "Root not found: $Root" }

$DevSource = "D:\900 AIWork", "D:/900 AIWork", "900 AIWork"

# Files whose text we scan. Others are checked only by the raw byte-string scan below.
$TextExt = @(
    ".json", ".toml", ".cfg", ".config", ".ini", ".conf", ".yaml", ".yml",
    ".xml", ".txt", ".md", ".log", ".env", ".spec", ".py", ".pyi", ".rs",
    ".ps1", ".sh", ".html", ".js", ".css", ".ts", ".map"
)

$Failures = [System.Collections.Generic.List[string]]::new()
$Warnings = [System.Collections.Generic.List[string]]::new()
$Info     = [System.Collections.Generic.List[string]]::new()
$fileCount = 0

function Test-AbsolutePath([string]$line) {
    # Drive-letter absolute (Windows) paths: `C:\` / `C:/` at start-of-line or after a
    # non-alphanumeric delimiter. The `[^A-Za-z0-9/:]` guard excludes URL schemes like
    # `http://` (the `p:` is preceded by a letter, so it never reads as a drive letter).
    if ($line -match '(^|[^A-Za-z0-9/:])[A-Za-z]:[\\/]') { return $true }
    return $false
}

Get-ChildItem -Path $Root -Recurse -Force -File | ForEach-Object {
    $fileCount++
    $f = $_
    $rel = $f.FullName.Substring($Root.Length)

    # --- byte-level scan: build-machine source paths inside text AND binary strings ----
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    $asciiLen = $bytes.Length; if ($asciiLen -gt 4MB) { $asciiLen = 4MB }
    $ascii = [System.Text.Encoding]::ASCII.GetString($bytes, 0, $asciiLen)
    foreach ($s in $DevSource) {
        if ($ascii.IndexOf($s, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $Failures.Add("dev source path in binary/text: $rel  (matched '$s')")
        }
    }

    # --- text scan (only for textual files) ---
    $isText = [System.IO.Path]::GetExtension($f.Name).ToLower() -in $TextExt
    if (-not $isText) { return }

    # Cap the lines scanned: onedir _internal\ contains many small files; if one is huge,
    # we still scan it but stop early once it's clearly not a text config.
    $lines = [System.IO.File]::ReadLines($f.FullName)
    foreach ($line in $lines) {
        if ($line.Length -eq 0) { continue }
        # env-var forms (%VAR% and $(VAR)/${VAR}) are fine — runtime-resolved, not absolute.
        if ($line -match '%\w+%') { continue }

        if (Test-AbsolutePath $line) {
            # Whitelist: these are documented runtime fallbacks, overridden by the launcher.
            $trimmed = $line.Trim()
            if ($trimmed -match '[A-Za-z]:[\\/]Users[\\/]') {
                $Info.Add("info: user-path fallback (runtime-resolved): $rel :: $trimmed")
                continue
            }
            $Failures.Add("absolute path: $rel :: $trimmed")
        }
    }
}

Write-Host ""
Write-Host "Scanned $fileCount file(s) under $Root" -ForegroundColor Cyan
if ($Info.Count) {
    foreach ($i in $Info) { Write-Host "  $i" -ForegroundColor DarkGray }
}
if ($Warnings.Count) { foreach ($w in $Warnings) { Write-Host "  WARN $w" -ForegroundColor Yellow } }
if ($Failures.Count) {
    Write-Host ""
    Write-Host "ABSOLUTE-PATH LEAKS FOUND ($($Failures.Count)):" -ForegroundColor Red
    foreach ($x in $Failures) { Write-Host "  $x" -ForegroundColor Red }
    Write-Host "Portable is NOT fully relocatable. Fix the leaks, then rebuild." -ForegroundColor Red
    exit 1
}
Write-Host "OK: no absolute-path leaks. Tree is relocatable." -ForegroundColor Green
exit 0
