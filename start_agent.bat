@echo off
title OpenPaw Agent
echo ============================================
echo   OpenPaw Agent - Starting...
echo ============================================
echo.

cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Install dependencies if needed
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

:: Start the agent
echo Starting OpenPaw agent...
echo.
python main.py

:: If we get here, the agent exited
echo.
echo Agent has stopped.
pause
