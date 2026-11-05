# Shared settings for the Windows launcher and set-up script (dot-sourced by both).

$Root      = $PSScriptRoot                         # the install folder
$AppDir    = Join-Path $Root "app"
$Mamba     = Join-Path $Root "bin\micromamba.exe"

# Where FIMsim keeps its Python environment.  FIMSIM_HOME exists so the build
# checks can run in a scratch folder instead of the real one.
$HomeDir   = if ($env:FIMSIM_HOME) { $env:FIMSIM_HOME } else { Join-Path $env:LOCALAPPDATA "FIMsim" }
$EnvDir    = Join-Path $HomeDir "env"
$StampFile = Join-Path $HomeDir "env.stamp"
$WorkDir   = Join-Path $HomeDir "work"

# The environment is rebuilt when these two files change (a new release that
# needs different packages), and only then.
function Get-WantStamp {
    $bytes = [IO.File]::ReadAllBytes((Join-Path $AppDir "environment.yml")) +
             [IO.File]::ReadAllBytes((Join-Path $AppDir "requirements-pip.txt"))
    $sha = [Security.Cryptography.SHA256]::Create()
    return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLower()
}

function Test-EnvReady {
    if (-not (Test-Path (Join-Path $EnvDir "python.exe"))) { return $false }
    if (-not (Test-Path $StampFile)) { return $false }
    return ((Get-Content $StampFile -Raw).Trim() -eq (Get-WantStamp))
}

# Settings every FIMsim process, and every process FIMsim starts, should see.
function Set-FimsimEnv {
    $env:MAMBA_ROOT_PREFIX   = Join-Path $HomeDir "mamba"
    $env:PYTHONNOUSERSITE    = "1"                              # ignore user-site packages
    $env:PYTHONPYCACHEPREFIX = Join-Path $HomeDir "pycache"
    Remove-Item Env:PYTHONPATH, Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:FIMSIM_INSTALLED    = "1"
    $env:FIMSIM_ENV_DIR      = $EnvDir
}

# Quote one argument for a command line.
function ConvertTo-CmdArg([string]$Text) {
    if ($Text -match '[\s]') { return '"' + $Text + '"' }
    return $Text
}
