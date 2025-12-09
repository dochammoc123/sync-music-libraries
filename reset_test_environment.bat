@echo off
REM Reset test environment for manual testing
REM This cleans up test directories so you can start fresh

setlocal

set "DOWNLOADS_MUSIC=C:\Users\docha\Downloads\Music"
set "TEST_MUSIC_ROOT=D:\TestMusicLibrary\ROON\Music"
set "TEST_T8_ROOT=D:\TestMusicLibrary\T8\Music"
set "BACKUP_ROOT=D:\TestMusicLibrary\ROON\_EmbeddedArtOriginal"
set "UPDATE_ROOT=D:\TestMusicLibrary\ROON\_UpdateOverlay"

echo ========================================
echo Reset Test Environment
echo ========================================
echo.
echo This will clean up test directories for fresh testing.
echo.
echo WARNING: This will delete:
echo   - All files in %DOWNLOADS_MUSIC%
echo   - All files in %TEST_MUSIC_ROOT%
echo   - All files in %TEST_T8_ROOT%
echo   - All files in %BACKUP_ROOT%
echo   - All files in %UPDATE_ROOT%
echo.
set /p CONFIRM="Are you sure? (yes/no): "
if /i not "%CONFIRM%"=="yes" (
    echo Cancelled.
    pause
    exit /b
)

echo.
echo Cleaning up...

REM Clean downloads (but keep the directory)
if exist "%DOWNLOADS_MUSIC%" (
    echo Cleaning Downloads\Music...
    del /Q /S "%DOWNLOADS_MUSIC%\*.*" 2>nul
    for /d %%d in ("%DOWNLOADS_MUSIC%\*") do rd /S /Q "%%d" 2>nul
)

REM Clean test music library
if exist "%TEST_MUSIC_ROOT%" (
    echo Cleaning ROON\Music...
    del /Q /S "%TEST_MUSIC_ROOT%\*.*" 2>nul
    for /d %%d in ("%TEST_MUSIC_ROOT%\*") do rd /S /Q "%%d" 2>nul
)

REM Clean T8 library
if exist "%TEST_T8_ROOT%" (
    echo Cleaning T8\Music...
    del /Q /S "%TEST_T8_ROOT%\*.*" 2>nul
    for /d %%d in ("%TEST_T8_ROOT%\*") do rd /S /Q "%%d" 2>nul
)

REM Clean backup directory
if exist "%BACKUP_ROOT%" (
    echo Cleaning backup directory...
    del /Q /S "%BACKUP_ROOT%\*.*" 2>nul
    for /d %%d in ("%BACKUP_ROOT%\*") do rd /S /Q "%%d" 2>nul
)

REM Clean update overlay
if exist "%UPDATE_ROOT%" (
    echo Cleaning update overlay...
    del /Q /S "%UPDATE_ROOT%\*.*" 2>nul
    for /d %%d in ("%UPDATE_ROOT%\*") do rd /S /Q "%%d" 2>nul
)

echo.
echo ========================================
echo Reset Complete!
echo ========================================
echo.
echo Test environment cleaned. Ready for fresh testing.
echo.
echo Next steps:
echo   1. Copy a few albums from E:\Plex Library\Music to %DOWNLOADS_MUSIC%
echo   2. Run the sync script to test
echo.
pause

