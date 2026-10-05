@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  Soundwave Setup
echo ============================================================
echo.

REM -- Execution policy (needed to activate venv on Windows) --
echo Setting PowerShell execution policy for current user...
powershell -NoProfile -Command "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force"
if %errorlevel% neq 0 (
    echo [WARN] Could not set execution policy. You may need to run as Administrator
    echo        or set it manually: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
)

REM -- Check Python --
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python not found.
    echo Download Python 3.10+ from https://python.org/downloads
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python found: %PYVER%

REM -- Create virtual environment --
if exist venv (
    echo.
    echo Virtual environment already exists. Skipping creation.
) else (
    echo.
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created.
)

REM -- Activate --
echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM -- Upgrade pip --
echo.
echo Upgrading pip...
python -m pip install --upgrade pip --quiet

REM -- Install dependencies --
echo.
echo Installing dependencies (this may take several minutes on first run)...
echo torch + torchaudio are large downloads (~1GB). Please be patient.
echo.
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Dependency installation failed.
    echo Try running manually: pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete!
echo ============================================================
echo.
echo To activate the environment in a new terminal:
echo   venv\Scripts\activate
echo.
echo Quick start:
echo   python main.py analyze "C:\path\to\music" --dry-run --no-lyrics
echo   python main.py analyze "C:\path\to\music" -o my_cues.xml
echo.
echo Run tests:
echo   pip install pytest pytest-cov
echo   pytest tests\ -v
echo.
pause
