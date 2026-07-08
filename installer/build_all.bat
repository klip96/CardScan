@echo off
chcp 65001 >nul
REM ============================================================
REM  CardScan installer build - one click.
REM  Just double-click this file.
REM
REM  This .bat is intentionally ASCII-only: cmd.exe mis-parses
REM  non-ASCII .bat files and that is what produced the
REM  "'?' is not recognized as an internal or external command"
REM  errors. All localized (Russian) output lives in build_all.ps1,
REM  which PowerShell renders correctly.
REM
REM  build_all.ps1 will:
REM    - check and, if needed, INSTALL Python and Inno Setup
REM      (via winget, otherwise download the official installers);
REM    - build CardScan.exe (PyInstaller);
REM    - compile the installer (Inno Setup) -> installer\Output\
REM
REM  A UAC admin prompt may appear to install missing components.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_all.ps1"

if errorlevel 1 (
    echo.
    echo [WARNING] Could not start the PowerShell build script.
    echo Try: right-click build_all.bat -^> "Run as administrator".
    echo.
    pause
)
