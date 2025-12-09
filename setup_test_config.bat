@echo off
REM Optional: Create test directories if they don't exist
REM This ensures all test paths are ready

setlocal

set "DOWNLOADS_MUSIC=C:\Users\docha\Downloads\Music"
set "TEST_MUSIC_ROOT=D:\TestMusicLibrary\ROON\Music"
set "TEST_T8_ROOT=D:\TestMusicLibrary\T8\Music"
set "BACKUP_ROOT=D:\TestMusicLibrary\ROON\_EmbeddedArtOriginal"
set "UPDATE_ROOT=D:\TestMusicLibrary\ROON\_UpdateOverlay"

echo ========================================
echo Setting Up Test Directories
echo ========================================
echo.

REM Create directories if they don't exist
if not exist "%DOWNLOADS_MUSIC%" (
    echo Creating %DOWNLOADS_MUSIC%...
    mkdir "%DOWNLOADS_MUSIC%"
)

if not exist "%TEST_MUSIC_ROOT%" (
    echo Creating %TEST_MUSIC_ROOT%...
    mkdir "%TEST_MUSIC_ROOT%"
)

if not exist "%TEST_T8_ROOT%" (
    echo Creating %TEST_T8_ROOT%...
    mkdir "%TEST_T8_ROOT%"
)

if not exist "%BACKUP_ROOT%" (
    echo Creating %BACKUP_ROOT%...
    mkdir "%BACKUP_ROOT%"
)

if not exist "%UPDATE_ROOT%" (
    echo Creating %UPDATE_ROOT%...
    mkdir "%UPDATE_ROOT%"
)

echo.
echo ========================================
echo Setup Complete!
echo ========================================
echo.
echo All test directories are ready.
echo.
echo Test workflow:
echo   1. Copy albums from E:\Plex Library\Music to %DOWNLOADS_MUSIC%
echo   2. Run: cd C:\Users\docha\iCloudDrive\scripts\music-sync-refactored
echo   3. Run: python main.py --mode normal --dry
echo   4. Review output, then run without --dry
echo.
pause

