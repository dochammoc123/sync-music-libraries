"""
Configuration module for music library sync script.
Contains all paths, constants, and configuration settings.
"""
import os
import platform
import shutil
from pathlib import Path
from typing import Optional, Tuple

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
        return home / "icloud" # not used


ICLOUD = icloud_dir()
SCRIPTS_ROOT = ICLOUD / "scripts" / "sync-music-libraries"

# Per-OS config
if SYSTEM == "Windows":
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT = Path("//ROCK/Data/Storage/InternalStorage/Music")
    T8_ROOT = Path("//10.0.1.222/Share/EB5E-E9D3/Music")
    LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_windows.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_windows_summary.log"
    DETAIL_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_detail_windows.log"
    STRUCTURED_SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_summary_windows.log"

elif SYSTEM == "Darwin":
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT = "SMB:" / "ROCK" / "Data" / "Storage" / "InternalStorage" / "Music"
    T8_ROOT = "SMB:" / "10.0.1.222" / "Share" / "EB5E-E9D3" / "Music"
    LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_macos.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_macos_summary.log"
    DETAIL_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_detail_macos.log"
    STRUCTURED_SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_summary_macos.log"

else:
    DOWNLOADS_DIR = Path.home() / "Downloads" / "Music"
    MUSIC_ROOT = Path.home() / "Music" / "Library"
    T8_ROOT = None
    LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_other.log"
    SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_other_summary.log"
    DETAIL_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_detail_other.log"
    STRUCTURED_SUMMARY_LOG_FILE = SCRIPTS_ROOT / "Logs" / "library_sync_summary_other.log"

# ===================== CONFIG =====================

# Backup root for original FLACs before embedded-art changes
BACKUP_ROOT = MUSIC_ROOT.parent / "_EmbeddedArtOriginal"

# Update overlay root – where you drop patch files (cover.jpg, FLACs, etc.)
UPDATE_ROOT = MUSIC_ROOT.parent / "_UpdateOverlay"

# Audio file extensions considered "audio"
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".wma", ".m4v"}

# Lossless extension we want to KEEP when present
PREFERRED_EXT = ".flac"

# MusicBrainz identity (be polite)
MB_APP = "CaptChrisLibraryScript"
MB_VER = "1.0"
MB_CONTACT = "dochammoc@gmail.com"  # optional but recommended

# Delete empty directories under DOWNLOADS_DIR after moving files?
CLEAN_EMPTY_DOWNLOAD_FOLDERS = True

# Delete empty directories under BACKUP_ROOT after restoring files?
CLEAN_EMPTY_BACKUP_FOLDERS = True

LOG_MAX_BYTES = 1_000_000    # ~1 MB per log file
LOG_BACKUP_COUNT = 5         # keep up to 5 old logs

WEB_ART_LOOKUP_TIMEOUT = 4       # seconds per fetch attempt
WEB_ART_LOOKUP_RETRIES = 3       # number of attempts
ENABLE_WEB_ART_LOOKUP = True     # enable/disable web art lookups

# Files we clean up from download folders (exact filenames)
CLEANUP_FILENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

# Extensions we clean up from download folders (incomplete downloads, leftover images, archives, etc.)
CLEANUP_EXTENSIONS = {".partial", ".jpg", ".jpeg", ".png", ".gif", ".zip"}

# Archive formats we support for extraction (can be extended with 7z, rar, etc.)
ARCHIVE_EXTENSIONS = {".zip"}  # Add ".7z", ".rar", etc. as needed

# Minimum disk capacity required (1TB in bytes) - protects against targeting system drives
MIN_DISK_CAPACITY_BYTES = 1_000_000_000_000  # 1TB

# T8 sync comparison mode
# False (default): Fast mode - uses file size + mtime comparison (much faster)
# True: Accurate mode - uses MD5 checksums (slower but more reliable)
T8_SYNC_USE_CHECKSUMS = False

# T8 sync exclusions - don't copy these from ROON, don't delete them on T8
# T8 manages its own .thumbnails and similar cache/metadata
T8_SYNC_EXCLUDE_DIRS = {".thumbnails", ".cache", ".trash", ".Trash"}
T8_SYNC_EXCLUDE_FILES = {".database_uuid", ".DS_Store", "Thumbs.db"}

# ROON library refresh configuration
# ROON needs to rescan its library after files are added/modified.
# Set ENABLE_ROON_REFRESH to True to enable automatic ROON refresh after sync operations.
ENABLE_ROON_REFRESH = True

# ROON refresh method (always uses ROCK API for remote ROCK server)
# "rock_api" - Restart ROON software via ROCK server REST API (default)
# "none" - Disable ROON refresh (for testing or if using manual refresh)
ROON_REFRESH_METHOD = "rock_api"

# ROCK server configuration
ROCK_SERVER_IP = "10.0.1.221"  # IP address or hostname of your ROCK server (DHCP reserved)

# ROCK API endpoint configuration
# Based on ROCK web UI JavaScript: POST to /1/restartsoftware with empty JSON body
ROCK_API_ENDPOINT = "/1/restartsoftware"  # Relative path (will be appended to http://{ROCK_SERVER_IP})
ROCK_API_METHOD = "POST"  # "GET" or "POST"
ROCK_API_HEADERS = {"Content-Type": "application/json"}  # JSON content type for POST request
ROCK_API_DATA = {}  # Empty JSON object (as sent by web UI)
ROCK_API_COOKIES = None  # Optional: Cookies dict if authentication required


