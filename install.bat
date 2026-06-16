@echo off
echo.
echo ====================================================
echo   MeetRecorder - installing dependencies
echo ====================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.9+ from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo [1/5] Upgrading pip...
python -m pip install --upgrade pip --quiet

echo [2/5] Installing sounddevice...
python -m pip install sounddevice --quiet

echo [3/5] Installing pyaudiowpatch...
python -m pip install pyaudiowpatch --quiet

echo [4/5] Installing numpy, mss, Pillow, pydub, openai-whisper...
python -m pip install numpy mss Pillow pydub openai-whisper --quiet

echo [5/5] Checking ffmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNING] ffmpeg not found in PATH.
    echo Download ffmpeg: https://www.gyan.dev/ffmpeg/builds/
    echo Extract and add the bin folder to PATH.
    echo See README.md for details.
    echo.
) else (
    echo [OK] ffmpeg found.
)

echo.
echo ====================================================
echo   Done! Run the app with:
echo   python recorder_app_final.py
echo   or double-click run.bat
echo ====================================================
echo.
pause
