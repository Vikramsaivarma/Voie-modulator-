@echo off
REM ============================================================
REM  Voice Control - Windows Installer
REM  Checks Python, installs dependencies, verifies, and reports.
REM ============================================================
setlocal enabledelayedexpansion
title Voice Control - Installer
color 0A
chcp 65001 >nul

echo.
echo ==========================================================
echo         VOICE CONTROL - DEPENDENCY INSTALLER
echo ==========================================================
echo.

REM ------------------------------------------------------------
REM  STEP 1: Locate a usable Python interpreter
REM ------------------------------------------------------------
set "PY="
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if !errorlevel!==0 (
        set "PY=py"
    ) else (
        echo [ERROR] Python was not found on this system.
        echo.
        echo 1. Download Python 3.11 from: https://www.python.org/downloads/
        echo 2. Run the installer and TICK "Add Python to PATH".
        echo 3. Re-run this file.
        echo.
        pause
        exit /b 1
    )
)

echo [OK] Python launcher found: %PY%

REM ------------------------------------------------------------
REM  STEP 2: Verify the Python version is recent enough
REM ------------------------------------------------------------
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,8) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.8 or newer is required.
    echo Your version: 
    %PY% --version
    echo Please install Python 3.11 or later from python.org.
    pause
    exit /b 1
)
%PY% --version

REM ------------------------------------------------------------
REM  STEP 3: Upgrade pip, setuptools and wheel
REM ------------------------------------------------------------
echo.
echo [STEP 1/4] Upgrading pip, setuptools and wheel...
%PY% -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [WARNING] pip upgrade failed. Continuing anyway...
)

REM ------------------------------------------------------------
REM  STEP 4: Install the project dependencies
REM ------------------------------------------------------------
echo.
echo [STEP 2/4] Installing speech_recognition, pyttsx3, pyautogui, pyaudio...
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    echo.
    echo Suggestions:
    echo   * Run it manually:  %PY% -m pip install -r "%~dp0requirements.txt"
    echo   * If PyAudio fails, install a matching wheel from:
    echo     https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio  (Python 3.11)
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM  STEP 5: Verify that every module can be imported
REM ------------------------------------------------------------
echo.
echo [STEP 3/4] Verifying imports...
%PY% -c "import speech_recognition, pyttsx3, pyautogui, pyaudio, cv2, mediapipe; print('   All dependencies import OK.')" 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Verification failed - one or more packages cannot import.
    %PY% -c "import speech_recognition" 2>nul || echo   - speech_recognition FAILED
    %PY% -c "import pyttsx3"             2>nul || echo   - pyttsx3 FAILED
    %PY% -c "import pyautogui"          2>nul || echo   - pyautogui FAILED
    %PY% -c "import pyaudio"            2>nul || echo   - pyaudio FAILED
    %PY% -c "import cv2, mediapipe"     2>nul || echo   - cv2/mediapipe FAILED (hand cursor)
    pause
    exit /b 1
)
%PY% -c "import PIL; print('   Pillow (camera preview) OK.')" 2>nul || echo   (warn) Pillow missing - run:  pip install Pillow

REM ------------------------------------------------------------
REM  STEP 6: Success banner
REM ------------------------------------------------------------
echo.
echo [STEP 4/4] Done!
echo.
echo ==========================================================
echo   INSTALLATION COMPLETE - EVERYTHING WORKS!
echo ==========================================================
echo.
echo   To start the app:
echo       python voice_app.py
echo.
echo   (Or simply double-click voice_app.py)
echo.
pause
exit /b 0