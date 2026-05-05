@echo off
REM Deploy refactored music library sync to iCloud scripts folder for testing
REM Subroutines live right after this goto so CMD always resolves call :label (do not place labels after exit).

setlocal

set "SOURCE_DIR=%~dp0"
set "ICLOUD_SCRIPTS=C:\Users\docha\iCloudDrive\scripts"
set "DEPLOY_FOLDER=%ICLOUD_SCRIPTS%\sync-music-libraries"
set "FAILED=0"
goto deploy_main

REM ---------- helpers (must be reachable for call :label) ----------
:deploy_copy_one
copy /Y %*
if errorlevel 1 set "FAILED=1"
exit /b 0

:deploy_xcopy_tree
xcopy %*
REM XCOPY errorlevels: 0=ok, 1=files copied ok; treat >=2 as failure
if errorlevel 2 set "FAILED=1"
exit /b 0

REM ---------- main ----------
:deploy_main
echo ========================================
echo Deploying Refactored Music Library Sync
echo ========================================
echo.
echo Source: %SOURCE_DIR%
echo Target: %DEPLOY_FOLDER%
echo.

REM Fail fast if this .bat was run from the wrong folder (e.g. only main.py on iCloud).
if not exist "%SOURCE_DIR%sync_operations.py" (
    echo.
    echo ERROR: sync_operations.py not found next to this script.
    echo Run deploy_to_icloud.bat from the full repository folder ^(where every *.py module lives^),
    echo not from an empty deploy target. Typical: C:\src\sync-music-libraries\deploy_to_icloud.bat
    echo.
    exit /b 1
)

REM Create target directory
if not exist "%DEPLOY_FOLDER%" (
    echo Creating deployment folder...
    mkdir "%DEPLOY_FOLDER%"
)

REM Copy Python modules
echo Copying Python modules...
call :deploy_copy_one "%SOURCE_DIR%main.py" "%DEPLOY_FOLDER%\main.py"
call :deploy_copy_one "%SOURCE_DIR%config.py" "%DEPLOY_FOLDER%\config.py"
call :deploy_copy_one "%SOURCE_DIR%logging_utils.py" "%DEPLOY_FOLDER%\logging_utils.py"
call :deploy_copy_one "%SOURCE_DIR%structured_logging.py" "%DEPLOY_FOLDER%\structured_logging.py"
call :deploy_copy_one "%SOURCE_DIR%tag_operations.py" "%DEPLOY_FOLDER%\tag_operations.py"
call :deploy_copy_one "%SOURCE_DIR%artwork.py" "%DEPLOY_FOLDER%\artwork.py"
call :deploy_copy_one "%SOURCE_DIR%file_operations.py" "%DEPLOY_FOLDER%\file_operations.py"
call :deploy_copy_one "%SOURCE_DIR%sync_operations.py" "%DEPLOY_FOLDER%\sync_operations.py"
call :deploy_copy_one "%SOURCE_DIR%run_state.py" "%DEPLOY_FOLDER%\run_state.py"
call :deploy_copy_one "%SOURCE_DIR%roon_refresh.py" "%DEPLOY_FOLDER%\roon_refresh.py"
call :deploy_copy_one "%SOURCE_DIR%build_info.txt" "%DEPLOY_FOLDER%\build_info.txt"
call :deploy_copy_one "%SOURCE_DIR%stamp_build_info.py" "%DEPLOY_FOLDER%\stamp_build_info.py"

REM Copy existing run scripts
echo Copying run scripts...
if exist "%SOURCE_DIR%normal_run.bat" (
    call :deploy_copy_one "%SOURCE_DIR%normal_run.bat" "%DEPLOY_FOLDER%\normal_run.bat"
)
if exist "%SOURCE_DIR%restore_originals.bat" (
    call :deploy_copy_one "%SOURCE_DIR%restore_originals.bat" "%DEPLOY_FOLDER%\restore_originals.bat"
)
if exist "%SOURCE_DIR%safe_test_run.bat" (
    call :deploy_copy_one "%SOURCE_DIR%safe_test_run.bat" "%DEPLOY_FOLDER%\safe_test_run.bat"
)
if exist "%SOURCE_DIR%embed_art.bat" (
    call :deploy_copy_one "%SOURCE_DIR%embed_art.bat" "%DEPLOY_FOLDER%\embed_art.bat"
)