def get_disk_root_path(path: Path) -> Path:
    """
    Get the disk root path for a given path.
    For UNC paths (//SERVER/Share/Path), returns \\\\SERVER\\Share (Windows format).
    For local paths (C:\\Path), returns C:\\.
    """
    path_str = str(path)
    
    # Handle UNC paths (Windows network shares)
    if path_str.startswith("\\\\") or path_str.startswith("//"):
        # UNC path: //SERVER/Share/Path or \\SERVER\Share\Path -> \\SERVER\Share
        # Normalize to forward slashes first, then convert to Windows format
        normalized = path_str.replace("\\", "/").strip("/")
        parts = normalized.split("/")
        if len(parts) >= 2:
            # Return \\SERVER\Share (Windows UNC format)
            if SYSTEM == "Windows":
                return Path(f"\\\\{parts[0]}\\{parts[1]}")
            else:
                return Path(f"//{parts[0]}/{parts[1]}")
        # Fallback to original path if malformed
        return path
    
    # For local paths, get the root (drive letter on Windows, / on Unix)
    return Path(path.anchor)


def check_disk_capacity(path: Path, min_bytes: int = MIN_DISK_CAPACITY_BYTES) -> Tuple[bool, float, str]:
    """
    Check if the disk containing the given path has at least min_bytes total capacity.
    This protects against accidentally targeting system drives which are typically smaller.
    
    Args:
        path: The path to check disk capacity for
        min_bytes: Minimum required disk capacity in bytes (default: 1TB)
    
    Returns:
        Tuple of (has_enough_capacity: bool, capacity_gb: float, path_checked: str)
        Returns (False, 0.0, path_checked) if check fails or path is inaccessible
    """
    import time

    try:
        # Get the disk root path (handle UNC paths properly)
        disk_root = get_disk_root_path(path)
        disk_root_str = str(disk_root)
        
        # On Windows, ensure UNC paths use backslashes for shutil.disk_usage()
        if SYSTEM == "Windows" and disk_root_str.startswith("\\\\"):
            # Already in Windows format (\\SERVER\Share)
            check_path = disk_root
        elif SYSTEM == "Windows" and (disk_root_str.startswith("//") or disk_root_str.startswith("/")):
            # Convert forward slashes to backslashes for Windows UNC
            check_path_str = disk_root_str.replace("/", "\\")
            if check_path_str.startswith("//"):
                check_path_str = "\\" + check_path_str[1:]  # //SERVER/Share -> \SERVER\Share
            check_path = Path(check_path_str)
        else:
            # For local paths, check if path exists or use disk root
            check_path = path if path.exists() else disk_root
        
        # For network shares (UNC paths), try multiple times with retry logic
        # This helps when running from systray where network might not be immediately available
        is_unc_path = SYSTEM == "Windows" and str(check_path).startswith("\\\\")
        max_retries = 3 if is_unc_path else 1
        retry_delay = 0.5  # 0.5 seconds between retries
        
        # Try to find an accessible path - start with disk root, then try the original path
        usage = None
        paths_to_try = [check_path, path]
        last_error = None
        
        for attempt in range(max_retries):
            for test_path in paths_to_try:
                try:
                    # Check if path is accessible
                    test_str = str(test_path)
                    # For UNC paths, we can try even if exists() fails (network might not be ready)
                    if test_path.exists() or (SYSTEM == "Windows" and test_str.startswith("\\\\")):
                        usage = shutil.disk_usage(test_path)
                        if usage and usage.total > 0:
                            break
                        else:
                            # Got usage but it's 0 - might be a timing issue, retry
                            from structured_logging import logmsg
                            logmsg.verbose("Disk usage returned 0 for {path}, retrying...", path=str(test_path))
                            usage = None
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay)
                except (OSError, PermissionError) as e:
                    last_error = e
                    from structured_logging import logmsg
                    logmsg.verbose("Could not get disk usage for {path} (attempt {attempt}/{max}): {error}",
                        path=str(test_path), attempt=attempt + 1, max=max_retries, error=str(e))
                    if attempt < max_retries - 1 and is_unc_path:
                        time.sleep(retry_delay)
                    continue
            
            if usage and usage.total > 0:
                break
        
        if usage is None or usage.total == 0:
            # All paths failed or returned 0
            error_msg = f"Could not access any path to check disk capacity: {path}"
            if last_error:
                error_msg += f" (last error: {last_error})"
            if is_unc_path:
                from structured_logging import logmsg
                logmsg.warn("{msg} - Network share may not be accessible or may need authentication", msg=error_msg)
            raise OSError(error_msg)
        
        total_bytes = usage.total
        capacity_gb = total_bytes / (1024 ** 3)  # Convert to GB
        
        has_enough = total_bytes >= min_bytes
        
        return (has_enough, capacity_gb, disk_root_str)
    
    except Exception as e:
        # Log the error for debugging
        from structured_logging import logmsg
        logmsg.warn("Error checking disk capacity for {path}: {error}", path=str(path), error=str(e))
        # Path doesn't exist, not accessible, or other error
        return (False, 0.0, str(path))


