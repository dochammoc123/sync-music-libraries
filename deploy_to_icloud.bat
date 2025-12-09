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

REM Copy test script
if exist "%SOURCE_DIR%test_quick.py" (
    copy /Y "%SOURCE_DIR%test_quick.py" "%DEPLOY_FOLDER%\test_quick.py"
)

REM Copy requirements
if exist "%SOURCE_DIR%requirements.txt" (
    copy /Y "%SOURCE_DIR%requirements.txt" "%DEPLOY_FOLDER%\requirements.txt"
)

REM Create test run script (with venv activation)
echo Creating test run script...
(
echo @echo off
echo REM Test run script for refactored music library sync
echo REM Activates venv and runs dry-run test
echo C:\Users\docha\local_python_envs\t8sync\Scripts\activate
echo cd /d "%DEPLOY_FOLDER%"
echo python main.py --mode normal --dry
echo pause
) > "%DEPLOY_FOLDER%\test_run.bat"

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
echo Or run: %DEPLOY_FOLDER%\test_run.bat
echo   (This will activate venv automatically)
echo.
pause

