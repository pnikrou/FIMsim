@echo off
REM =============================================================================
REM build_windows.bat  —  Build FIMsim.exe for Windows and package as a .zip
REM
REM Usage (from the lisflood_prep_app\ directory):
REM   build_windows.bat
REM
REM Requirements:
REM   - conda environment "lisflood_workflow" with all packages installed
REM   - Run from Anaconda Prompt (not plain cmd) so conda is available
REM
REM Output:
REM   dist\FIMsim\FIMsim.exe  <- the Windows executable
REM   dist\FIMsim-windows.zip <- zip this and share it
REM =============================================================================

setlocal enabledelayedexpansion

set CONDA_ENV=lisflood_workflow
set APP_NAME=FIMsim
set SCRIPT_DIR=%~dp0

echo.
echo ============================================================
echo   FIMsim -- Windows Build
echo ============================================================
echo.

REM -- Activate conda env -------------------------------------------------------
echo [1/5] Activating conda env: %CONDA_ENV%
call conda activate %CONDA_ENV%
if %errorlevel% neq 0 (
    echo ERROR: Could not activate conda env "%CONDA_ENV%".
    echo Make sure you are running from Anaconda Prompt.
    pause
    exit /b 1
)
echo.

REM -- Install / upgrade PyInstaller --------------------------------------------
echo [2/5] Installing/upgrading PyInstaller...
pip install --quiet --upgrade pyinstaller
if %errorlevel% neq 0 (
    echo ERROR: pip install pyinstaller failed.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('pyinstaller --version') do set PI_VER=%%v
echo   PyInstaller %PI_VER% ready
echo.

REM -- Clean previous build -----------------------------------------------------
echo [3/5] Cleaning previous build artifacts...
if exist "%SCRIPT_DIR%build"     rmdir /s /q "%SCRIPT_DIR%build"
if exist "%SCRIPT_DIR%dist"      rmdir /s /q "%SCRIPT_DIR%dist"
echo   Cleaned.
echo.

REM -- Run PyInstaller ----------------------------------------------------------
echo [4/5] Running PyInstaller (this takes 5-15 minutes on first build)...
cd /d "%SCRIPT_DIR%"
pyinstaller build_windows.spec --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller failed. Check the output above.
    pause
    exit /b 1
)
echo.

REM -- Verify output ------------------------------------------------------------
if not exist "%SCRIPT_DIR%dist\%APP_NAME%\%APP_NAME%.exe" (
    echo ERROR: dist\%APP_NAME%\%APP_NAME%.exe was not created.
    pause
    exit /b 1
)
echo   App folder verified: dist\%APP_NAME%\

REM -- Package as zip using PowerShell ------------------------------------------
echo.
echo [5/5] Creating zip for distribution...
set ZIP_SRC=%SCRIPT_DIR%dist\%APP_NAME%
set ZIP_DST=%SCRIPT_DIR%dist\%APP_NAME%-windows.zip

powershell -NoProfile -Command "Compress-Archive -Path '%ZIP_SRC%' -DestinationPath '%ZIP_DST%' -Force"
if %errorlevel% neq 0 (
    echo WARNING: Could not create zip automatically.
    echo Manually zip the folder: dist\%APP_NAME%\
) else (
    echo   Created: dist\%APP_NAME%-windows.zip
)

echo.
echo ============================================================
echo   BUILD COMPLETE
echo ============================================================
echo.
echo   File to share:  dist\%APP_NAME%-windows.zip
echo.
echo   HOW TO DISTRIBUTE:
echo   1. Upload dist\%APP_NAME%-windows.zip to Google Drive,
echo      Dropbox, or WeTransfer and share the link.
echo.
echo   HOW THE RECIPIENT OPENS IT:
echo   1. Download and unzip -- a FIMsim folder appears.
echo   2. Open the FIMsim folder.
echo   3. Double-click FIMsim.exe to launch the app.
echo   4. If Windows Defender SmartScreen warns "unknown publisher",
echo      click "More info" then "Run anyway".
echo.
pause
