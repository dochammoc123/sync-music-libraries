"""
Logging utilities for music library sync script.
Handles summary generation and notifications.
All logging uses structured_logging.logmsg - the old logger API has been removed.
"""
import logging
import logging.handlers
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from config import STRUCTURED_SUMMARY_LOG_FILE, SYSTEM


def __getattr__(name: str):
    """Raise if anyone tries to use removed APIs."""
    if name == "logger":
        raise AttributeError(
            "The old 'logger' has been removed. Use structured_logging.logmsg instead."
        )
    if name == "print_summary_log_to_stdout":
        raise AttributeError(
            "print_summary_log_to_stdout has been removed. logmsg.write_summary() prints the summary."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    RotatingFileHandler that gracefully handles Windows file locking issues.
    If rotation fails due to file being locked, it continues logging without rotation.
    """
    def doRollover(self):
        """Override doRollover to handle Windows file locking gracefully."""
        try:
            super().doRollover()
        except (PermissionError, OSError):
            # On Windows, if the log file is locked (by another process or log viewer),
            # rotation will fail. Continue logging without rotation.
            # The log will grow beyond maxBytes, but the script won't crash.
            pass

# ANSI color codes for console output
class Colors:
    """ANSI color codes for terminal output."""
    # Reset
    RESET = '\033[0m'
    
    # Text colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Background colors
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_YELLOW = '\033[43m'
    BG_WHITE = '\033[47m'
    
    # Warning: black on yellow
    WARNING = BLACK + BG_YELLOW
    # Error: white on red
    ERROR = WHITE + BG_RED

# Icons for summary lines
ICONS = {
    'info': 'ℹ',
    'warning': '⚠',
    'error': '✗',
    'success': '✓',
    'step': '▶',
}

# Old API structures removed - structured logging handles this


def _enable_windows_ansi_colors() -> None:
    """Enable ANSI color support on Windows 10+."""
    if SYSTEM == "Windows":
        try:
            # Enable ANSI escape sequences in Windows console
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            # If it fails, colors just won't work - not critical
            pass


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to warnings and errors for console output.
    Uses log level for records from logmsg.warn()/error(); only uses [WARN]/[ERROR]
    prefix for summary lines sent via info() so phrases like 'no warnings' are not colored.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        # Level-based: actual warning/error logs always get color
        if record.levelno >= logging.ERROR:
            msg = f"{Colors.ERROR}{msg}{Colors.RESET}"
        elif record.levelno >= logging.WARNING:
            msg = f"{Colors.WARNING}{msg}{Colors.RESET}"
        else:
            # INFO/DEBUG: only color if message explicitly starts with [WARN] or [ERROR]
            msg_stripped = msg.lstrip()
            if msg_stripped.startswith("[ERROR]"):
                msg = f"{Colors.ERROR}{msg}{Colors.RESET}"
            elif msg_stripped.startswith("[WARN]"):
                msg = f"{Colors.WARNING}{msg}{Colors.RESET}"
        return msg


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output (no colors)."""
    pass


def album_label_from_tags(artist: str, album: str, year: str) -> str:
    """Create an album label from tags."""
    return f"{artist} - {album} ({year})" if year else f"{artist} - {album}"


def album_label_from_dir(album_dir: Path) -> str:
    """
    Build a label from the directory under MUSIC_ROOT, e.g.
    'Artist - Album (1995)'. Normalizes year format to match album_label_from_tags().
    Falls back to path if odd.
    """
    from config import MUSIC_ROOT
    import re
    
    try:
        rel = album_dir.relative_to(MUSIC_ROOT)
    except ValueError:
        return album_dir.as_posix()

    # Collapse CD1/CD2 etc to album folder
    parts = list(rel.parts)
    if parts and parts[-1].upper().startswith("CD") and len(parts) >= 2:
        parts = parts[:-1]

    if len(parts) >= 2:
        artist = parts[0]
        album_folder = parts[1]
        
        # Extract year from album folder if it's at the beginning: "(2012) Album Name"
        # Normalize to match album_label_from_tags() format: "Artist - Album (2012)"
        year_match = re.match(r'^\((\d{4})\)\s*(.+)$', album_folder)
        if year_match:
            year = year_match.group(1)
            album = year_match.group(2).strip()
            return f"{artist} - {album} ({year})"
        else:
            # No year prefix, use as-is
            return f"{artist} - {album_folder}"
    else:
        return rel.as_posix()


def notify_run_summary(mode: str) -> None:
    """
    Simple cross-platform notification at the end of a run,
    mentioning whether there were warnings.
    Now just logs to console - no blocking prompts.
    """
    from structured_logging import logmsg
    total_warnings = logmsg.count_warnings
    total_errors = logmsg.count_errors

    if total_errors > 0:
        message = f"Mode: {mode} — finished with {total_errors} error(s) and {total_warnings} warning(s)."
    elif total_warnings > 0:
        message = f"Mode: {mode} — finished with {total_warnings} warning(s)."
    else:
        message = f"Mode: {mode} — finished with no warnings."

    from structured_logging import logmsg
    logmsg.info("Run complete: {run_msg}", run_msg=message)

    # macOS Notification (non-blocking)
    if SYSTEM == "Darwin":
        try:
            subprocess.run([
                "osascript", "-e",
                f'display notification "{message}" with title "Library Sync Complete"'
            ], check=False)
        except Exception as e:
            # macOS notification is non-critical, don't log (would require logmsg import)
            pass

    # Windows: No blocking MessageBox - just log to console
    # The console and summary log viewer will remain open for user to review

    # Other OS: no-op beyond log line


def show_summary_log_in_viewer() -> None:
    """
    Open the summary log in a simple viewer:
      - macOS: TextEdit via 'open'
      - Windows: default associated app via os.startfile (usually Notepad)
    Safe no-op if the file doesn't exist.
    """
    try:
        if STRUCTURED_SUMMARY_LOG_FILE is None or not STRUCTURED_SUMMARY_LOG_FILE.exists():
            return

        if SYSTEM == "Darwin":
            # Open with default app (TextEdit by default)
            subprocess.run(
                ["open", str(STRUCTURED_SUMMARY_LOG_FILE)],
                check=False
            )
        elif SYSTEM == "Windows":
            os.startfile(str(STRUCTURED_SUMMARY_LOG_FILE))
        else:
            # On other OS's, summary log location already logged via logmsg if available
            pass
    except Exception as e:
        # File viewer errors are non-critical, don't log (would require logmsg import)
        pass

