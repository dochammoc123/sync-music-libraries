@echo off
REM Deploy refactored music library sync to iCloud scripts folder for testing
REM This copies the new modular code to a subfolder for testing

setlocal

set "SOURCE_DIR=%~dp0"
set "ICLOUD_SCRIPTS=C:\Users\docha\iCloudDrive\scripts"
set "DEPLOY_FOLDER=%ICLOUD_SCRIPTS%\sync-music-libraries"

echo ========================================
echo Deploying Refactored Music Library Sync
echo ========================================
echo.
echo Source: %SOURCE_DIR%
echo Target: %DEPLOY_FOLDER%
echo.

REM Create target directory
if not exist "%DEPLOY_FOLDER%" (
    echo Creating deployment folder...
    mkdir "%DEPLOY_FOLDER%"
)

REM Copy Python modules
echo Copying Python modules...
copy /Y "%SOURCE_DIR%main.py" "%DEPLOY_FOLDER%\main.py"
copy /Y "%SOURCE_DIR%config.py" "%DEPLOY_FOLDER%\config.py"
copy /Y "%SOURCE_DIR%logging_utils.py" "%DEPLOY_FOLDER%\logging_utils.py"
copy /Y "%SOURCE_DIR%tag_operations.py" "%DEPLOY_FOLDER%\tag_operations.py"
copy /Y "%SOURCE_DIR%artwork.py" "%DEPLOY_FOLDER%\artwork.py"
copy /Y "%SOURCE_DIR%file_operations.py" "%DEPLOY_FOLDER%\file_operations.py"
copy /Y "%SOURCE_DIR%sync_operations.py" "%DEPLOY_FOLDER%\sync_operations.py"
copy /Y "%SOURCE_DIR%roon_refresh.py" "%DEPLOY_FOLDER%\roon_refresh.py"

REM Copy test script
if exist "%SOURCE_DIR%test_quick.py" (
    copy /Y "%SOURCE_DIR%test_quick.py" "%DEPLOY_FOLDER%\test_quick.py"
)

REM Copy existing run scripts
echo Copying run scripts...
if exist "%SOURCE_DIR%normal_run.bat" (
    copy /Y "%SOURCE_DIR%normal_run.bat" "%DEPLOY_FOLDER%\normal_run.bat"
)
if exist "%SOURCE_DIR%restore_originals.bat" (
    copy /Y "%SOURCE_DIR%restore_originals.bat" "%DEPLOY_FOLDER%\restore_originals.bat"
)
if exist "%SOURCE_DIR%safe_test_run.bat" (
    copy /Y "%SOURCE_DIR%safe_test_run.bat" "%DEPLOY_FOLDER%\safe_test_run.bat"
)
if exist "%SOURCE_DIR%embed_art.bat" (
    copy /Y "%SOURCE_DIR%embed_art.bat" "%DEPLOY_FOLDER%\embed_art.bat"
)

REM Copy tray launcher
if exist "%SOURCE_DIR%library_tray_launcher.py" (
    copy /Y "%SOURCE_DIR%library_tray_launcher.py" "%DEPLOY_FOLDER%\library_tray_launcher.py"
)

REM Copy icons directory
if exist "%SOURCE_DIR%icons" (
    echo Copying icons directory...
    xcopy /E /I /Y "%SOURCE_DIR%icons" "%DEPLOY_FOLDER%\icons"
)

REM Copy requirements
if exist "%SOURCE_DIR%requirements.txt" (
    copy /Y "%SOURCE_DIR%requirements.txt" "%DEPLOY_FOLDER%\requirements.txt"
)

echo.
echo ========================================
echo Deployment Complete!
echo ========================================
echo.
echo Files deployed to: %DEPLOY_FOLDER%
echo.
echo To test:
echo   1. Activate venv: C:\Users\docha\local_python_envs\t8sync\Scripts\activate
echo   2. cd /d "%DEPLOY_FOLDER%"
echo   3. python test_quick.py
echo   4. python main.py --mode normal --dry
echo.
echo Or use your existing test scripts (safe_test_run.bat, etc.)
echo.
pause

