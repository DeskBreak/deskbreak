@echo off
cd /d "%~dp0"
REM ============================================================
REM  DeskBreak build script
REM  Run this on Windows, from the project folder (double-click).
REM  Requires: Python 3.10+ and Inno Setup 6 (for the installer).
REM ============================================================

echo [0/4] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist DeskBreak.spec del /q DeskBreak.spec

echo [1/4] Installing dependencies (using this Python's pip)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 goto :error

echo [2/4] Building DeskBreak.exe (PyInstaller)...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --icon=assets\icon.ico ^
    --add-data "assets;assets" ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL._tkinter_finder ^
    --name DeskBreak ^
    main.py
if %errorlevel% neq 0 goto :error

echo [3/4] Building installer (Inno Setup)...
where iscc >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo WARNING: iscc.exe ^(Inno Setup Compiler^) was not found in PATH.
    echo Download and install Inno Setup: https://jrsoftware.org/isdl.php
    echo Then open installer.iss with a double-click and press Compile,
    echo or add the Inno Setup folder to PATH and run build.bat again.
    goto :end
)
iscc installer.iss
if %errorlevel% neq 0 goto :error

echo.
echo [4/4] DONE! The installer is located at: installer_output\DeskBreak-Setup.exe
goto :end

:error
echo.
echo Something went wrong during the build. See the message above.

:end
pause
