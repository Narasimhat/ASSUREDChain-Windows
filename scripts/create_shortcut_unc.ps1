param(
    [string]$ShortcutPath,
    [string]$TargetUNC = "\\mdc-berlin.net\fs\AG_Diecke\DATA MANAGMENT\Projects\Gene_Editing_Projects\ASSUREDChain",
    [int]$Port = 8503,
    [int]$PortTries = 10
)

$ErrorActionPreference = "Stop"

# Resolve default desktop path if none provided
if (-not $ShortcutPath) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not (Test-Path $desktop)) {
        $desktop = $env:TEMP
    }
    $ShortcutPath = Join-Path $desktop "ASSUREDChain.lnk"
}

# Ensure target exists
if (-not (Test-Path $TargetUNC)) {
    throw "Target folder not found: $TargetUNC"
}

# Ensure destination folder exists
$shortcutDir = Split-Path $ShortcutPath -Parent
if (-not (Test-Path $shortcutDir)) {
    New-Item -ItemType Directory -Path $shortcutDir -Force | Out-Null
}

$targetExe = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$args = "-NoExit -ExecutionPolicy Bypass -File `"$TargetUNC\scripts\start_shared_server.ps1`" -Port $Port -PortTries $PortTries"

$w = New-Object -ComObject WScript.Shell
$sc = $w.CreateShortcut($ShortcutPath)
$sc.TargetPath = $targetExe
$sc.Arguments = $args
$sc.WorkingDirectory = $TargetUNC
$sc.Save()

Write-Host "Shortcut created at: $ShortcutPath" -ForegroundColor Green
Write-Host "Launch target: $targetExe $args" -ForegroundColor Cyan
