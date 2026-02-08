"""
Logging utilities for music library sync script.
Handles logging setup, summary generation, and notifications.
"""
import logging
import os
import platform
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional

from config import LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, SUMMARY_LOG_FILE, SYSTEM, DETAIL_LOG_FILE

logger = logging.getLogger("library_sync")


class SafeRotatingFileHandler(RotatingFileHandler):
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
    """Custom formatter that adds colors to warnings and errors for console output."""
    
    def format(self, record: logging.LogRecord) -> str:
        # Get the base formatted message
        msg = super().format(record)
        
        # Check if message contains warning or error indicators
        msg_upper = msg.upper()
        if '[WARN]' in msg_upper or 'WARNING' in msg_upper:
            msg = f"{Colors.WARNING}{msg}{Colors.RESET}"
        elif '[ERROR]' in msg_upper or 'ERROR' in msg_upper or 'EXCEPTION' in msg_upper or 'FAILED' in msg_upper:
            msg = f"{Colors.ERROR}{msg}{Colors.RESET}"
        
        return msg


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output (no colors)."""
    pass


def setup_logging() -> None:
    """Configure old logging API: file handler only (no console)."""
    logger.setLevel(logging.INFO)
    
    # Plain formatter for file (no colors)
    file_fmt = PlainFormatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    # File handler without colors (writes to log file only, no console)
    if LOG_FILE is not None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = SafeRotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        fh.setFormatter(file_fmt)
        logger.addHandler(fh)


# log() function removed - use print() for console output or structured logging for detailed logs


# Old API functions removed - structured logging handles this


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


# write_summary_log() removed - new structured logging system handles this via logmsg.write_summary()


def notify_run_summary(mode: str) -> None:
    """
    Simple cross-platform notification at the end of a run,
    mentioning whether there were warnings.
    Now just logs to console - no blocking prompts.
    """
    from structured_logging import logmsg
    total_warnings = logmsg.count_warnings()
    total_errors = logmsg.count_errors()

    if total_errors > 0:
        message = f"Mode: {mode} — finished with {total_errors} error(s) and {total_warnings} warning(s)."
    elif total_warnings > 0:
        message = f"Mode: {mode} — finished with {total_warnings} warning(s)."
    else:
        message = f"Mode: {mode} — finished with no warnings."

    print(f"Run complete: {message}")

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
        if not SUMMARY_LOG_FILE.exists():
            return

        if SYSTEM == "Darwin":
            # Open with default app (TextEdit by default)
            subprocess.run(
                ["open", str(SUMMARY_LOG_FILE)],
                check=False
            )
        elif SYSTEM == "Windows":
            os.startfile(str(SUMMARY_LOG_FILE))
        else:
            # On other OS's, summary log location already logged via logmsg if available
            pass
    except Exception as e:
        # File viewer errors are non-critical, don't log (would require logmsg import)
        pass


def print_summary_log_to_stdout() -> None:
    """
    Print the summary log contents to stdout at the end of the run.
    Safe no-op if file doesn't exist.
    Adds icons and colors to the summary output.
    """
    try:
        if not SUMMARY_LOG_FILE.exists():
            return

        print("\n================ SUMMARY ================")
        with SUMMARY_LOG_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                line = line.rstrip('\n\r')
                if not line:
                    print()
                    continue
                
                # Detect format:
                # - Albums: start with "* "
                # - Headers: start with one or more tabs followed by "- "
                # - Warnings/Errors: start with "[WARN]" or "[ERROR]" (may have leading tabs)
                # - Section headers: contain ":" and no leading tabs (like "Albums processed:")
                
                line_stripped = line.lstrip(" \t")  # Remove leading whitespace for prefix detection
                
                if line_stripped.startswith("[ERROR]"):
                    # Error line - white on red
                    print(f"{Colors.ERROR}{ICONS['error']} {line}{Colors.RESET}")
                elif line_stripped.startswith("[WARN]"):
                    # Warning line - black on yellow
                    print(f"{Colors.WARNING}{ICONS['warning']} {line}{Colors.RESET}")
                elif line.startswith("* "):
                    # Album line - highlight differently (cyan/blue, or bold)
                    print(f"{Colors.CYAN}{ICONS['step']} {line}{Colors.RESET}")
                elif '\t' in line and line.lstrip('\t').startswith("- "):
                    # Header line (tab(s) + dash) - add > icon
                    print(f"{ICONS['step']} {line}")
                elif ':' in line and not line.startswith("  ") and not line.startswith("\t") and not line.startswith("*"):
                    # Section header (like "Albums processed:")
                    print(f"{ICONS['step']} {line}")
                elif line.startswith("  ") or line.startswith("\t"):
                    # Other indented lines (legacy format) - add info icon
                    print(f"{ICONS['info']} {line}")
                else:
                    # Regular line
                    print(line)
        print("=========================================\n")
    except Exception as e:
        # Summary log printing errors are non-critical, don't log (would require logmsg import)
        pass

