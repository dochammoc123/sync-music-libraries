@echo off
REM Start the music library sync tray launcher on Windows
REM This script activates the venv and starts the tray launcher

REM Activate virtual environment
C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\activate

REM Change to scripts directory
cd /d C:\Users\docha\iCloudDrive\scripts\sync-music-libraries

REM Start the tray launcher
python library_tray_launcher.py

REM If the script exits, pause so we can see any errors
pause

