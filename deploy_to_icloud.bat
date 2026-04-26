@echo off
REM Deploy refactored music library sync to iCloud scripts folder for testing
REM This copies the new modular code to a subfolder for testing

setlocal

set "SOURCE_DIR=%~dp0"
set "ICLOUD_SCRIPTS=C:\Users\docha\iCloudDrive\scripts"
set "DEPLOY_FOLDER=%ICLOUD_SCRIPTS%\sync-music-libraries"
set "FAILED=0"
goto :main

:main
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
call :_copy "%SOURCE_DIR%main.py" "%DEPLOY_FOLDER%\main.py"
call :_copy "%SOURCE_DIR%config.py" "%DEPLOY_FOLDER%\config.py"
call :_copy "%SOURCE_DIR%logging_utils.py" "%DEPLOY_FOLDER%\logging_utils.py"
call :_copy "%SOURCE_DIR%structured_logging.py" "%DEPLOY_FOLDER%\structured_logging.py"
call :_copy "%SOURCE_DIR%tag_operations.py" "%DEPLOY_FOLDER%\tag_operations.py"
call :_copy "%SOURCE_DIR%artwork.py" "%DEPLOY_FOLDER%\artwork.py"
call :_copy "%SOURCE_DIR%file_operations.py" "%DEPLOY_FOLDER%\file_operations.py"
call :_copy "%SOURCE_DIR%sync_operations.py" "%DEPLOY_FOLDER%\sync_operations.py"
call :_copy "%SOURCE_DIR%run_state.py" "%DEPLOY_FOLDER%\run_state.py"
call :_copy "%SOURCE_DIR%roon_refresh.py" "%DEPLOY_FOLDER%\roon_refresh.py"
call :_copy "%SOURCE_DIR%build_info.txt" "%DEPLOY_FOLDER%\build_info.txt"
call :_copy "%SOURCE_DIR%stamp_build_info.py" "%DEPLOY_FOLDER%\stamp_build_info.py"

REM Copy test script
if exist "%SOURCE_DIR%test_quick.py" (
    call :_copy "%SOURCE_DIR%test_quick.py" "%DEPLOY_FOLDER%\test_quick.py"
)

REM Copy existing run scripts
echo Copying run scripts...
if exist "%SOURCE_DIR%normal_run.bat" (
    call :_copy "%SOURCE_DIR%normal_run.bat" "%DEPLOY_FOLDER%\normal_run.bat"
)
if exist "%SOURCE_DIR%restore_originals.bat" (
    call :_copy "%SOURCE_DIR%restore_originals.bat" "%DEPLOY_FOLDER%\restore_originals.bat"
)
if exist "%SOURCE_DIR%safe_test_run.bat" (
    call :_copy "%SOURCE_DIR%safe_test_run.bat" "%DEPLOY_FOLDER%\safe_test_run.bat"
)
if exist "%SOURCE_DIR%embed_art.bat" (
    call :_copy "%SOURCE_DIR%embed_art.bat" "%DEPLOY_FOLDER%\embed_art.bat"
)

REM Copy tray launcher
if exist "%SOURCE_DIR%library_tray_launcher.py" (
    call :_copy "%SOURCE_DIR%library_tray_launcher.py" "%DEPLOY_FOLDER%\library_tray_launcher.py"
)

REM Copy icons directory
if exist "%SOURCE_DIR%icons" (
    echo Copying icons directory...
    call :_xcopy /E /I /Y "%SOURCE_DIR%icons" "%DEPLOY_FOLDER%\icons"
)

REM Copy optional git hooks + tooling
if exist "%SOURCE_DIR%githooks" (
    echo Copying githooks directory...
    call :_xcopy /E /I /Y "%SOURCE_DIR%githooks" "%DEPLOY_FOLDER%\githooks"
)
if exist "%SOURCE_DIR%tools" (
    echo Copying tools...
    if not exist "%DEPLOY_FOLDER%\tools" mkdir "%DEPLOY_FOLDER%\tools"
    call :_copy "%SOURCE_DIR%tools\bump_build_info.py" "%DEPLOY_FOLDER%\tools\bump_build_info.py"
)

REM Copy requirements
if exist "%SOURCE_DIR%requirements.txt" (
    call :_copy "%SOURCE_DIR%requirements.txt" "%DEPLOY_FOLDER%\requirements.txt"
)

python "%SOURCE_DIR%stamp_build_info.py" "%DEPLOY_FOLDER%"

echo.
echo ========================================
echo Deployment Complete!
echo ========================================
echo.
echo Files deployed to: %DEPLOY_FOLDER%
echo.
echo To test:
echo   1. Activate venv: 
echo      C:\Users\docha\local_python_envs\t8sync\.venv\Scripts\activate
echo   2. cd /d "%DEPLOY_FOLDER%"
echo   3. python test_quick.py
echo   4. python main.py --mode normal --dry
echo.

if "%FAILED%"=="1" (
    echo.
    echo Deployment finished with errors. Review output above.
    pause
) else (
    exit /b 0
)

REM Helpers to track failures but keep going
:_copy
copy /Y %*
if errorlevel 1 set "FAILED=1"
exit /b 0

:_xcopy
xcopy %*
REM XCOPY errorlevels: 0=ok, 1=files copied ok; treat >=2 as failure
if errorlevel 2 set "FAILED=1"
exit /b 0

