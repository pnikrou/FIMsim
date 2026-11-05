# FIMsim launcher (Windows).  The Start Menu shortcut runs this, hidden.
#
#   launch.ps1               open FIMsim; the very first time, set it up first
#   launch.ps1 -Setup        (re)build the Python environment here
#   launch.ps1 -SmokeTest    set up if needed, then run the self-check
#
# Why a set-up step exists: HAND-FIM and ARC-Curve2Flood start Python, git and
# aws as separate programs, which cannot work from a single frozen executable.
# So FIMsim runs in a real Python environment, built from environment.yml and
# requirements-pip.txt - the same files the tested source install uses.
param([switch]$Setup, [switch]$SmokeTest)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$SetupScript = Join-Path $PSScriptRoot "setup.ps1"
$PS = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

if ($Setup) {
    & $SetupScript -Inline
    exit $LASTEXITCODE
}

if ($SmokeTest) {
    if (-not (Test-EnvReady)) {
        & $SetupScript -Inline
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }
    Set-FimsimEnv
    New-Item -ItemType Directory -Force $WorkDir | Out-Null
    Set-Location $WorkDir
    & $Mamba run -p $EnvDir python (Join-Path $PSScriptRoot "smoke_test.py")
    exit $LASTEXITCODE
}

if (Test-EnvReady) {
    Set-FimsimEnv
    New-Item -ItemType Directory -Force $WorkDir | Out-Null     # libraries keep small caches in the current folder
    $logOut = Join-Path $HomeDir "fimsim.log"
    $logErr = Join-Path $HomeDir "fimsim-errors.log"
    $argList = @("run", "-p", (ConvertTo-CmdArg $EnvDir), "python",
                 (ConvertTo-CmdArg (Join-Path $AppDir "main.py")))
    Start-Process -FilePath $Mamba -ArgumentList $argList -WorkingDirectory $WorkDir `
        -WindowStyle Hidden -RedirectStandardOutput $logOut -RedirectStandardError $logErr
    exit 0
}

# First start (or a release that changed the packages): set up in a visible
# window so the person can watch it work.
Start-Process -FilePath $PS -WorkingDirectory $PSScriptRoot -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (ConvertTo-CmdArg $SetupScript))
exit 0
