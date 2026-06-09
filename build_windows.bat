@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM build_windows.bat  —  Build FIMsim.exe folder on Windows
REM
REM Usage (from lisflood_prep_app\ directory, in Anaconda Prompt):
REM   conda activate lisflood_workflow
REM   pip install pyinstaller
REM   build_windows.bat
REM
REM After this, run Inno Setup compiler on installer_windows.iss
REM to produce a single FIMsim-setup.exe installer.
REM ─────────────────────────────────────────────────────────────────────────────

echo =^> Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo =^> Installing / upgrading PyInstaller...
pip install --quiet --upgrade pyinstaller

echo =^> Running PyInstaller...
pyinstaller build_app.spec --noconfirm

if errorlevel 1 (
    echo BUILD FAILED.
    pause
    exit /b 1
)

echo.
echo Done! App folder: dist\FIMsim\
echo.
echo Next step: open installer_windows.iss in Inno Setup Compiler
echo and click Build ^> Compile to produce FIMsim-setup.exe
pause
