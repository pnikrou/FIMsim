# One-time set-up of FIMsim's Python environment (Windows).
#
#   setup.ps1            run in a console window; start FIMsim when done
#   setup.ps1 -Inline    plain run (used by the build checks and launch.ps1 -Setup)
#
# It does exactly the two install steps of the source install, with the same
# files:   1. environment.yml  (conda-forge packages)
#          2. python -m core.deps --fix  (requirements-pip.txt: HAND-FIM + ARC)
# plus git, which fimserve needs to fetch the OWP mapping scripts.
param([switch]$Inline)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "common.ps1")

$NeedGB = 12
New-Item -ItemType Directory -Force $HomeDir | Out-Null
$Log = Join-Path $HomeDir "setup.log"
"" | Set-Content $Log

function Say([string]$Text = "") {
    Write-Host $Text
    Add-Content -Path $Log -Value $Text
}

function Fail([string]$Text) {
    Say ""
    Say "============================================================"
    Say "  FIMsim could not be set up."
    Say "  $Text"
    Say "  Full log: $Log"
    Say "  Open FIMsim again to retry; it starts from a clean slate."
    Say "============================================================"
    if (-not $Inline) { Read-Host "`nPress Enter to close this window" | Out-Null }
    exit 1
}

# Run a program, show its output and append it to the log.  The program goes
# through a small .cmd file so that stderr is merged without PowerShell turning
# every warning line into an error record.
function Invoke-Logged([string]$Exe, [string[]]$Arguments) {
    $line = '"' + $Exe + '" ' + (($Arguments | ForEach-Object { ConvertTo-CmdArg $_ }) -join ' ')
    $bat = Join-Path $env:TEMP ("fimsim_step_{0}.cmd" -f $PID)
    Set-Content -Path $bat -Value ("@echo off`r`n" + $line + " 2>&1") -Encoding ASCII
    & cmd.exe /d /c $bat | Tee-Object -FilePath $Log -Append | Out-Host
    $rc = $LASTEXITCODE
    Remove-Item $bat -ErrorAction SilentlyContinue
    return $rc
}

# ── another set-up already running? ──────────────────────────────────────────
$mutex = New-Object Threading.Mutex($false, "Local\FIMsimSetup")
if (-not $mutex.WaitOne(0)) {
    Write-Host "FIMsim is already being set up in another window. Please wait for it to finish."
    if (-not $Inline) { Read-Host "Press Enter to close this window" | Out-Null }
    exit 0
}

Say ""
Say "============================================================"
Say "  FIMsim - one-time set-up"
Say "============================================================"
Say ""
Say "  FIMsim runs flood models that need a large set of scientific"
Say "  packages (GDAL, rasterio, ARC, OWP HAND-FIM, ...). They are"
Say "  downloaded and installed ONCE, now. Later starts are instant."
Say ""
Say "  - needs an internet connection"
Say "  - downloads roughly 2-3 GB and uses about 6 GB of disk"
Say "  - takes about 10-20 minutes"
Say "  Please keep this window open."
Say ""

# ── disk space ───────────────────────────────────────────────────────────────
try {
    $drive = (Split-Path $HomeDir -Qualifier).TrimEnd(':')
    $freeGB = [math]::Floor((Get-PSDrive -Name $drive).Free / 1GB)
    if ($freeGB -lt $NeedGB) {
        Fail "Not enough free disk space: about $NeedGB GB are needed, $freeGB GB are free on drive ${drive}:."
    }
} catch { }

if (-not (Test-Path $Mamba)) { Fail "The package installer is missing from the app: $Mamba" }
Set-FimsimEnv
if (Test-Path $EnvDir)    { Remove-Item -Recurse -Force $EnvDir }
if (Test-Path $StampFile) { Remove-Item -Force $StampFile }

Say "[1/3] Installing the scientific packages (conda-forge) ..."
$rc = Invoke-Logged $Mamba @("create", "-y", "-p", $EnvDir, "-f", (Join-Path $AppDir "environment.yml"), "git")
if ($rc -ne 0) { Fail "Step 1 failed (conda-forge packages). Check the internet connection and try again." }

Say ""
Say "[2/3] Installing the HAND-FIM and ARC-Curve2Flood backends ..."
Push-Location $AppDir
$rc = Invoke-Logged $Mamba @("run", "-p", $EnvDir, "python", "-m", "core.deps", "--fix")
Pop-Location
if ($rc -ne 0) { Fail "Step 2 failed (HAND-FIM / ARC backends). Check the internet connection and try again." }

Say ""
Say "[3/3] Checking the installation ..."
Push-Location $AppDir
$rc = Invoke-Logged $Mamba @("run", "-p", $EnvDir, "python", "-m", "core.deps")
Pop-Location
if ($rc -ne 0) { Fail "The final check found a problem (see above)." }

# The download cache is only needed during install - free the space.
& $Mamba clean --all -y *> $null
$pkgs = Join-Path $HomeDir "mamba\pkgs"
if (Test-Path $pkgs) { Remove-Item -Recurse -Force $pkgs -ErrorAction SilentlyContinue }

Set-Content -Path $StampFile -Value (Get-WantStamp) -NoNewline
$mutex.ReleaseMutex()

Say ""
Say "============================================================"
Say "  Done. FIMsim is ready."
Say "============================================================"

if (-not $Inline) {
    Say "  Starting FIMsim - you can close this window."
    Start-Sleep -Seconds 3
    $PS = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    Start-Process -FilePath $PS -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", (ConvertTo-CmdArg (Join-Path $PSScriptRoot "launch.ps1")))
}
exit 0
