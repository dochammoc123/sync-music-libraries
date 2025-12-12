@echo off
REM Test script for tray launcher
REM This helps verify the tray launcher is configured correctly

echo ========================================
echo Testing Tray Launcher Configuration
echo ========================================
echo.

REM Check venv exists
echo Checking virtual environment...
if exist "C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe" (
    echo   [OK] Venv Python found
) else (
    echo   [ERROR] Venv Python NOT found!
    echo   Expected: C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe
    pause
    exit /b 1
)

REM Check scripts directory
echo.
echo Checking scripts directory...
if exist "C:\Users\docha\iCloudDrive\scripts\library_tray_launcher.py" (
    echo   [OK] Tray launcher script found
) else (
    echo   [ERROR] Tray launcher script NOT found!
    echo   Expected: C:\Users\docha\iCloudDrive\scripts\library_tray_launcher.py
    pause
    exit /b 1
)

REM Check for main.py or library_sync_and_upgrade.py
echo.
echo Checking for sync script...
if exist "C:\Users\docha\iCloudDrive\scripts\main.py" (
    echo   [OK] main.py found (refactored version)
) else if exist "C:\Users\docha\iCloudDrive\scripts\library_sync_and_upgrade.py" (
    echo   [OK] library_sync_and_upgrade.py found (original version)
) else (
    echo   [WARN] Neither main.py nor library_sync_and_upgrade.py found!
    echo   Tray launcher will fail to find sync script.
)

REM Check for icons
echo.
echo Checking for icons...
if exist "C:\Users\docha\iCloudDrive\scripts\icons\pulse_32.png" (
    echo   [OK] Icons directory found
) else (
    echo   [WARN] Icons directory not found!
    echo   Tray icon may not display correctly.
)

REM Check dependencies
echo.
echo Checking Python dependencies...
C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe -c "import pystray; print('  [OK] pystray installed')" 2>nul || echo   [ERROR] pystray NOT installed! Run: pip install pystray pillow
C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\python.exe -c "import PIL; print('  [OK] Pillow installed')" 2>nul || echo   [ERROR] Pillow NOT installed! Run: pip install pystray pillow

echo.
echo ========================================
echo Configuration Check Complete
echo ========================================
echo.
echo If all checks passed, you can start the tray launcher with:
echo   start_tray_windows.bat
echo.
pause

