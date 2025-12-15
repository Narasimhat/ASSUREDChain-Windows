# Quick launcher for shared Streamlit server on a LAN
# Usage (PowerShell):  ./scripts/start_shared_server.ps1

param(
    [int] $Port = 8503,
    [int] $PortTries = 10
)

$ErrorActionPreference = "Stop"

# Move to repo root (parent of the scripts directory)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $repoRoot

# Resolve a usable virtual environment activation script
$activationCandidates = @(
    (Join-Path $repoRoot ".venv\Scripts\Activate.ps1"),
    (Join-Path $env:LOCALAPPDATA "ASSUREDChain\.venv\Scripts\Activate.ps1")
)
$activationScript = $activationCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $activationScript) {
    Write-Error "No virtual environment found (.venv or %LOCALAPPDATA%\ASSUREDChain\.venv). Run scripts\setup_for_colleagues.bat or create a venv and install requirements."
}
$venvDir = Split-Path $activationScript -Parent
$venvPython = Join-Path $venvDir "python.exe"
if (!(Test-Path $venvPython)) {
    Write-Error "Virtual env is missing python.exe at $venvPython. Recreate the environment, then rerun start_app.bat."
}

function Get-FreePort {
    param([int] $StartPort, [int] $Attempts = 5)
    for ($p = $StartPort; $p -lt ($StartPort + $Attempts); $p++) {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $p)
        try {
            $listener.Start()
            $listener.Stop()
            return $p
        } catch {
            continue
        }
    }
    return $StartPort
}

# Activate venv
. $activationScript

# Optional: load env file for on-chain settings, etc.
if (Test-Path ".env") {
    Write-Host "Loading .env..."
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*#') { return }
        if ($_ -match '^\s*$') { return }
        $parts = $_ -split '=', 2
        if ($parts.Length -eq 2) {
            $name = $parts[0]
            $value = $parts[1]
            [Environment]::SetEnvironmentVariable($name, $value)
        }
    }
}

$address = "0.0.0.0"
$chosenPort = Get-FreePort -StartPort $Port -Attempts $PortTries
if ($chosenPort -ne $Port) {
    Write-Warning "Port $Port is in use; switching to $chosenPort."
}
Write-Host "Starting Streamlit on http://$($env:COMPUTERNAME):$chosenPort (bind $address). Press Ctrl+C to stop."
streamlit run app/Home.py --server.address $address --server.port $chosenPort