REM Copy tray launcher (nested deploy folder)
if exist "%SOURCE_DIR%library_tray_launcher.py" (
    call :deploy_copy_one "%SOURCE_DIR%library_tray_launcher.py" "%DEPLOY_FOLDER%\library_tray_launcher.py"
)
REM Legacy entry points under iCloud scripts\ (Task Scheduler / docs often use these paths;
REM without this copy, restarting the tray runs stale code next to scripts\, not sync-music-libraries\).
if exist "%SOURCE_DIR%library_tray_launcher.py" (
    call :deploy_copy_one "%SOURCE_DIR%library_tray_launcher.py" "%ICLOUD_SCRIPTS%\library_tray_launcher.py"
)
if exist "%SOURCE_DIR%start_tray_windows.bat" (
    call :deploy_copy_one "%SOURCE_DIR%start_tray_windows.bat" "%ICLOUD_SCRIPTS%\start_tray_windows.bat"
)

REM Copy icons directory
if exist "%SOURCE_DIR%icons" (
    echo Copying icons directory...
    call :deploy_xcopy_tree /E /I /Y "%SOURCE_DIR%icons" "%DEPLOY_FOLDER%\icons"
)

REM Copy optional git hooks + tooling
if exist "%SOURCE_DIR%githooks" (
    echo Copying githooks directory...
    call :deploy_xcopy_tree /E /I /Y "%SOURCE_DIR%githooks" "%DEPLOY_FOLDER%\githooks"
)
if exist "%SOURCE_DIR%tools" (
    echo Copying tools...
    if not exist "%DEPLOY_FOLDER%\tools" mkdir "%DEPLOY_FOLDER%\tools"
    call :deploy_copy_one "%SOURCE_DIR%tools\bump_build_info.py" "%DEPLOY_FOLDER%\tools\bump_build_info.py"
)

REM Copy requirements
if exist "%SOURCE_DIR%requirements.txt" (
    call :deploy_copy_one "%SOURCE_DIR%requirements.txt" "%DEPLOY_FOLDER%\requirements.txt"
)

REM Documentation
echo Copying documentation...
if exist "%SOURCE_DIR%README.md" (
    call :deploy_copy_one "%SOURCE_DIR%README.md" "%DEPLOY_FOLDER%\README.md"
)
if exist "%SOURCE_DIR%USER_GUIDE.md" (
    call :deploy_copy_one "%SOURCE_DIR%USER_GUIDE.md" "%DEPLOY_FOLDER%\USER_GUIDE.md"
)
if exist "%SOURCE_DIR%SIDECAR_RULES.md" (
    call :deploy_copy_one "%SOURCE_DIR%SIDECAR_RULES.md" "%DEPLOY_FOLDER%\SIDECAR_RULES.md"
)

if exist "%SOURCE_DIR%test_log_paths.py" (
    call :deploy_copy_one "%SOURCE_DIR%test_log_paths.py" "%DEPLOY_FOLDER%\test_log_paths.py"
)
if exist "%SOURCE_DIR%test_log_paths.bat" (
    call :deploy_copy_one "%SOURCE_DIR%test_log_paths.bat" "%DEPLOY_FOLDER%\test_log_paths.bat"
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
echo   3. python main.py --mode normal --dry
echo.

if not "%FAILED%"=="1" goto deploy_ok
echo.
echo Deployment finished with errors. Review output above.
pause
exit /b 1

:deploy_ok
exit /b 0
