"""
Configuration module for music library sync script.
Contains all paths, constants, and configuration settings.
"""
import os
import platform
from pathlib import Path
from typing import Optional

# ===================== ENVIRONMENT CONFIG =====================

SYSTEM = platform.system()  # "Windows", "Darwin", "Linux", etc.


def icloud_dir() -> Path:
    """
    Return the path to your iCloud root on each OS.
    Adjust these if your layout is different.
    """
    home = Path.home()
    if SYSTEM == "Windows":
        return home / "iCloudDrive"
    elif SYSTEM == "Darwin":
        return home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    else:
        return home / "scripts"


ICLOUD = icloud_dir()
SCRIPTS_ROOT = ICLOUD / "scripts"

# Per-OS config
if SYSTEM == "Windows":
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT = Path("D:/TestMusicLibrary/ROON/Music")
    T8_ROOT = Path("D:/TestMusicLibrary/T8/Music")
    LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_windows.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_windows_summary.log"

elif SYSTEM == "Darwin":
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT = ICLOUD / "TestMusicLibrary" / "ROON" / "Music"
    T8_ROOT = ICLOUD / "TestMusicLibrary" / "T8" / "Music"
    LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_macos.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_macos_summary.log"

else:
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT = Path.home() / "Music" / "Library"
    T8_ROOT = None
    LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_other.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_other_summary.log"

# ===================== CONFIG =====================

# Backup root for original FLACs before embedded-art changes
BACKUP_ROOT = MUSIC_ROOT.parent / "_EmbeddedArtOriginal"

# Update overlay root – where you drop patch files (cover.jpg, FLACs, etc.)
UPDATE_ROOT = MUSIC_ROOT.parent / "_UpdateOverlay"

# Audio file extensions considered "audio"
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".wma"}

# Lossless extension we want to KEEP when present
PREFERRED_EXT = ".flac"

# MusicBrainz identity (be polite)
MB_APP = "CaptChrisLibraryScript"
MB_VER = "1.0"
MB_CONTACT = "dochammoc@gmail.com"  # optional but recommended

# Delete empty directories under DOWNLOADS_DIR after moving files?
CLEAN_EMPTY_DOWNLOAD_FOLDERS = True

LOG_MAX_BYTES = 1_000_000    # ~1 MB per log file
LOG_BACKUP_COUNT = 5         # keep up to 5 old logs

WEB_ART_LOOKUP_TIMEOUT = 4       # seconds per fetch attempt
WEB_ART_LOOKUP_RETRIES = 3       # number of attempts
ENABLE_WEB_ART_LOOKUP = True     # enable/disable web art lookups

# Files we consider ignorable "junk" in download folders
JUNK_FILENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


